#!/usr/bin/env python3
"""Audit the product FIND stage against a hand-checked canonical setup page.

``tools/find_benchmark.py`` probes the free rungs directly. This tool instead drives the
REAL Finder exactly as ``python3 -m guidegen find <name>`` does — the same ladder, ranker,
page budget and optional search fallback — and scores the winner against a canonical URL
that was fetched and eyeballed once.

    .venv/bin/python tools/find_audit.py                  # 30 providers, network + search
    .venv/bin/python tools/find_audit.py --no-search      # free rungs only (baseline)
    .venv/bin/python tools/find_audit.py stripe.com linear.app
    .venv/bin/python tools/find_audit.py --out data/bench/x.json

Verdicts per provider
  EXACT   winner path == canonical path after normalisation  -> "found the right page"
  NEAR    first-party and auth-ish, but a different page; one click from right, OR a
          sibling surface on the same docs host (needs a human read — titles are printed)
  WRONG   first-party but unrelated surface (marketing, help centre, another product)
  MISS    no usable surface (status unknown)
  GT-FAIL the canonical URL did not verify; the row is excluded from the score

The ground-truth table is the point: without it "found a page" is the only thing you can
say, and that number was 16/30 while the right page was 6/30.
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guidegen.env import load_env  # noqa: E402
from guidegen.find import find  # noqa: E402
from guidegen.search import NullSearch, from_env  # noqa: E402

UA = "Mozilla/5.0 (compatible; GuideGenAudit/0.1)"
CTX = ssl.create_default_context()

# (name, domain, canonical setup page) — every URL below verified 200 + auth vocabulary.
GROUND_TRUTH: list[tuple[str, str, str]] = [
    ("Stripe", "stripe.com", "https://docs.stripe.com/connect/oauth-reference"),
    ("HubSpot", "hubspot.com", "https://developers.hubspot.com/docs/apps/legacy-apps/authentication/working-with-oauth"),
    ("Zendesk", "zendesk.com", "https://developer.zendesk.com/documentation/authentication/creating-and-using-oauth-tokens-with-the-api/"),
    ("Linear", "linear.app", "https://linear.app/developers/oauth-2-0-authentication"),
    ("Asana", "asana.com", "https://developers.asana.com/docs/oauth"),
    ("Airtable", "airtable.com", "https://airtable.com/developers/web/guides/oauth"),
    ("Salesforce", "salesforce.com", "https://developer.salesforce.com/docs/platform/api-rest/guide/intro-oauth-and-connected-apps.html"),
    ("Notion", "notion.so", "https://developers.notion.com/guides/get-started/authorization"),
    ("Slack", "slack.com", "https://docs.slack.dev/authentication/installing-with-oauth/"),
    ("Shopify", "shopify.com", "https://shopify.dev/docs/apps/build/authentication-authorization/authenticate-standalone-apps"),
    ("Twilio", "twilio.com", "https://www.twilio.com/docs/iam/oauth-apps/overview"),
    ("Atlassian", "atlassian.com", "https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/"),
    ("monday.com", "monday.com", "https://developer.monday.com/apps/docs/oauth"),
    ("ClickUp", "clickup.com", "https://developer.clickup.com/docs/authentication"),
    ("Intercom", "intercom.com", "https://developers.intercom.com/docs/build-an-integration/learn-more/authentication/setting-up-oauth"),
    ("DocuSign", "docusign.com", "https://developers.docusign.com/platform/auth/authcode/"),
    ("QuickBooks", "intuit.com", "https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization"),
    ("Xero", "xero.com", "https://developer.xero.com/documentation/guides/oauth2/overview/"),
    ("Mailchimp", "mailchimp.com", "https://mailchimp.com/developer/marketing/build/start-developing/set-up-an-oauth-app"),
    ("Dropbox", "dropbox.com", "https://docs.dropboxapi.com/dropbox-api/docs/oauth"),
    ("Box", "box.com", "https://developer.box.com/guides/authentication/oauth2"),
    ("Tally", "tally.so", "https://tally.so/help/api"),  # API key only, no OAuth app registration
    ("Calendly", "calendly.com", "https://developer.calendly.com/docs/authentication/creating-an-oauth-app"),
    ("Typeform", "typeform.com", "https://www.typeform.com/developers/get-started/applications/"),
    ("Pipedrive", "pipedrive.com", "https://developers.pipedrive.com/docs/api/v1/Oauth"),
    ("Zoho", "zoho.com", "https://www.zoho.com/developer/oauth/introduction.html"),
    ("Freshdesk", "freshdesk.com", "https://developers.freshworks.com/docs/tutorials/intermediate/request-method/setup-oauth/"),
    ("Klaviyo", "klaviyo.com", "https://developers.klaviyo.com/en/docs/set_up_oauth"),
    ("Figma", "figma.com", "https://developers.figma.com/docs/rest-api/authentication/"),
    ("Plaid", "plaid.com", "https://plaid.com/docs/api/"),  # client_id/secret; Link OAuth is a different thing
]

AUTHISH = re.compile(r"oauth|auth|applications?|app[-_]registration|connect|credential|api[-_]key|access[-_]token|authoriz", re.I)
AUTH_VOCAB = re.compile(r"oauth|client\s*id|client\s*secret|redirect|api\s*key|authoriz", re.I)

# second-level labels that are not a registrable domain (keeps co.uk/co.in sane enough here)
TWO_LABEL = {"co", "com", "org", "net", "ac"}


def registrable(host: str) -> str:
    parts = [p for p in host.lower().split(".") if p]
    if len(parts) >= 3 and parts[-2] in TWO_LABEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def norm(url: str) -> tuple[str, str]:
    """(registrable host, path) with the noise removed: www, anchors, .md, trailing slash."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    if path.endswith(".md"):
        path = path[:-3]
    return registrable(host), path


def verdict(winner: str | None, expected: str) -> str:
    if not winner:
        return "MISS"
    wh, wp = norm(winner)
    eh, ep = norm(expected)
    same_host = wh == eh
    if same_host and (wp == ep or (len(min(wp, ep, key=len)) > 8 and (wp.startswith(ep) or ep.startswith(wp)))):
        return "EXACT"
    if same_host and AUTHISH.search(wp):
        return "NEAR"
    return "WRONG"


def probe(url: str) -> int:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            text = r.read(700_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
            return len(AUTH_VOCAB.findall(text)) if r.status == 200 else 0
    except Exception:  # noqa: BLE001
        return 0


def one(row: tuple[str, str, str], searcher, max_pages: int) -> dict:
    name, domain, expected = row
    t0 = time.time()
    out: dict = {"name": name, "domain": domain, "expected": expected}
    try:
        result = find(name, brand=name, domains=[domain], searcher=searcher, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001
        out.update(status="error", error=f"{type(exc).__name__}: {exc}", seconds=round(time.time() - t0, 1))
        out["verdict"] = "MISS"
        return out
    best = result.best
    out.update(
        status=result.status,
        rungs=result.rungs,
        pages=result.pages_fetched,
        search=result.search_provider,
        winner=best.url if best else None,
        title=best.title if best else None,
        kind=best.kind if best else None,
        score=best.score if best else None,
        notes=result.notes[:4],
        seconds=round(time.time() - t0, 1),
    )
    out["verdict"] = verdict(out["winner"], expected)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("domains", nargs="*", help="subset of the ground-truth domains")
    ap.add_argument("--no-search", action="store_true", help="free rungs only")
    ap.add_argument("--max-pages", type=int, default=28)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = GROUND_TRUTH
    if args.domains:
        wanted = {d.lower() for d in args.domains}
        rows = [r for r in GROUND_TRUTH if r[1] in wanted or r[0].lower() in wanted]
        if not rows:
            print(f"no ground-truth row matches {args.domains}", file=sys.stderr)
            return 2

    load_env()
    searcher = NullSearch() if args.no_search else from_env()
    print(f"searcher: {searcher.name}   providers: {len(rows)}   max_pages: {args.max_pages}", flush=True)

    # ground truth must hold before it can judge anything
    with ThreadPoolExecutor(max_workers=8) as ex:
        vocab = dict(zip([r[1] for r in rows], ex.map(lambda r: probe(r[2]), rows)))
    gt_ok = {d: n >= 3 for d, n in vocab.items()}
    for d, n in vocab.items():
        if not gt_ok[d]:
            print(f"  GT-FAIL {d}: canonical URL did not verify (vocab={n}) — excluded", flush=True)
    rows = [r for r in rows if gt_ok[r[1]]]

    t0 = time.time()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(lambda r: one(r, searcher, args.max_pages), rows):
            results.append(res)
            print(
                f"{res['name']:12s} {res['verdict']:5s} rung={(','.join(res.get('rungs') or []) or '-'):30s} "
                f"pages={res.get('pages', 0):3d} {res.get('seconds', 0):5.1f}s  {res.get('winner') or res.get('error')}",
                flush=True,
            )

    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    n = len(results)
    search_wins = sum(1 for r in results if (r.get("rungs") or [])[-1:] == ["search"] and r["verdict"] in ("EXACT", "NEAR"))
    print("\n=== SUMMARY ===")
    print(json.dumps(counts, indent=2))
    print(f"exact          {counts.get('EXACT', 0)}/{n}  ({(counts.get('EXACT', 0) / n * 100) if n else 0:.0f}%)")
    print(f"exact+near     {counts.get('EXACT', 0) + counts.get('NEAR', 0)}/{n}")
    print(f"search was the winning rung (exact/near): {search_wins}/{n}")
    print(f"searcher: {searcher.name}   wall: {round(time.time() - t0, 1)}s")

    out = Path(args.out) if args.out else ROOT / "data" / "bench" / f"find-audit-{date.today()}{'-nosearch' if args.no_search else ''}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ran_at": time.time(), "searcher": searcher.name, "counts": counts, "rows": results}, indent=1))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
