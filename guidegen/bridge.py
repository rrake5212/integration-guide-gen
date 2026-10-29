"""Bridge: a catalogue-routed name becomes a provider a guide can actually be built for.

PLAN §13.6 named this as the next step and §13.8 measured the gap on the live site
(``POST /api/guides {"input":"hubspot"}`` -> ``PROVIDER_UNKNOWN`` at stage ``resolve``): the
resolver could route 1,688 catalogue names but only the 9 curated rows could generate.

    catalogue entry -> (stored surface | FIND: ladder + search) -> provider entry -> Provider

Preference order, and the cost of each:

1. a surface already in ``surfaces.json`` — human-confirmed or previously auto-accepted.
   No network, no query.
2. FIND with the search fallback — 1-2 Serper queries for a provider the free rungs miss,
   then cached for 7 days by ``CachedSearch`` and forever in ``surfaces.json``.

Gate E still holds: ``SurfaceStore.propose`` auto-accepts a resolved result so the guide can be
built now, but the entry stays in ``pending()`` until a human confirms it, and a human answer is
never overwritten.

Nothing here relaxes the standing rule that the catalogue is a router, never a fact source: the
entry contributes an id, a name, and auth families; every fact in the guide still comes from the
provider's own page that FIND located.
"""

from __future__ import annotations

from typing import Callable

from .catalog import CatalogEntry, domain_candidates
from .errors import GuideError
from .fetch import Document, fetch_document
from .find import MAX_PAGES, find, infer_domains
from .registry import Provider
from .search import SearchProvider, from_env
from .surfaces import Surface, SurfaceStore

# The spec's availability vocabulary; a catalogue entry says "api_key" where the spec says
# "api_key_only" (see provider.spec.schema.json).
_AVAILABILITY = {"api_key": "api_key_only"}


def availability_for(entry: CatalogEntry) -> str:
    return _AVAILABILITY.get(entry.availability, entry.availability)


def provider_from_surface(
    entry: CatalogEntry,
    surface: Surface,
    domains: list[str],
    *,
    note: str | None = None,
) -> Provider:
    """The Provider the research/assemble stages need, built from a located surface.

    ``docs`` is the one fact FIND is allowed to supply: where the setup page is. Everything the
    guide asserts about that page is researched from the page itself.
    """
    return Provider(
        id=entry.slug.lower(),
        name=entry.name,
        domains=tuple(domains),
        aliases=(),
        slug_prefixes=(),
        availability=availability_for(entry),
        docs=(surface.docs_url,),
        console=surface.console_url,
        discovery=surface.discovery_url,
        note=note or f"docs located by FIND ({surface.rung or 'stored'}); curated row pending",
    )


def provider_for_catalogue(
    entry: CatalogEntry,
    *,
    store: SurfaceStore | None = None,
    searcher: SearchProvider | None = None,
    fetcher: Callable[[str], Document] = fetch_document,
    max_pages: int = MAX_PAGES,
    on_progress=None,
) -> Provider:
    """Resolve a catalogue entry to a Provider, finding and recording its setup docs."""
    store = store or SurfaceStore()
    entry_id = entry.slug.lower()

    stored = store.get(entry_id)
    if stored and stored.surfaces:
        domains = list(stored.domains) or domain_candidates(entry.name, entry.slug)[:1]
        return provider_from_surface(
            entry, stored.surfaces[0], domains,
            note=f"docs from surfaces.json ({stored.accepted_by}-accepted {stored.accepted_at or 'seed'}); curated row pending",
        )

    if on_progress:
        on_progress("find", 3, provider=entry_id)
    domains = infer_domains(entry.name, entry.slug, fetcher=fetcher)
    if not domains:
        domains = domain_candidates(entry.name, entry.slug)[:1]
    if not domains:
        raise GuideError("PROVIDER_UNKNOWN", detail=f"no domain could be inferred for {entry.name!r}")

    result = find(
        entry.name,
        brand=entry.name,
        domains=domains,
        catalogue=entry,
        searcher=searcher if searcher is not None else from_env(),
        fetcher=fetcher,
        max_pages=max_pages,
    )
    store.propose(result, entry_id=entry_id, name=entry.name, domains=domains,
                  auth_families=list(entry.auth_families))
    store.save()

    best = result.best
    if best is None:
        raise GuideError(
            "PROVIDER_UNKNOWN",
            detail=f"FIND {result.status} for {entry.name!r} over {len(domains)} domain(s)",
        )
    surface = Surface(
        kind=best.kind,
        docs_url=best.url,
        title=best.title or None,
        confidence=best.confidence,
        score=best.score,
        rung=best.rung,
    )
    note = f"docs located by FIND ({best.rung}); curated row pending"
    if result.status == "needs_review":
        note += " — two credible surfaces, the top-ranked one was used"
    return provider_from_surface(entry, surface, domains, note=note)
