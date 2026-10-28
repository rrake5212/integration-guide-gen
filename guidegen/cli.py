"""CLI for P1: resolve a free-text app name or website to an OAuth provider."""

from __future__ import annotations

import json
import sys

from .errors import GuideError, user_payload
from .probe import probe_domain
from .registry import Registry
from .resolve import Resolution, Resolver

USAGE = """\
usage: python3 -m guidegen "<app name or website>" [--json] [--no-network] [--pick <provider-id>]

examples:
  python3 -m guidegen "tally.so"
  python3 -m guidegen "google sheets"
  python3 -m guidegen "tally"                 # ambiguous -> asks, exit 2
  python3 -m guidegen "tally" --pick tally
  python3 -m guidegen "example.com" --no-network

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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
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
