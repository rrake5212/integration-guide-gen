"""Research stage: fetch docs + discovery, select sections, chunk, extract, merge.

Precedence for endpoints: OIDC discovery > provider docs. A conflicting doc value is
kept as a note, never silently merged. Facts are only accepted when their quote is a
substring of the chunk they came from (``extract.extract_chunk``).
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field

from .errors import GuideError
from .extract import (ExtractionResult, extract_chunk, extract_scopes, looks_like_scope_table,
                      paths_for_availability, quote_in_text, sensitivity_for)
from .fetch import Document, JsonDocument, chunk_text, fetch_document, fetch_json, select_sections
from .registry import Provider
from .steps import extract_steps

DISCOVERY_ENDPOINTS = {
    "authorization_url": "authorization_endpoint",
    "token_url": "token_endpoint",
}


@dataclass
class ChunkStat:
    url: str
    index: int
    chars: int
    facts: int
    scopes: int
    ms: int
    model: str
    usage: dict | None
    scope_ms: int = 0
    scope_usage: dict | None = None


@dataclass
class ResearchResult:
    docs: list[Document]
    discovery: JsonDocument | None
    facts: dict[str, dict]
    scopes: list[dict]
    unknown_fields: list[str]
    conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    chunks: list[ChunkStat] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    steps_meta: dict | None = None


def _fact(value: str, quote: str, source_url: str) -> dict:
    return {"value": value, "quote": quote, "source_url": source_url}


def discovery_facts(discovery: JsonDocument) -> dict[str, dict]:
    facts: dict[str, dict] = {}
    for fact_path, key in DISCOVERY_ENDPOINTS.items():
        value = discovery.data.get(key)
        if isinstance(value, str) and value.startswith("https://") and quote_in_text(value, discovery.raw):
            facts[f"auth.oauth.{fact_path}"] = _fact(value, value, discovery.url)
    return facts


def discovery_scopes(discovery: JsonDocument) -> list[dict]:
    scopes: list[dict] = []
    for name in discovery.data.get("scopes_supported", []) or []:
        if not isinstance(name, str) or not name or not quote_in_text(name, discovery.raw):
            continue
        scopes.append({
            "name": name,
            "description": "Supported scope listed in the OIDC discovery document.",
            "quote": name,
            "source_url": discovery.url,
            "sensitivity": sensitivity_for(name, ""),
        })
        if len(scopes) >= 30:
            break
    return scopes


def _redirect_rule_fallback(docs: list[Document]) -> dict | None:
    best: tuple[int, str, str] | None = None
    for document in docs:
        for sentence in re.split(r"(?<=[.!?])\s+", document.text):
            text = " ".join(sentence.split())
            lowered = text.lower()
            if not ("redirect" in lowered or "callback" in lowered):
                continue
            if not any(word in lowered for word in ("must", "exactly", "match", "recommended", "wildcard", "localhost")):
                continue
            if not 40 <= len(text) <= 400:
                continue
            score = 0
            for word, weight in (("exactly", 3), ("must", 2), ("redirect", 2), ("callback", 2),
                                 ("https", 1), ("wildcard", 2), ("localhost", 2)):
                if word in lowered:
                    score += weight
            if best is None or score > best[0]:
                best = (score, text, document.url)
    if best is None:
        return None
    return {"value": best[1], "quote": best[1], "source_url": best[2]}


def _client_secret_fallback(docs: list[Document]) -> dict | None:
    for document in docs:
        for line in document.text.splitlines():
            text = " ".join(line.split())
            lowered = text.lower()
            if ("client_secret" not in lowered and "client secret" not in lowered) or "required" not in lowered:
                continue
            if "not required" in lowered or "optional" in lowered:
                continue
            if len(text) < 20:
                continue
            quote = text[:400]
            return {"value": "true", "quote": quote, "source_url": document.url}
    return None


def _document_chunks(provider: Provider) -> tuple[list[Document], list[tuple[str, str]], list[str], list[GuideError]]:
    docs: list[Document] = []
    chunks: list[tuple[str, str]] = []
    notes: list[str] = []
    errors: list[GuideError] = []
    for url in provider.docs:
        try:
            document = fetch_document(url)
        except GuideError as exc:
            notes.append(f"docs fetch failed: {url} ({exc.code})")
            errors.append(exc)
            continue
        docs.append(document)
        sections = select_sections(document.text)
        joined = "\n\n".join(f"{'#' * section.level} {section.heading}\n{section.body}" for section in sections)
        if not joined.strip():
            notes.append(f"no auth sections found in {url}")
            continue
        for index, chunk in enumerate(chunk_text(joined, max_chars=6000, overlap=400)):
            chunks.append((document.url, chunk))
            notes.append(f"chunk {index} from {url}: {len(chunk)} chars")
    return docs, chunks, notes, errors


def research(provider: Provider, client, max_chunks: int = 8, on_progress=None) -> ResearchResult:
    docs, chunks, notes, errors = _document_chunks(provider)
    if not chunks:
        if errors:
            raise errors[0]
        raise GuideError("DOCS_UNPARSEABLE", f"no usable auth sections for {provider.id}")

    discovery: JsonDocument | None = None
    if provider.discovery:
        try:
            discovery = fetch_json(provider.discovery)
        except GuideError as exc:
            notes.append(f"discovery fetch failed: {exc.code}")

    paths = paths_for_availability(provider.availability)
    facts: dict[str, dict] = {}
    scopes: dict[str, dict] = {}
    conflicts: list[str] = []
    unknown: set[str] = set()
    stats: list[ChunkStat] = []

    for index, (url, chunk) in enumerate(chunks[:max_chunks]):
        if on_progress:
            on_progress(index, min(len(chunks), max_chunks))
        result: ExtractionResult = extract_chunk(client, provider.name, chunk, url, paths)
        scope_result = None
        if looks_like_scope_table(chunk):
            scope_result = extract_scopes(client, provider.name, chunk, url)
        for path, fact in result.facts.items():
            if path in facts and facts[path]["value"] != fact["value"]:
                conflicts.append(f"{path}: {facts[path]['value']!r} vs {fact['value']!r} (kept first)")
                continue
            facts.setdefault(path, fact)
        for scope in (scope_result.scopes if scope_result else result.scopes):
            scopes.setdefault(scope["name"].lower(), scope)
        unknown |= set(result.unknown_fields)
        stats.append(ChunkStat(
            url=url, index=index, chars=len(chunk), facts=len(result.facts),
            scopes=len(scope_result.scopes if scope_result else result.scopes),
            ms=result.ms, model=result.model, usage=result.usage,
            scope_ms=scope_result.ms if scope_result else 0,
            scope_usage=scope_result.usage if scope_result else None,
        ))

    if len(chunks) > max_chunks:
        notes.append(f"{len(chunks) - max_chunks} chunk(s) skipped by max_chunks={max_chunks}")

    if discovery is not None:
        for path, fact in discovery_facts(discovery).items():
            if path in facts and facts[path]["value"] != fact["value"]:
                conflicts.append(f"{path}: docs said {facts[path]['value']!r}; discovery said {fact['value']!r} (discovery used)")
            facts[path] = fact
        for scope in discovery_scopes(discovery):
            scopes.setdefault(scope["name"].lower(), scope)

    unknown |= set(paths) - set(facts)
    if "auth.oauth.authorization_url" in facts and "auth.oauth.redirect_uri_rules" not in facts:
        fallback = _redirect_rule_fallback(docs)
        if fallback:
            facts["auth.oauth.redirect_uri_rules"] = fallback
            notes.append("redirect URI rules: deterministic sentence fallback")
    if "auth.oauth.authorization_url" in facts and "auth.oauth.client_secret_required" not in facts:
        fallback = _client_secret_fallback(docs)
        if fallback:
            facts["auth.oauth.client_secret_required"] = fallback
            notes.append("client secret requirement: deterministic table fallback")
    step_result = extract_steps(client, provider.name, docs)
    notes.append(f"setup steps: {len(step_result.steps)}")
    return ResearchResult(
        docs=docs,
        discovery=discovery,
        facts=facts,
        scopes=list(scopes.values()),
        unknown_fields=sorted(unknown),
        conflicts=conflicts,
        notes=notes,
        chunks=stats,
        steps=step_result.steps,
        steps_meta={"model": step_result.model, "ms": step_result.ms,
                    "total_tokens": (step_result.usage or {}).get("total_tokens"),
                    "estimated_cost": (step_result.usage or {}).get("estimated_cost")},
    )
