"""CLI: resolve a free-text app name or website, and find its auth surface."""

from __future__ import annotations

import json
import sys

from .catalog import CatalogEntry, domain_candidates
from .env import load_env
from .errors import GuideError, user_payload
from .find import MAX_PAGES, find, infer_domains
from .probe import probe_domain
from .registry import Registry
from .resolve import Resolution, Resolver
from .search import NullSearch, from_env
from .surfaces import SurfaceStore

USAGE = """\
usage: python3 -m guidegen "<app name or website>" [--json] [--no-network] [--pick <provider-id>]
       python3 -m guidegen find "<app name or website>" [--json] [--no-network] [--no-search] [--save]
       python3 -m guidegen batch <name>... [--file names.txt] [--no-search] [--max-pages N]
       python3 -m guidegen review [--json] [--limit N]
       python3 -m guidegen confirm <entry-id> <proposal-index>

examples:
  python3 -m guidegen "tally.so"
  python3 -m guidegen "google sheets"
  python3 -m guidegen "tally"                 # ambiguous -> asks, exit 2
  python3 -m guidegen "tally" --pick tally
  python3 -m guidegen "example.com" --no-network
  python3 -m guidegen find "hubspot" --save   # propose, then confirm with review/confirm

exit codes: 0 resolved · 2 ambiguous (ask) · 3 unknown · 4 invalid input
"""


def _forced(resolver: Resolver, raw: str, pick: str) -> Resolution:
    registry = resolver.registry
    if pick in registry.providers:
        provider = registry.provider(pick)
    else:
        ids = registry.by_alias.get(pick.strip().lower(), set())
        if len(ids) != 1:
            raise SystemExit(f"unknown or ambiguous --pick value: {pick!r}")
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
        notes=["forced with --pick"],
    )


def _print_human(res: Resolution) -> None:
    if res.decision == "resolved" and res.provider:
        p = res.provider
        print(f'RESOLVED  "{res.raw}" -> {p.name} ({p.id})')
        print(f"          availability  {res.availability}")
        print(f"          matched by    {res.matched_by} - confidence {res.confidence}")
        if p.domains:
            print(f"          domain        {p.domains[0]}")
        if p.console:
            print(f"          console       {p.console}")
        for doc in p.docs:
            print(f"          docs          {doc}")
        for note in res.notes:
            print(f"          note          {note}")
        return

    if res.decision == "catalogue" and res.catalogue:
        c = res.catalogue
        print(f'ROUTED    "{res.raw}" -> {c.name} ({c.slug})   [toolkit index, no curated row]')
        print(f"          supports      {c.label}")
        print(f"          availability  {res.availability} (unverified until FIND locates the docs)")
        for note in res.notes:
            print(f"          note          {note}")
        return

    if res.decision == "ask":
        print(f'ASK       "{res.raw}" -> {len(res.alternatives)} candidates; refusing to guess')
        for i, c in enumerate(res.alternatives, 1):
            print(f"          {i}  {c.name} ({c.id})  {c.domain or '-'}  {c.availability}")
        picks = " | ".join(f"--pick {c.id}" for c in res.alternatives)
        print(f"          choose one: {picks}")
        return

    print(f'UNKNOWN   "{res.raw}" -> no provider matched')
    for note in res.notes:
        print(f"          - {note}")


def _route_for(raw: str, registry: Registry, network: bool):
    """Resolve the query to a brand, domains, entry id and catalogue record."""
    resolver = Resolver(registry=registry, probe=probe_domain if network else None)
    res = resolver.resolve(raw)
    catalogue = registry.catalog(raw)
    if res.provider is not None:
        brand, domains, entry_id = res.provider.name, list(res.provider.domains), res.provider.id
    elif catalogue is not None:
        brand, entry_id = catalogue.name, catalogue.slug.lower()
        domains = (
            infer_domains(catalogue.name, catalogue.slug)
            if network
            else domain_candidates(catalogue.name, catalogue.slug)[:1]
        )
    else:
        brand, entry_id = raw, raw.lower().replace(" ", "-")
        domains = [raw] if ("." in raw and " " not in raw) else []
    return res, brand, domains, entry_id, catalogue


def _cmd_find(argv: list[str]) -> int:
    as_json = False
    network = True
    save = False
    use_search = True
    max_pages = MAX_PAGES
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            as_json = True
        elif arg == "--no-network":
            network = False
        elif arg == "--no-search":
            use_search = False
        elif arg == "--save":
            save = True
        elif arg == "--no-save":
            save = False
        elif arg == "--max-pages":
            i += 1
            max_pages = int(argv[i])
        elif arg.startswith("-"):
            print(f"unknown option: {arg}", file=sys.stderr)
            return 1
        else:
            rest.append(arg)
        i += 1
    if len(rest) != 1:
        print(USAGE, end="", file=sys.stderr)
        return 1

    raw = rest[0]
    registry = Registry()
    store = SurfaceStore()
    try:
        res, brand, domains, entry_id, catalogue = _route_for(raw, registry, network)
    except GuideError as exc:
        print(f"ERROR  {exc.spec.user_message}", file=sys.stderr)
        return 4
    if res.decision == "ask":
        _print_human(res)
        return 2

    entry = store.get(entry_id)
    if not network:
        payload = {
            "route": res.to_dict(),
            "brand": brand,
            "domains": domains,
            "catalogue": catalogue.to_dict() if catalogue else None,
            "would_fetch": bool(domains),
        }
        if as_json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(f'FIND      "{raw}" -> {brand}   (route: {res.decision})')
            print(f"          domains   {', '.join(domains) or '-'}")
            print(f"          catalogue {catalogue.label if catalogue else '-'}")
            print("          --no-network: nothing fetched")
        return 0 if domains else 3

    searcher = from_env() if use_search else NullSearch()
    result = find(
        raw, brand=brand, domains=domains,
        entry=entry.to_dict() if entry else None,
        catalogue=catalogue, searcher=searcher, max_pages=max_pages,
    )
    if save:
        store.propose(
            result, entry_id=entry_id, name=brand, domains=domains,
            auth_families=list(catalogue.auth_families) if catalogue else None,
        )
        store.save()

    if as_json:
        print(json.dumps({"route": res.to_dict(), "find": result.to_dict()}, indent=2, ensure_ascii=False))
    else:
        print(f'FIND      "{raw}" -> {brand}   [{catalogue.label if catalogue else (entry.availability if entry else "-")}]')
        print(f"          domains   {', '.join(domains) or '-'}")
        print(f"          status    {result.status}   pages {result.pages_fetched}   search {result.search_provider}")
        for n, cand in enumerate(result.surfaces[:4], 1):
            flag = "OK " if cand.usable else "-- "
            print(f"          {n}  {flag}[{cand.score:>2}] {cand.kind:24s} {cand.url}")
            if cand.title:
                print(f"                 {cand.title[:96]}")
        for note in result.notes:
            print(f"          note      {note}")
        if save:
            print(f"          saved     {entry_id} (proposals; confirm with: python3 -m guidegen confirm {entry_id} 1)")
    return 0 if result.status != "unknown" else 3


def _cmd_batch(argv: list[str]) -> int:
    """One command for the gate-E review queue: find + save every name in a list."""
    network = True
    use_search = True
    max_pages = MAX_PAGES
    source: str | None = None
    names: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--no-network":
            network = False
        elif arg == "--no-search":
            use_search = False
        elif arg == "--max-pages":
            i += 1
            max_pages = int(argv[i])
        elif arg == "--file":
            i += 1
            source = argv[i]
        elif arg.startswith("-"):
            print(f"unknown option: {arg}", file=sys.stderr)
            return 1
        else:
            names.append(arg)
        i += 1
    if source:
        names += [line.strip() for line in open(source, encoding="utf-8") if line.strip() and not line.startswith("#")]
    if not names:
        print(USAGE, end="", file=sys.stderr)
        return 1

    registry = Registry()
    store = SurfaceStore()
    searcher = from_env() if use_search else NullSearch()
    counts: dict[str, int] = {}
    print(f"BATCH     {len(names)} names   search {getattr(searcher, 'name', 'null')}")
    for raw in names:
        try:
            res, brand, domains, entry_id, catalogue = _route_for(raw, registry, network)
        except GuideError as exc:
            print(f"  SKIP    {raw}: {exc.spec.user_message}")
            counts["error"] = counts.get("error", 0) + 1
            continue
        if res.decision == "ask" or not domains:
            status = "ask" if res.decision == "ask" else "no-domain"
            print(f"  {status.upper():7s} {raw}")
            counts[status] = counts.get(status, 0) + 1
            continue
        entry = store.get(entry_id)
        result = find(
            raw, brand=brand, domains=domains,
            entry=entry.to_dict() if entry else None,
            catalogue=catalogue, searcher=searcher, max_pages=max_pages,
        )
        store.propose(
            result, entry_id=entry_id, name=brand, domains=domains,
            auth_families=list(catalogue.auth_families) if catalogue else None,
        )
        store.save()  # per item: a long batch survives an interrupt
        best = result.best
        print(f"  {result.status.upper():11s} {raw:22s} {best.url if best else '-'}")
        counts[result.status] = counts.get(result.status, 0) + 1
    print("BATCH done:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("next: python3 -m guidegen review --limit 40")
    return 0


def _cmd_review(argv: list[str]) -> int:
    as_json = False
    limit: int | None = None
    i = 0
    while i < len(argv):
        if argv[i] == "--json":
            as_json = True
        elif argv[i] == "--limit":
            i += 1
            limit = int(argv[i])
        else:
            print(f"unknown option: {argv[i]}", file=sys.stderr)
            return 1
        i += 1
    store = SurfaceStore()
    rows = store.pending()
    needs = [e for e in rows if e.proposals]
    empty = [e for e in rows if not e.proposals]
    if limit:
        needs = needs[:limit]
    if as_json:
        print(json.dumps({e.id: e.to_dict() for e in needs}, indent=2, ensure_ascii=False))
        return 0
    if not needs and not empty:
        print("REVIEW    nothing pending")
        return 0
    print(f"REVIEW    {len(needs)} to confirm · {len(empty)} with no candidates found\n")
    for entry in needs:
        print(f"  {entry.id:28s} {entry.name}  [{entry.status}]")
        for n, prop in enumerate(entry.proposals, 1):
            print(f"    {n}  [{prop.confidence:6s}] {prop.kind:24s} {prop.docs_url}")
            if prop.title:
                print(f"       {prop.title[:100]}")
        if not entry.proposals:
            print("    (no proposals; run: python3 -m guidegen find \"<name>\" --save)")
    if empty:
        names = ", ".join(e.id for e in empty[:20])
        more = " …" if len(empty) > 20 else ""
        print(f"\n  no candidates found: {names}{more}")
        print("  (add a search key, or confirm one by hand: guidegen confirm <entry-id> 1)")
    return 0


def _cmd_confirm(argv: list[str]) -> int:
    if len(argv) != 2:
        print(USAGE, end="", file=sys.stderr)
        return 1
    entry_id, raw_index = argv
    try:
        index = int(raw_index) - 1
    except ValueError:
        print("proposal index must be a number (1-based)", file=sys.stderr)
        return 1
    store = SurfaceStore()
    try:
        entry = store.confirm(entry_id, index)
    except (KeyError, IndexError, ValueError) as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        return 4
    store.save()
    print(f"CONFIRMED {entry.id} -> {entry.surfaces[0].docs_url}")
    print(f"          accepted_by human  kind {entry.surfaces[0].kind}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    load_env()  # .env is the documented home for the optional search key
    if argv and argv[0] == "find":
        return _cmd_find(argv[1:])
    if argv and argv[0] == "batch":
        return _cmd_batch(argv[1:])
    if argv and argv[0] == "review":
        return _cmd_review(argv[1:])
    if argv and argv[0] == "confirm":
        return _cmd_confirm(argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE, end="")
        return 0
    if argv[0] == "resolve":
        argv = argv[1:]

    as_json = False
    network = True
    pick: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--json":
            as_json = True
        elif arg == "--no-network":
            network = False
        elif arg == "--pick":
            i += 1
            if i >= len(argv):
                print("--pick needs a provider id", file=sys.stderr)
                return 1
            pick = argv[i]
        elif arg.startswith("--pick="):
            pick = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            print(f"unknown option: {arg}", file=sys.stderr)
            return 1
        else:
            rest.append(arg)
        i += 1

    if len(rest) != 1:
        print(USAGE, end="", file=sys.stderr)
        return 1

    raw = rest[0]
    resolver = Resolver(registry=Registry(), probe=probe_domain if network else None)
    try:
        res = _forced(resolver, raw, pick) if pick else resolver.resolve(raw)
    except GuideError as exc:
        if as_json:
            print(json.dumps(user_payload(exc.code), indent=2, ensure_ascii=False))
        else:
            print(f"ERROR  {exc.spec.user_message}", file=sys.stderr)
        return 4

    if as_json:
        print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_human(res)
    return res.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
