#!/usr/bin/env python3
"""Re-measure the FIND ladder against real providers (network; no keys, no spend).

Run it before and after any ranker change — the point of the ladder is a number, not a vibe:
    .venv/bin/python tools/find_benchmark.py                 # default 30-provider set
    .venv/bin/python tools/find_benchmark.py stripe.com linear.app ...   # your own set
    .venv/bin/python tools/find_benchmark.py --out /tmp/bench.json

It reports which rung produced the winner and flags a rung="MISS". A winner here is only
"a page with setup vocabulary" — it is NOT proof of the right surface. Pair it with the
audit in PLAN §13.6 (canonical-URL check) before quoting a coverage number.


Rungs tested, in the order the product would use them:
  1 llms.txt   (root, docs., developers., developer.)
  2 sitemap.xml filtered for auth paths
  3 nav links on the homepage / developer portal / docs home
  4 .well-known OIDC + RFC 8414 (endpoint metadata, corroboration only)
  5 common-path guesses, applied ONLY to what rungs 1-3 missed
A hit counts only when the fetched page carries real setup vocabulary
(client id, client secret, redirect URI, scopes, app registration...).
"""
import json, re, ssl, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

UA = "Mozilla/5.0 (compatible; GuideGenFindProbe/0.1)"
TIMEOUT = 8
MAXB = 2_000_000
CTX = ssl.create_default_context()

DEFAULT_DOMAINS = [
    "stripe.com", "hubspot.com", "zendesk.com", "linear.app", "asana.com",
    "airtable.com", "salesforce.com", "notion.so", "slack.com", "shopify.com",
    "twilio.com", "atlassian.com", "monday.com", "clickup.com", "intercom.com",
    "docusign.com", "intuit.com", "xero.com", "mailchimp.com", "dropbox.com",
    "box.com", "tally.so", "calendly.com", "typeform.com", "pipedrive.com",
    "zoho.com", "freshdesk.com", "klaviyo.com", "figma.com", "plaid.com",
]

STRONG = [
    r"client[\s_-]?id", r"client[\s_-]?secret", r"redirect[\s_-]?uri",
    r"app(lication)?\s+registration", r"authorization\s+code",
    r"\bscopes?\b", r"oauth", r"api[\s_-]?key",
    r"create\s+(an?\s+)?(app|application|integration)",
]
STRONG_RE = [re.compile(p, re.I) for p in STRONG]
AUTH_WORD = re.compile(r"oauth|api[\s_-]?key|authenticat|authoriz|credential|access token|app registration|scopes", re.I)
AUTH_PATH = re.compile(r"(oauth|api[-_]?key|authenticat|authoriz|credential|app[-_]?registration|connect/|/connect|scopes|access[-_]?token)", re.I)
BAD_PATH = re.compile(r"(blog|pricing|careers|release[-_]?notes|changelog|status|legal|privacy|terms)", re.I)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,text/plain,application/xml,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            raw = r.read(MAXB + 1)[:MAXB]
            enc = r.headers.get_content_charset() or "utf-8"
            return {"url": r.geturl(), "status": r.status, "text": raw.decode(enc, "replace"),
                    "ctype": r.headers.get_content_type()}
    except urllib.error.HTTPError as e:
        return {"url": url, "status": e.code, "text": "", "ctype": ""}
    except Exception as e:  # noqa: BLE001
        return {"url": url, "status": 0, "text": "", "ctype": "", "err": type(e).__name__}


def score(text):
    hits = sorted({p.pattern for p in STRONG_RE if p.search(text)})
    return len(hits), hits


def links_from_html(html, base):
    out = []
    for m in re.finditer(r"""<a\b[^>]*href=["']([^"'#]+)["'][^>]*>(.*?)</a>""", html, re.I | re.S):
        href, label = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        out.append((urljoin(base, href), re.sub(r"\s+", " ", label).strip()))
    return out


def urls_from_text(text, base):
    out = []
    for m in re.finditer(r"\]\((https?://[^)\s]+)\)|(?<!\()(https?://[^\s)\"'<>]+)", text):
        u = m.group(1) or m.group(2)
        out.append(urljoin(base, u))
    return out


def rung_llms(domain):
    cands = []
    for host in (domain, f"docs.{domain}", f"developers.{domain}", f"developer.{domain}"):
        url = f"https://{host}/llms.txt"
        r = fetch(url)
        if r["status"] == 200 and len(r["text"]) > 100:
            lines = [ln.strip() for ln in r["text"].splitlines() if AUTH_WORD.search(ln)]
            u = []
            for ln in lines[:40]:
                u += urls_from_text(ln, url)
            seen, keep = set(), []
            for x in u:
                if x not in seen and AUTH_PATH.search(x) and not BAD_PATH.search(x):
                    seen.add(x)
                    keep.append(x)
            cands.append({"index": url, "bytes": len(r["text"]), "auth_lines": len(lines), "candidates": keep[:6]})
    return cands


def rung_sitemap(domain):
    cands = []
    for host in (f"docs.{domain}", f"developers.{domain}", domain):
        url = f"https://{host}/sitemap.xml"
        r = fetch(url)
        if r["status"] != 200 or "<" not in r["text"]:
            continue
        locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r["text"])
        picked = [u for u in locs if AUTH_PATH.search(u) and not BAD_PATH.search(u)]
        # one level into a sitemap index that names docs/developers
        if not picked and locs[:5] and any(x.endswith(".xml") for x in locs[:5]):
            for sub in [u for u in locs if re.search(r"(doc|develop|api|auth)", u, re.I)][:2]:
                r2 = fetch(sub)
                if r2["status"] == 200:
                    locs += re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r2["text"])
            picked = [u for u in locs if AUTH_PATH.search(u) and not BAD_PATH.search(u)]
        cands.append({"sitemap": url, "locs": len(locs), "candidates": picked[:6]})
    return cands


def rung_nav(domain):
    cands = []
    for host in (domain, f"developer.{domain}", f"developers.{domain}", f"docs.{domain}"):
        base = f"https://{host}/"
        r = fetch(base)
        if r["status"] != 200:
            continue
        cands.append({"page": base, "candidates": []})
        for href, label in links_from_html(r["text"], base):
            if AUTH_WORD.search(label) or AUTH_PATH.search(href):
                if not BAD_PATH.search(href) and urlparse(href).netloc:
                    cands[-1]["candidates"].append(href)
        cands[-1]["candidates"] = cands[-1]["candidates"][:8]
    return cands


def rung_wellknown(domain):
    found = []
    for host in (domain, f"auth.{domain}", f"login.{domain}"):
        for path in ("/.well-known/openid-configuration", "/.well-known/oauth-authorization-server"):
            r = fetch(f"https://{host}{path}")
            if r["status"] == 200 and "authorization_endpoint" in r["text"] or (r["status"] == 200 and "token_endpoint" in r["text"]):
                try:
                    d = json.loads(r["text"])
                except Exception:  # noqa: BLE001
                    d = {}
                found.append({"doc": f"https://{host}{path}",
                              "authorization_endpoint": d.get("authorization_endpoint"),
                              "token_endpoint": d.get("token_endpoint"),
                              "scopes": len(d.get("scopes_supported") or [])})
    return found


GUESS = [
    "/docs/authentication", "/docs/oauth", "/docs/api/authentication",
    "/developers/authentication", "/docs/app-registration", "/api/authentication",
    "/docs/getting-started/authentication", "/docs/api-keys",
]


def rung_guess(domain):
    out = []
    for host in (f"docs.{domain}", f"developers.{domain}", f"developer.{domain}"):
        for p in GUESS:
            r = fetch(f"https://{host}{p}")
            if r["status"] == 200 and len(r["text"]) > 500:
                n, hits = score(r["text"])
                out.append({"url": r["url"], "score": n, "hits": hits})
    out.sort(key=lambda x: -x["score"])
    return out[:3]


def verify(urls, limit=2):
    out = []
    for u in urls[:limit]:
        r = fetch(u)
        if r["status"] == 200:
            n, hits = score(r["text"])
            plat = next((p for p in ("mintlify", "readme.io", "docusaurus", "gitbook", "redocly", "docsify", "sphinx") if p in r["text"].lower()), "")
            out.append({"url": r["url"], "score": n, "hits": hits, "usable": n >= 3, "platform": plat, "bytes": len(r["text"])})
    return out


def one(domain):
    t0 = time.time()
    row = {"domain": domain, "llms": rung_llms(domain), "sitemap": rung_sitemap(domain), "nav": rung_nav(domain)}
    # ladder: llms -> sitemap -> nav, verify as we go, stop at first usable
    winner, rung = None, None
    order = [("llms", [c for e in row["llms"] for c in e["candidates"]]),
             ("sitemap", [c for e in row["sitemap"] for c in e["candidates"]]),
             ("nav", [c for e in row["nav"] for c in e["candidates"]])]
    for name, urls in order:
        v = verify(urls)
        good = next((x for x in v if x["usable"]), None)
        row[f"verify_{name}"] = v
        if good:
            winner, rung = good["url"], name
            break
    row["winner"], row["rung"] = winner, rung
    if not winner:
        row["wellknown"] = rung_wellknown(domain)
        g = rung_guess(domain)
        row["guess"] = g
        good = next((x for x in g if x["score"] >= 3), None)
        if good:
            row["winner"], row["rung"] = good["url"], "path-guess"
        elif row["wellknown"]:
            row["rung"] = "wellknown-only"
    else:
        row["wellknown"] = rung_wellknown(domain)
    row["seconds"] = round(time.time() - t0, 1)
    print(f"{domain:18s} rung={row['rung'] or 'MISS':16s} winner={row['winner'] or '-'}", flush=True)
    return row


if __name__ == "__main__":
    args = sys.argv[1:]
    out = "/tmp/oauth_find_probe.json"
    if "--out" in args:
        i = args.index("--out"); out = args[i + 1]; del args[i:i + 2]
    DOMAINS = args or DEFAULT_DOMAINS
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(one, DOMAINS):
            results.append(r)
    summary = {}
    for r in results:
        summary[r["rung"] or "MISS"] = summary.get(r["rung"] or "MISS", 0) + 1
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print("wall seconds:", round(time.time() - t0, 1))
    with open(out, "w") as f:
        json.dump(results, f, indent=1)
    print("wrote", out)
