"""FIND stage: propose the provider's auth surface, then prove it with content.

The ladder is cheap-signal-first and every rung ends in the same ranker and the same
verification, so a search hit cannot smuggle in a worse page than an llms.txt hit:

    curated docs -> llms.txt -> sitemap -> nav -> path guesses -> well-known -> search

Measured on 30 real providers (2026-10-29): the shallow free ladder found *a* page for
16/30 but the right page for only 6/30 — help articles, marketing pages and a different
product surface (Stripe's wallet OAuth, Twilio's Authy push auth) scored as "OAuth" by
keyword alone. So the ranker, not the fetch, is the product:

* docs/developer/api host and an auth path outrank a product page with a nice title
* a page is only usable with >=3 distinct setup phrases (client id, client secret,
  redirect URI, app registration, create an app, scopes, authorization code)
* help/community/blog/marketing pages and other product surfaces are penalised hard
* two credible surfaces of different kinds is an ASK, never a silent pick

Nothing here writes facts into a guide. It answers one question: which URL should the
extractor read, and how sure are we.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

from .catalog import CatalogEntry, domain_candidates, first_party
from .errors import GuideError
from .fetch import Document, fetch_document
from .search import NullSearch, SearchProvider

MAX_PAGES = 28
SEARCH_RESERVE = 4  # search results get pages even when the free rungs spent the budget

DOCS_HOST = re.compile(r"^(docs|developer|developers|api|platform|dev)\.", re.I)
DEV_HOST = re.compile(r"develop|api\.|platform", re.I)
BAD_HOST = re.compile(r"(help|support|community|forum|stackoverflow|medium\.com|youtube|linkedin)", re.I)
BAD_PATH = re.compile(r"(blog|pricing|careers|release|changelog|status|legal|privacy|terms|/help|community|/news|case-stud|customer-stor|company-news|/about|/academy|/partners|partner-?credential|accreditation|/events|/webinars|/podcast|/thank-you|/templates?)", re.I)
AUTH_PATH = re.compile(r"(oauth|api[-_]?key|authenticat|authoriz|credential|app[-_]?registration|access[-_]?token|connect|scopes?)", re.I)
AUTH_WORD = re.compile(r"(oauth|api[\s_-]?key|authenticat|authoriz|credential|access token|app registration|scopes?)", re.I)
GENERIC_PATH = re.compile(r"(/overview|/getting-started/?$|/get-started/?$|/introduction|/index(\.html)?$|/guides/?$)", re.I)
OTHER_SURFACE = re.compile(r"(agentic|wallet|authy|push[-_]authentication|data[-_]connector|3-d secure|3ds|provisional|external[-_]credentials|social[-_]sdk)", re.I)
NAV_LABEL = re.compile(r"\b(docs?|documentation|developers?|api|build|get started|guides?|integrat|platform)\b", re.I)

SETUP_PHRASES = (
    r"client[\s_-]?id", r"client[\s_-]?secret", r"redirect[\s_-]?uri", r"app(lication)?\s+registration",
    r"create\s+(an?\s+)?(app|application|integration)", r"authorization\s+code", r"\bscopes?\b",
    r"api[\s_-]?key", r"oauth",
)
CONSOLE_PHRASES = (r"\bconsole\b", r"\bdashboard\b", r"developer\s+settings", r"app\s+dashboard", r"settings\s*[>›/]")

KINDS = ("oauth_app_registration", "api_key_settings", "app_marketplace", "connect_oauth", "discovery", "unknown")


@dataclass
class Candidate:
    url: str
    rung: str
    kind: str = "unknown"
    score: int = 0
    title: str = ""
    label: str = ""
    http_status: int = 0
    verified: bool = False
    blocked: bool = False
    hits: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    doc_hash: str | None = None

    @property
    def usable(self) -> bool:
        return self.verified and not self.blocked and self.score >= 7

    @property
    def confidence(self) -> str:
        if not self.usable:
            return "none"
        return "high" if self.score >= 9 else "medium"

    def to_dict(self) -> dict:
        return {
            "url": self.url, "rung": self.rung, "kind": self.kind, "score": self.score,
            "title": self.title, "verified": self.verified, "blocked": self.blocked,
            "http_status": self.http_status, "confidence": self.confidence,
            "hits": list(self.hits), "reasons": list(self.reasons), "doc_hash": self.doc_hash,
        }


@dataclass
class FindResult:
    query: str
    brand: str
    domains: list[str]
    status: str  # resolved | needs_review | unknown
    surfaces: list[Candidate]
    rungs: list[str] = field(default_factory=list)
    pages_fetched: int = 0
    search_provider: str = "null"
    notes: list[str] = field(default_factory=list)

    @property
    def best(self) -> Candidate | None:
        usable = [c for c in self.surfaces if c.usable]
        return usable[0] if usable else None

    def to_dict(self) -> dict:
        return {
            "query": self.query, "brand": self.brand, "domains": list(self.domains),
            "status": self.status, "search_provider": self.search_provider,
            "pages_fetched": self.pages_fetched, "rungs": list(self.rungs),
            "surfaces": [c.to_dict() for c in self.surfaces], "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- scoring
def surface_kind(url: str, title: str = "") -> str:
    text = (url + " " + title).lower()
    if "/connect" in text or "connect oauth" in text:
        return "connect_oauth"
    if re.search(r"(marketplace|app store|appstore|listing)", text):
        return "app_marketplace"
    if re.search(r"(api[\s_-]?key|personal access token|access token)", text):
        return "api_key_settings"
    if re.search(r"(oauth|authoriz|authenticat|credentials?)", text):
        return "oauth_app_registration"
    return "unknown"


def setup_hits(text: str) -> list[str]:
    return [p for p in SETUP_PHRASES if re.search(p, text, re.I)]


def score_candidate(url: str, *, label: str = "", title: str = "", text: str = "", brand: str | None = None) -> tuple[int, str, list[str]]:
    """Return (score, kind, reasons). Deterministic, no network, testable in isolation."""
    host = (urlsplit(url).hostname or "").lower()
    path = urlsplit(url).path or "/"
    score = 0
    reasons: list[str] = []

    if DOCS_HOST.match(host):
        score += 3
        reasons.append("+3 docs/developer host")
    elif DEV_HOST.search(host):
        score += 2
        reasons.append("+2 developer-ish host")
    if BAD_HOST.search(host):
        score -= 4
        reasons.append("-4 help/support/community host")
    if BAD_PATH.search(path):
        score -= 4
        reasons.append("-4 blog/legal/help path")
    if AUTH_PATH.search(path):
        score += 3
        reasons.append("+3 auth path")
    if GENERIC_PATH.search(path):
        score -= 2
        reasons.append("-2 generic landing path")
    if AUTH_WORD.search(title):
        score += 3
        reasons.append("+3 auth title")
    elif title:
        reasons.append("0 non-auth title")
    hits = setup_hits(text) if text else []
    if len(hits) >= 4:
        score += 3
        reasons.append(f"+3 {len(hits)} setup phrases")
    elif len(hits) >= 2:
        score += 1
        reasons.append(f"+1 {len(hits)} setup phrases")
    if text and any(re.search(p, text, re.I) for p in CONSOLE_PHRASES):
        score += 1
        reasons.append("+1 console/settings path described")
    if brand and first_party(host, [brand]):
        score += 2
        reasons.append("+2 first-party host")
    if AUTH_WORD.search(label):
        score += 2
        reasons.append("+2 auth link label")
    # A page whose own path says "oauth" is the OAuth page, not merely one that mentions credentials
    # (measured on Discord: .../topics/oauth2 and .../provisional-accounts/external-credentials-
    # exchange both scored 14, and the wrong one won the tie).
    if re.search(r"(^|[/_-])oauth", path, re.I):
        score += 3
        reasons.append("+3 oauth path")
    if OTHER_SURFACE.search(url + " " + title):
        score -= 6
        reasons.append("-6 different product surface")
    if path.count("/") > 5:
        score -= 1
        reasons.append("-1 very deep path")
    return score, surface_kind(url, title), reasons


def verify(document: Document, *, rung: str, label: str = "", brand: str | None = None) -> Candidate:
    hits = setup_hits(document.text)
    score, kind, reasons = score_candidate(
        document.url, label=label, title=document.title, text=document.text, brand=brand
    )
    blocked = bool(OTHER_SURFACE.search(document.url + " " + (document.title or "")))
    if blocked:
        kind = "other_surface"
    return Candidate(
        url=document.url, rung=rung, kind=kind, score=score, title=document.title[:120],
        label=label, http_status=document.http_status, verified=len(hits) >= 3, blocked=blocked,
        hits=hits, reasons=reasons, doc_hash="sha256:" + document.sha256,
    )


# ------------------------------------------------------------------------- harvesting
def anchors(html: str, base: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"""<a\b[^>]*href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", html, re.I | re.S):
        href = urljoin(base, m.group(1))
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2))).strip()
        out.append((href, label))
    return out


def _urls_in_text(text: str, base: str) -> list[str]:
    out = []
    for m in re.finditer(r"\]\((https?://[^)\s]+)\)|(?<!\()(https?://[^\s)\"'<>]+)", text):
        out.append(urljoin(base, m.group(1) or m.group(2)))
    return out


def _norm(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "/", "", ""))


class Finder:
    def __init__(
        self,
        *,
        fetcher: Callable[[str], Document] = fetch_document,
        searcher: SearchProvider | None = None,
        max_pages: int = MAX_PAGES,
    ) -> None:
        self._fetch = fetcher
        self._search = searcher or NullSearch()
        self._max = max_pages
        self.pages = 0
        self.tried: list[str] = []
        self.candidates: dict[str, Candidate] = {}
        self.notes: list[str] = []
        self.rungs: list[str] = []
        self.errors: dict[str, int] = {}
        self._budget_noted = False
        self._searching = False

    # ------------------------------------------------------------------ plumbing
    def _get(self, url: str) -> Document | None:
        limit = self._max + (SEARCH_RESERVE if self._searching else 0)
        if self.pages >= limit:
            if not self._budget_noted:
                self.notes.append(f"page budget exhausted ({self._max}); later rungs were skipped")
                self._budget_noted = True
            return None
        self.pages += 1
        self.tried.append(url)
        try:
            return self._fetch(url)
        except GuideError as exc:
            self._note_error(url, exc.code)
            return None
        except Exception as exc:  # noqa: BLE001 - a broken host must not stop the ladder
            self._note_error(url, type(exc).__name__)
            return None

    def _note_error(self, url: str, code: str) -> None:
        """Aggregate per host+code: one dead subdomain should not print 9 notes."""
        host = (urlsplit(url).hostname or url).lower()
        key = f"{host}: {code}"
        self.errors[key] = self.errors.get(key, 0) + 1

    def _consider(self, url: str, rung: str, label: str = "", brand: str | None = None) -> Candidate | None:
        key = _norm(url)
        if key in self.candidates:
            return self.candidates[key]
        doc = self._get(url)
        if doc is None:
            return None
        cand = verify(doc, rung=rung, label=label, brand=brand)
        self.candidates[key] = cand
        return cand

    def _usable(self) -> list[Candidate]:
        return sorted((c for c in self.candidates.values() if c.usable), key=lambda c: -c.score)

    @staticmethod
    def _rank_urls(items: list[tuple[str, str]], brand: str | None) -> list[tuple[str, str]]:
        """Order candidate URLs before spending a fetch on them (path/host/label only)."""
        scored = []
        for url, label in items:
            score, _, _ = score_candidate(url, label=label, brand=brand)
            scored.append((score, url, label))
        scored.sort(key=lambda row: -row[0])
        return [(url, label) for _s, url, label in scored]

    def _settled(self) -> bool:
        usable = self._usable()
        if not usable:
            return False
        top = usable[0]
        if top.score < 9:
            return False
        rival = next((c for c in usable[1:] if c.kind != top.kind), None)
        return rival is None or top.score - rival.score >= 3

    # --------------------------------------------------------------------- rungs
    def curated(self, entry: dict | None, brand: str | None) -> None:
        if not entry:
            return
        self.rungs.append("curated")
        for url in list(entry.get("docs") or [])[:3]:
            self._consider(url, "curated", brand=brand)
        for surface in (entry.get("surfaces") or [])[:3]:
            if surface.get("docs_url"):
                self._consider(surface["docs_url"], "curated", brand=brand)

    def llms(self, domains: list[str], brand: str | None) -> None:
        self.rungs.append("llms")
        for domain in domains:
            for host in (domain, f"docs.{domain}", f"developers.{domain}", f"developer.{domain}"):
                doc = self._get(f"https://{host}/llms.txt")
                if doc is None or len(doc.raw) < 60:
                    continue
                urls: list[str] = []
                for line in doc.text.splitlines():
                    if AUTH_WORD.search(line):
                        urls += _urls_in_text(line, doc.url)
                picked = [u for u in urls if AUTH_PATH.search(u) and not BAD_PATH.search(u)][:8]
                for url, _label in self._rank_urls([(u, "") for u in picked], brand)[:4]:
                    self._consider(url, "llms", brand=brand)
                if self._settled():
                    return

    def sitemap(self, domains: list[str], brand: str | None) -> None:
        self.rungs.append("sitemap")
        for domain in domains:
            # Only portal sitemaps. A marketing root's sitemap is 100k URLs of case
            # studies and partner pages, and its "credential" paths are not auth pages.
            for host in (f"docs.{domain}", f"developers.{domain}", f"developer.{domain}"):
                doc = self._get(f"https://{host}/sitemap.xml")
                if doc is None or "<" not in doc.raw:
                    continue
                locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", doc.raw)
                picked = [u for u in locs if AUTH_PATH.search(u) and not BAD_PATH.search(u)][:8]
                for url, _label in self._rank_urls([(u, "") for u in picked], brand)[:4]:
                    self._consider(url, "sitemap", brand=brand)
                if self._settled():
                    return

    def nav(self, domains: list[str], brand: str | None) -> None:
        self.rungs.append("nav")
        for domain in domains:
            for host in (f"developer.{domain}", f"developers.{domain}", f"docs.{domain}", domain):
                doc = self._get(f"https://{host}/")
                if doc is None:
                    continue
                portal = bool(DOCS_HOST.match(host) or DEV_HOST.search(host))
                picked, sections = [], []
                for url, label in anchors(doc.raw, doc.url):
                    if BAD_PATH.search(url):
                        continue
                    if AUTH_WORD.search(label) or AUTH_PATH.search(url):
                        # A marketing homepage has case studies and partner pages with
                        # "connect"/"oauth" in the slug; only a portal host earns a link
                        # from the product root, and only under a docs/developer path.
                        if not portal and not re.search(r"/(develop|docs|api|integrat|build)", url, re.I):
                            continue
                        picked.append((url, label))
                    elif NAV_LABEL.search(label):
                        sections.append(url)
                for url, label in self._rank_urls(picked, brand)[:4]:
                    self._consider(url, "nav", label=label, brand=brand)
                if self._settled():
                    return
                if picked:
                    continue
                for section in sections[:2]:
                    sub = self._get(section)
                    if sub is None:
                        continue
                    for url, label in anchors(sub.raw, sub.url):
                        if AUTH_WORD.search(label) or AUTH_PATH.search(url):
                            cand = self._consider(url, "nav", label=label, brand=brand)
                            if cand and cand.usable and self._settled():
                                return
                        if self.pages >= self._max:
                            return

    GUESSES = (
        "/docs/authentication", "/docs/oauth", "/authentication", "/docs/api/authentication",
        "/docs/app-registration", "/developers/authentication", "/docs/getting-started/authentication",
        "/docs/api-keys",
    )
    GUESS_LIMIT = 6

    def guesses(self, domains: list[str], brand: str | None) -> None:
        self.rungs.append("guess")
        attempts = 0
        for domain in domains:
            for host in (f"docs.{domain}", f"developers.{domain}", f"developer.{domain}"):
                for path in self.GUESSES:
                    if attempts >= self.GUESS_LIMIT:
                        return
                    attempts += 1
                    cand = self._consider(f"https://{host}{path}", "guess", brand=brand)
                    if cand and cand.usable:
                        return
                    if self.pages >= self._max:
                        return

    def wellknown(self, domains: list[str]) -> None:
        self.rungs.append("wellknown")
        for domain in domains:
            for host in (domain, f"auth.{domain}", f"login.{domain}"):
                for path in ("/.well-known/openid-configuration", "/.well-known/oauth-authorization-server"):
                    doc = self._get(f"https://{host}{path}")
                    if doc is None:
                        continue
                    if "authorization_endpoint" not in doc.text and "token_endpoint" not in doc.text:
                        continue
                    key = _norm(doc.url)
                    self.candidates.setdefault(key, Candidate(
                        url=doc.url, rung="wellknown", kind="discovery", score=0,
                        title=f"OAuth metadata ({host})", http_status=doc.http_status, verified=True,
                        hits=["authorization_endpoint"], reasons=["discovery document: endpoints only, not setup steps"],
                        doc_hash="sha256:" + doc.sha256,
                    ))
                    return

    def search(self, brand: str, domains: list[str], catalogue: CatalogEntry | None) -> None:  # noqa: ARG002
        if isinstance(self._search, NullSearch) or self._usable():
            return
        self.rungs.append("search")
        # A site: query hits developer docs far more often than a generic one (measured:
        # generic returned blogs and community threads for HubSpot; site: returned the
        # developer-platform authentication pages). The generic query is the fallback.
        queries = [f"site:{domains[0]} oauth app authentication"] if domains else []
        queries.append(f"{brand} OAuth app authentication developer documentation")
        seen: set[str] = set()
        self._searching = True  # results may spend the reserved pages
        try:
            for query in queries:
                results = self._search.search(query, limit=6)
                if not results:
                    error = getattr(self._search, "last_error", None)
                    self.notes.append(
                        f"search ({self._search.name}) returned nothing" + (f": {error}" if error else "")
                    )
                    continue
                ranked = []
                for row in results:
                    host = (urlsplit(row.url).hostname or "").lower()
                    # Provider-authored only: a blog, an agency or a community thread must
                    # never become the guide's source, however well it ranks.
                    if not first_party(host, [brand, *domains]):
                        continue
                    score, _, _ = score_candidate(row.url, label=row.title, title=row.title, brand=brand)
                    ranked.append((score, row))
                for _score, row in sorted(ranked, key=lambda x: -x[0])[:3]:
                    key = _norm(row.url)
                    if key in seen:
                        continue
                    seen.add(key)
                    cand = self._consider(row.url, "search", label=row.title, brand=brand)
                    if cand and cand.usable and self._settled():
                        return
        finally:
            self._searching = False

    # ---------------------------------------------------------------------- entry
    def run(self, query: str, *, brand: str, domains: list[str], entry: dict | None = None,
            catalogue: CatalogEntry | None = None, allow_search: bool = True) -> FindResult:
        self.curated(entry, brand)
        if not self._settled():
            self.llms(domains, brand)
        if not self._settled():
            self.sitemap(domains, brand)
        if not self._settled():
            self.nav(domains, brand)
        if not self._settled():
            self.guesses(domains, brand)
        if allow_search and not self._settled():
            self.search(brand, domains, catalogue)
        # Discovery metadata is corroboration, not a surface: it runs last so a search
        # fallback is never starved of budget by six well-known probes.
        if not self._settled():
            self.wellknown(domains)

        surfaces = sorted(self.candidates.values(), key=lambda c: (-c.score, c.url))
        usable = [c for c in surfaces if c.usable]
        status = "unknown"
        notes = list(self.notes)
        for key, count in sorted(self.errors.items(), key=lambda kv: -kv[1])[:6]:
            notes.append(f"{key} x{count}" if count > 1 else key)
        if len(self.errors) > 6:
            notes.append(f"+{len(self.errors) - 6} more host errors")
        if usable:
            top = usable[0]
            kinds = {c.kind for c in usable[:2]}
            if top.score >= 9 and len(kinds) == 1:
                status = "resolved"
            else:
                status = "needs_review"
                if len(kinds) > 1:
                    notes.append(
                        "two credible surfaces found ("
                        + ", ".join(sorted(kinds))
                        + "); pick the one being integrated instead of merging them"
                    )
                else:
                    notes.append(f"best candidate scores {top.score}; needs a human confirm")
        elif any(c.kind == "discovery" for c in surfaces):
            status = "needs_review"
            notes.append("OAuth discovery metadata exists, but no setup page was located")
        else:
            notes.append("no usable setup page found by any free rung" + ("" if isinstance(self._search, NullSearch) else " or search"))
        return FindResult(
            query=query, brand=brand, domains=domains, status=status, surfaces=surfaces,
            rungs=self.rungs, pages_fetched=self.pages, search_provider=getattr(self._search, "name", "null"),
            notes=notes,
        )


def infer_domains(name: str, slug: str, *, fetcher: Callable[[str], Document] = fetch_document,
                  limit: int = 3) -> list[str]:
    """Verify inferred hosts against their own homepage before trusting one.

    A live host is not enough: a parked domain or a namesake company also answers 200.
    Accept only when the host is first-party AND its own title names the brand, then
    prefer the strongest title match (a JS shell with no title never wins).
    """
    ranked: list[tuple[int, str]] = []
    for host in domain_candidates(name, slug)[:limit]:
        try:
            document = fetcher(f"https://{host}/")
        except Exception:  # noqa: BLE001 - a dead guess is not an error
            continue
        if not first_party(host, [name, slug]):
            continue
        title = (document.title or "").strip().lower()
        if not title:
            # A JS shell with no title is how a parked or namesake host looks; the brand
            # word living only in the body is not evidence (measured on linear.io).
            continue
        brand = name.lower()
        if title.startswith(brand):
            score = 3
        elif brand in title:
            score = 2
        elif any(token in title for token in (name.lower(), slug.lower()) if token):
            score = 1
        else:
            continue
        ranked.append((score, host))
    if not ranked:
        return []
    ranked.sort(key=lambda row: (-row[0], len(row[1])))
    top = ranked[0][0]
    return [host for score, host in ranked if score == top][:2]


def find(
    query: str,
    *,
    brand: str,
    domains: list[str],
    entry: dict | None = None,
    catalogue: CatalogEntry | None = None,
    searcher: SearchProvider | None = None,
    fetcher: Callable[[str], Document] = fetch_document,
    max_pages: int = MAX_PAGES,
    allow_search: bool = True,
) -> FindResult:
    return Finder(fetcher=fetcher, searcher=searcher, max_pages=max_pages).run(
        query, brand=brand, domains=domains, entry=entry, catalogue=catalogue, allow_search=allow_search
    )
