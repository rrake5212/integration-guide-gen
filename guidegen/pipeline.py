"""Shared pipeline: resolve -> research -> assemble, with progress callbacks.

The CLI build command and the web worker both use this so they cannot drift. Rendering is a
separate, deterministic step (`render_bundle`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .assemble import assemble
from .env import load_env
from .errors import GuideError
from .llm import LLMClient
from .registry import Registry
from .render_agent import render_agent_json, render_agent_md
from .render_human import render_human
from .research import ResearchResult, research
from .resolve import Resolution, Resolver


@dataclass
class BuildResult:
    job_id: str
    resolution: Resolution
    research: ResearchResult
    spec: dict


def forced_resolution(registry: Registry, raw: str, pick: str) -> Resolution:
    if pick in registry.providers:
        provider = registry.provider(pick)
    else:
        ids = registry.by_alias.get(pick.strip().lower(), set())
        if len(ids) != 1:
            raise GuideError("AMBIGUOUS_PROVIDER", f"unknown or ambiguous pick: {pick!r}")
        provider = registry.provider(next(iter(ids)))
    return Resolution(
        raw=raw,
        normalized=pick,
        kind="text",
        decision="resolved",
        provider=provider,
        matched_by="pick",
        confidence="high",
        availability=provider.availability,
        notes=["forced by pick"],
    )


def build_spec(raw: str, *, pick: str | None = None, max_chunks: int = 8,
               on_progress=None, job_id: str | None = None) -> BuildResult:
    load_env()
    job_id = job_id or "job_" + uuid.uuid4().hex[:10]
    resolver = Resolver()
    resolution = forced_resolution(resolver.registry, raw, pick) if pick else resolver.resolve(raw)
    if resolution.decision == "ask":
        raise GuideError("AMBIGUOUS_PROVIDER", detail=f"{len(resolution.alternatives)} candidates")
    if resolution.decision != "resolved" or resolution.provider is None:
        raise GuideError("PROVIDER_UNKNOWN", detail=f"no provider for {raw!r}")
    provider = resolution.provider
    if provider.availability in ("unknown", "no_public_api"):
        raise GuideError("PROVIDER_UNKNOWN", detail=f"availability {provider.availability}")

    if on_progress:
        on_progress("resolve", 5, provider=provider.id)

    client = LLMClient.from_env()
    if on_progress:
        on_progress("docs", 10, provider=provider.id)

    def chunk_progress(index: int, total: int) -> None:
        if on_progress:
            percent = 15 + int(60 * (index / max(total, 1)))
            on_progress("extract", percent, chunk=index + 1, total=total)

    research_result = research(provider, client, max_chunks=max_chunks, on_progress=chunk_progress)
    if on_progress:
        on_progress("assemble", 85, steps=len(research_result.steps))
    spec = assemble(provider, resolution, research_result, client.model)
    if on_progress:
        on_progress("render", 95, status=spec["status"])
    return BuildResult(job_id=job_id, resolution=resolution, research=research_result, spec=spec)


def render_bundle(spec: dict) -> tuple[str, dict, str]:
    return render_human(spec), render_agent_json(spec), render_agent_md(spec)
