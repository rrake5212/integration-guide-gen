"""Fetch provider documentation and turn it into sectioned plain text.

Only registry-owned URLs are fetched (never user input). HTML is reduced to headings,
paragraphs, list items and code; navigation-heavy sections are dropped before chunking.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .errors import GuideError
from .input import assert_public_host
from .probe import _GuardedRedirectHandler

USER_AGENT = "oauth-guide-gen/0.1 (setup-guide researcher)"
MAX_DOC_BYTES = 3_000_000
FETCH_TIMEOUT = 20.0


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001 - HTMLParser signature
        if tag in ("script", "style", "noscript", "svg", "template"):
            self.skip += 1
        elif tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("p", "div", "section", "article", "br", "tr", "table"):
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "code":
            self.parts.append("`")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg", "template") and self.skip:
            self.skip -= 1
        elif tag in ("h1", "h2", "h3", "h4"):
            self.parts.append("\n")
        elif tag == "code":
            self.parts.append("`")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(raw_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw_html)
    text = "".join(parser.parts)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


@dataclass
class Document:
    url: str
    title: str
    text: str
    raw: str
    fetched_at: str
    http_status: int
    sha256: str

    def source(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "fetched_at": self.fetched_at,
            "http_status": self.http_status,
            "doc_hash": "sha256:" + self.sha256,
        }


def fetch_document(url: str, timeout: float = FETCH_TIMEOUT) -> Document:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise GuideError("DOCS_UNPARSEABLE", f"not an https docs URL: {url[:120]}")
    assert_public_host(parts.hostname)
    opener = urllib.request.build_opener(_GuardedRedirectHandler())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(MAX_DOC_BYTES)
            status = resp.status
            final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            code = "DOCS_RATE_LIMITED"
        elif exc.code in (401, 403):
            code = "DOCS_FORBIDDEN"
        elif exc.code == 404:
            code = "DOCS_NOT_FOUND"
        else:
            code = "DOCS_UNPARSEABLE"
        raise GuideError(code, f"HTTP {exc.code} for {url[:120]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GuideError("DOCS_UNPARSEABLE", f"{type(exc).__name__} for {url[:120]}") from exc

    raw = body.decode("utf-8", "replace")
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else final_url
    return Document(
        url=final_url,
        title=title,
        text=html_to_text(raw),
        raw=raw,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        http_status=status,
        sha256=hashlib.sha256(body).hexdigest(),
    )


@dataclass
class JsonDocument:
    url: str
    data: dict
    raw: str
    fetched_at: str
    http_status: int
    sha256: str

    def source(self) -> dict:
        return {
            "url": self.url,
            "title": "OIDC discovery document",
            "fetched_at": self.fetched_at,
            "http_status": self.http_status,
            "doc_hash": "sha256:" + self.sha256,
        }


def fetch_json(url: str, timeout: float = 15.0) -> JsonDocument:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise GuideError("DOCS_UNPARSEABLE", f"not an https JSON URL: {url[:120]}")
    assert_public_host(parts.hostname)
    opener = urllib.request.build_opener(_GuardedRedirectHandler())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(MAX_DOC_BYTES)
            status = resp.status
            final_url = resp.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            code = "DOCS_RATE_LIMITED"
        elif exc.code in (401, 403):
            code = "DOCS_FORBIDDEN"
        else:
            code = "DOCS_NOT_FOUND"
        raise GuideError(code, f"HTTP {exc.code} for {url[:120]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GuideError("DOCS_UNPARSEABLE", f"{type(exc).__name__} for {url[:120]}") from exc
    raw = body.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuideError("DOCS_UNPARSEABLE", f"discovery is not JSON: {url[:120]}") from exc
    if not isinstance(data, dict):
        raise GuideError("DOCS_UNPARSEABLE", "discovery JSON is not an object")
    return JsonDocument(
        url=final_url,
        data=data,
        raw=raw,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        http_status=status,
        sha256=hashlib.sha256(body).hexdigest(),
    )


@dataclass
class Section:
    heading: str
    level: int
    body: str


HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
AUTH_KEYWORDS = (
    "oauth", "authoriz", "token", "scope", "redirect", "credential", "client id", "client secret",
    "consent", "pkce", "refresh", "api key", "callback", "permission",
)
SKIP_HEADINGS = {
    "apps", "in this article", "client libraries", "help and support", "further reading",
    "was this doc helpful?", "help us make github docs great!", "still need help?",
}
LANGUAGE_HEADINGS = {
    "python", "php", "ruby", "java", "node.js", "go", ".net", "curl", "c#", "javascript", "typescript",
}
MAX_TOTAL_CHARS = 42_000
MAX_SECTION_CHARS = 7_000


def split_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    heading = ""
    level = 0
    body: list[str] = []

    def flush() -> None:
        if heading or body:
            sections.append(Section(heading=heading, level=level, body="\n".join(body).strip()))

    for line in text.splitlines():
        match = HEADING_RE.match(line.strip())
        if match:
            flush()
            level = len(match.group(1))
            heading = match.group(2).strip()
            body = []
        else:
            body.append(line)
    flush()
    return sections


def _nav_heavy(body: str) -> bool:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if len(lines) < 8:
        return False
    bullets = sum(1 for line in lines if line.startswith("- "))
    return bullets / len(lines) > 0.45


def select_sections(text: str, keywords: tuple[str, ...] = AUTH_KEYWORDS) -> list[Section]:
    candidates: list[tuple[int, Section, bool]] = []
    for index, section in enumerate(split_sections(text)):
        heading = (section.heading or "").strip()
        if not heading or heading.lower() in SKIP_HEADINGS or heading.lower() in LANGUAGE_HEADINGS:
            continue
        if heading.startswith("http"):
            continue
        body = section.body
        if len(body) < 250 or _nav_heavy(body):
            continue
        head_hit = any(word in heading.lower() for word in keywords)
        body_hit = any(word in body[:400].lower() for word in keywords)
        if head_hit or body_hit:
            candidates.append((index, section, head_hit))

    chosen: list[tuple[int, Section, str]] = []
    total = 0
    ordered = [row for row in candidates if row[2]] + [row for row in candidates if not row[2]]
    for index, section, _head_hit in ordered:
        body = section.body[:MAX_SECTION_CHARS]
        if total + len(body) > MAX_TOTAL_CHARS:
            continue
        chosen.append((index, section, body))
        total += len(body)
    chosen.sort(key=lambda row: row[0])
    return [Section(section.heading, section.level, body) for _index, section, body in chosen]


def chunk_text(text: str, max_chars: int = 9000, overlap: int = 500) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + max_chars // 2, end)
            if cut != -1:
                end = cut
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk]
