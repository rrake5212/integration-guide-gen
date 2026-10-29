# Integration setup guide generator

Status: **LIVE since 2026-10-28 — https://integration-guide.100.31.120.181.sslip.io** (P1–P6 built 2026-10-27;
P7 deployed 2026-10-28) — resolver, error taxonomy, `provider.spec` schema + validator, input/SSRF guard,
DeepInfra client, research + quoted extraction + assembly, time/steps/blockers, dual renderers, FastAPI
web UI, verifier + golden set + error paths + debug/replay; 177 tests. Repo: private,
`github.com/rrake5212/integration-guide-gen`. Deploy: `Dockerfile` +
`docker-compose.yml` + `deploy/Caddyfile` + one-command `deploy/deploy.sh` (see `deploy/README.md`);
running as the `guidegen` container behind Caddy on the Lightsail box. Remaining P7: private
`/admin/jobs/<id>`, 7-day cache table + badge.
FIND layer 2026-10-29: catalogue routing for 1,688 names, the ranked discovery ladder, the provider
entry store, and Serper search behind a 7-day result cache + daily cap (`tools/find_audit.py`:
9/30 canonical, 24/30 usable, vs 6/30 and 14/30 on the free rungs alone — PLAN §13.7). Next: wire
`surfaces.json` into generation so a catalogue-only provider can produce a guide.

Given a free-text app name or website (`"tally.so"`, `"google sheets"`, `"tally"`, `"acme.com"`), resolve it to
the OAuth provider that owns the app registration, and say what kind of setup is actually possible.

P1 covers resolution; P2 adds the canonical fact model and its validator. No guide rendering, LLM extraction or
time estimates yet.

**Product surface:** a live website, not the CLI. The CLI here is the internal development/probe entry point;
the site (planned, `PLAN.md` §6.1) wraps the same pipeline with a resolve box, async generation jobs, SSE
progress, and three result tabs — human guide, `agent.json`, `agent.md`. Hosting: the existing Lightsail
box behind Caddy on `integration-guide.100.31.120.181.sslip.io` (container on loopback, TLS at the edge).
Testing-only: shared with a few friends, no accounts or rate-limit machinery.

## Run

```
cd ~/AgentWork/integration-guide-gen

python3 -m guidegen "tally.so"
python3 -m guidegen "google sheets"
python3 -m guidegen "tally"                  # ambiguous -> asks, exit 2
python3 -m guidegen "tally" --pick tally
python3 -m guidegen "example.com" --no-network
python3 -m guidegen "paypal.com"             # live OIDC probe -> oidc_only
python3 -m guidegen "tally" --json

python3 -m guidegen.spec tests/fixtures/example-oauth.spec.json   # VALID / issues
python3 -m guidegen.verify --golden data/golden                   # golden set
python3 -m guidegen.verify data/jobs/job_1f3d598a0c               # one job
python3 -m guidegen.debug job_1f3d598a0c                          # timeline + state
python3 -m guidegen.replay job_1f3d598a0c --from-cache            # deterministic re-render
python3 -m guidegen.build "github"                                # live: docs -> spec + guide + agent files
python3 -m guidegen.build "google" --max-chunks 8 --json

# web UI (deps are isolated in .venv)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
LLM_MODEL=deepseek-ai/DeepSeek-V4-Flash-0731 .venv/bin/uvicorn guidegen.web.app:app --port 8765
# then open http://127.0.0.1:8765
```

Exit codes: `0` resolved · `2` ambiguous (ask) · `3` unknown · `4` invalid input; `guidegen.build` adds `5` build failed.

Web surface (FastAPI):

```
GET  /                          landing
POST /api/resolve               {input} -> resolved | ask + candidates | unknown
POST /api/guides                {input, pick?} -> {job_id} (202) or 409 candidates
GET  /api/guides/{job_id}       status, stage, progress, links
GET  /api/guides/{job_id}/events SSE progress
GET  /g/{job_id}                result page (Human guide | agent.json | agent.md)
GET  /g/{job_id}/guide.md       download
GET  /g/{job_id}/agent.json     download
GET  /g/{job_id}/agent.md       download
GET  /api/health                queue depth + running jobs
```

Jobs are SQLite-backed and run on two worker threads; job ids are unlisted UUIDs. A job exceeding
`GUIDEGEN_JOB_TIMEOUT_SECONDS` (default 180) fails with `JOB_TIMEOUT`. The 7-day cache badge is not wired yet
(P7).

## Finding the auth surface (FIND layer, 2026-10-29)

Reliable discovery is not one crawler; it is a ranked ladder plus a durable entry that a human can
freeze. Every candidate is verified by content, not by a 200 status.

```
python3 -m guidegen find "linear"                 # catalogue route -> ladder -> ranked surfaces
python3 -m guidegen find "zendesk" --save         # store the proposal in guidegen/data/surfaces.json
python3 -m guidegen batch "asana" "notion"            # or --file names.txt: find + save, one line each
python3 -m guidegen review --limit 20             # what still needs a human
python3 -m guidegen confirm linear 1              # freeze proposal #1 as the answer
python3 -m guidegen find "hubspot" --no-network    # route + would-fetch, no requests
```

Search is optional and used only for the residual (after llms.txt, sitemap, nav, path guesses and
well-known all fail). It sits behind a `SearchProvider` protocol, so the vendor is a config choice:

```
SEARCH_PROVIDER=serper  SERPER_API_KEY=...    # 2,500 free queries; 50k pack $1.00/1k
SEARCH_PROVIDER=brave   BRAVE_API_KEY=...     # $5.00/1k requests, $5 free monthly
# no key -> NullSearch; the free rungs stand alone
```

`guidegen/data/surfaces.json` is the durable answer: `surfaces[]` (accepted), `proposals[]` (machine),
`status`, `accepted_by` (`human`/`auto`), `accepted_at`, and the finder's own `find{}` record. A human
confirm is never overwritten by a later auto result. **An auto-resolved entry is usable but still sits
in `review`** until a person confirms it — usable is not ground truth (gate E). Two credible surfaces of
different kinds (`connect_oauth` vs `app_marketplace`) come back as `needs_review` with both — the same
rule as the name resolver: stop and ask, never guess.

## Tests

```
python3 -m unittest discover -s tests -v
```

9 tests: domain input, bare-name ambiguity, qualified name, toolkit name, full URL, unknown domain, fuzzy typo,
unrelated name, registry integrity. 5 taxonomy tests (completeness, no internals in user text, payload shape,
unknown code, error carries code). 21 spec tests: schema validity, both fixtures, fact shape, availability ↔ auth,
time range, provenance, unknown ledger, duplicates, quality counters, sparse failed spec. 20 input tests: empty,
length, control chars, schemes, credentials, ports, IP/localhost/.local, IDNA, text, SSRF classification, CLI
exit 4. 14 pipeline tests: HTML/section/chunking, quote fabrication, URL mismatch, scopes, assembly, degraded
availability, missing key. 10 planning tests: time bands, sensitive-scope review, unknown availability, blocker
selection, step quotes/dedupe, redirect/client-secret fallbacks, complete vs partial. 4 render tests: guide/agent
consistency, scopes/verification, no key material, HTML escaping. 7 web tests: ambiguous pick, landing/health,
resolve outcomes, queued job, done page + downloads, missing job, job timeout (skipped on system Python; run with
`.venv/bin/python`). 8 verifier tests: golden assertions, quote provenance, missing value/step, duplicate source,
bad hash, secret leakage, fabricated quote. 8 error-path tests: docs 403/429, invalid model JSON, refusal field,
refusal text, ambiguous name, blocked host, unknown provider. 2 replay tests. **108 at P6.**
2026-10-29 added 36: catalogue routing and domain inference (parse_auth, availability, title-gated
inference, first-party labels), the FIND ranker (docs vs marketing, generic-overview rejection, blocked
product surfaces, keyword-only pages are not usable, surface kinds), the ladder (llms stop, two-surface
ambiguity, search only on the residual and never when the ladder won, unknown is not a guess,
well-known alone is needs_review), the search adapters (Serper/Brave parsing, key handling, env factory,
failure degradation), and the entry store (auto-accept, human confirm, human answers are never
overwritten, file shape). **162 total.**
2026-10-29 (search key live) added 15: the searchapi.io adapter, the site:-query rewrite with a
first-party filter, the reserved page budget, and `CachedSearch` (7-day result cache, daily cap,
cache-hit-after-cap, expiry, empty-is-not-cached, env wiring). **177 total.**

## Verified 2026-10-27

| input | outcome |
|---|---|
| `tally.so` | RESOLVED Tally · `api_key_only` · domain · high |
| `tally` | ASK — Tally (tally.so) vs Tally ERP (tallysolutions.com) · exit 2 |
| `Tally Forms` | RESOLVED Tally |
| `google sheets` | RESOLVED Google · alias · high |
| `https://api.github.com/user` | RESOLVED GitHub · domain · high |
| `notin` | RESOLVED Notion · fuzzy 0.91 · medium |
| `example.com` | UNKNOWN · exit 3 |
| `paypal.com` | RESOLVED via live OIDC discovery · `oidc_only` · medium |
| `tally --pick tally` | RESOLVED Tally · forced |
| `python3 -m guidegen.spec tests/fixtures/example-oauth.spec.json` | VALID · complete · 14 facts (13 verified, 1 unknown) |
| `python3 -m guidegen.spec tests/fixtures/example-apikey.spec.json` | VALID · partial · 5 facts (4 verified, 1 unknown) |

Bad queries (clean message, exit 4, no traceback): `""` → "Enter an app name or a website." · `file:///etc/passwd`
→ "Use the app's normal website address…" · `http://localhost:8080` → "That doesn't look like a website address…" ·
`https://user:pass@example.com` → "Remove the username and password…" · `https://tally.so:8443` → "…without a
custom port."

Live builds (2026-10-27, `deepseek-ai/DeepSeek-V4-Flash-0731`; V4.1 returned a 429 engine-overload during the
run):

| provider | status | verified / unknown | steps | blockers | time | spec |
|---|---|---|---|---|---|---|
| Google | complete | 12 / 2 (`pkce`, `refresh_token`) | 11 | 8 | 35 min (20–50) | `data/jobs/job_a81ee54c01/spec.json` |
| GitHub | complete | 75 / 3 (`pkce`, `consent_screen`, `discovery_url`) | 10 | 8 | 15 min (10–25) | `data/jobs/job_15d800aa7a/spec.json` |

Both pass `python3 -m guidegen.spec`.

Web end-to-end (2026-10-27): `POST /api/guides {"input":"github"}` → `job_1f3d598a0c` → complete in ~70 s ·
78 facts (75 verified / 3 unknown) · 7 steps · SSE 37 → 52 → 60 → 67 → done. Post-verifier job
`job_d6eac30c26` → complete · 76 facts (72 / 4) · 7 steps · `validation.json` `ok: true`, plus
`events.jsonl` and 3 stored docs pages.

Verifier: `python3 -m guidegen.verify --golden data/golden` → VALID for both golden specs; mutation tests
catch missing values, missing steps, duplicate sources, bad hashes, fabricated quotes and secret leakage.

Test run: `python3 -m unittest discover -s tests -v` → **177 tests, OK** (7 web tests skip without the venv;
`.venv/bin/python -m unittest discover -s tests` runs all 177).

## Layout

```
guidegen/
  registry.py      providers + toolkit-name indexes (data/ is loaded here)
  resolve.py       normalisation -> domain / alias / toolkit / fuzzy / probe; ask and unknown are outcomes
  probe.py         live OAuth/OIDC discovery (both well-known docs, root + auth hosts) + homepage (stdlib, read-only)
  errors.py        32 error codes + 4 warnings; user message / operator hint split
  input.py         query validation + SSRF host classification (host_status)
  spec.py          structural + semantic validation of a provider spec
  llm.py           DeepInfra/OpenCode client: json_schema, retries, usage, no key logging
  fetch.py         docs + discovery fetch, HTML->text, sections, chunks
  extract.py       per-chunk facts/scopes extraction + quote verification
  research.py      merge chunks, discovery > docs, conflicts, chunk stats, deterministic fallbacks
  assemble.py      deterministic complete/partial spec + quality counters
  effort.py        time bands, friction flags, review wait, confidence
  blockers.py      curated class rules + provider overrides
  steps.py         docs-mined console steps with quotes
  build.py         python3 -m guidegen.build "<query>"
  render_human.py  provider.spec.json -> guide.md
  render_agent.py  provider.spec.json -> agent.json + agent.md
  pipeline.py      shared resolve -> research -> assemble path with progress
  verify.py        schema/provenance/cross-format/secret verifier + CLI
  debug.py         timeline + state for a job directory
  replay.py        deterministic re-render + verify from spec.json
  web/
    app.py         FastAPI landing, resolve, async jobs, SSE, result, downloads
    static/        landing + result pages, CSS, JS
  schema/
    provider.spec.schema.json   canonical fact model (Draft 2020-12)
    facts_only.schema.json      extractor output, object keyed by fact path
    scopes_only.schema.json     extractor output for a scope table
    steps_only.schema.json      extractor output for ordered setup steps
  cli.py           python3 -m guidegen "..."
  data/
    providers.json 9 curated providers (google, github, slack, meta, tally, tally-erp, notion, prisma, stripe)
    toolkits.json  1,688 toolkit names/slugs from docs.composio.dev — REFERENCE VOCABULARY ONLY
tests/
  test_resolve.py
  test_errors.py
  test_spec.py
  fixtures/        build.py + example-oauth.spec.json + example-apikey.spec.json (synthetic)
scripts/
  probe_llm.py     provider probe: models -> no-key -> plain -> json_schema -> tool -> json_object
data/
  probe-deepinfra.json   probe record (no key, no headers)
```

`toolkits.json` is a name index, not a fact source. No guide fact may come from Composio; facts come from the
provider's own docs and OIDC discovery (see `PLAN.md` §3).

The extractor does not emit `provider.spec.json`. Per chunk it returns an object keyed by fact path
(`facts_only.schema.json`) or a scope table (`scopes_only.schema.json`); assembly merges them, adds env/time/
quality, and validates the result against `provider.spec.schema.json`.

## Rules encoded in P1

- A tie always stops and asks. Bare `tally` returns Tally (forms) and Tally ERP; it never guesses.
- A domain that is not in the registry is probed, not assumed: discovery metadata (OIDC or RFC 8414, root or auth host) → `oidc_only`, otherwise
  `unknown` (a reachable homepage is not proof of an OAuth surface).
- Resolution records why: `matched_by`, `confidence`, and `alternatives` are all in the JSON output.

## Errors and debugging (taxonomy live; wiring lands with P5/P6)

Every failure has one code and two renderings:

  user      plain message, Retry button when retryable, copyable job id — no model names, keys, paths, SQL,
            HTTP jargon or stack traces
  operator  code, stage, URL/status, truncated provider error, model, chunk, tokens, attempts, stack trace

The API returns only `{"error": {code, stage, message, retryable, job_id}}`. Examples:

  "We couldn't match that. Try the app's website address instead."
  "This provider blocks automated reading of its docs. Open their docs directly."
  "The guide failed our accuracy check, so we didn't publish it."
  "This guide is taking longer than expected. Check back in a minute."

A guide can be `complete`, `partial` or `failed`. Missing facts become `unknown` with a warning banner, not a
dead job; only "no auth path / no endpoints / failed accuracy gate" fails it.

Per-job debug artifacts (planned, `data/jobs/<job_id>/`): `job.json`, `events.jsonl`, `docs/<hash>.html`,
`chunks/<n>.json`, `llm/<n>-{request,response}.json`, `spec.json`, `validation.json`. Debug commands (P5/P6):

```
python3 -m guidegen debug <job_id>
python3 -m guidegen replay <job_id> --from-cache
```

`tests/test_errors.py` enforces that every code has a message and that no user message can contain an internal
term.

## Deploy (Docker + Caddy)

    cp .env.example .env                  # fill DEEPINFRA_API_KEY
    deploy/deploy.sh --bootstrap          # once: docker, swap, caddy import line
    deploy/deploy.sh                      # every deploy: sync, build, restart, reload caddy, smoke

    deploy/smoke.sh https://integration-guide.100.31.120.181.sslip.io --with-job   # verify incl. one real guide

Topology: `internet -> Caddy :443 -> 127.0.0.1:8765 -> container (uid 10001)`, artifacts in a bind mount at
`runtime/`. `.env` never enters the image and is never synced from the laptop. Full runbook, operate commands
and troubleshooting: `deploy/README.md`.

Local, without Docker (same CMD the image runs):

    .venv/bin/uvicorn guidegen.web.app:app --host 127.0.0.1 --port 8765 --no-server-header
    deploy/smoke.sh http://127.0.0.1:8765

## Next (from PLAN.md)

P6 delivered: verifier + golden set, cross-format/secret checks, forced error paths, job timeout, and
`debug`/`replay` (108 tests at P6, 177 now). P7 **deployed 2026-10-28** — image built on the box, container healthy, Caddy +
Let's Encrypt live at `integration-guide.100.31.120.181.sslip.io`, smoke 6/6 loopback and 6/6 public, one
real GitHub guide end to end.

## Known issues / open items

- `tests/test_web.py` writes its `job_timeout` fixture into the **live** `data/jobs/`, not a temp dir —
  running the suite rewrites `data/jobs/job_timeout/{job.json,events.jsonl}`. Harmless, pollutes the
  job store; should use a tmp path.
- Job retention: nothing prunes `data/jobs` (23 jobs / 11 MB locally; the box grows under `runtime/`).
- Chunk/prompt request-response artifacts and failed-job docs are not persisted yet (a failed job dir
  holds only `job.json` + `events.jsonl`).
- No rate limiting or token budget — deliberate for a testing-only site; add before widening access.
- Three human reads (OAuth provider, API-key provider, review-heavy provider) still pending; the golden
  GitHub and Google guides are the stand-ins.
- `auth.oauth.pkce` / `client_secret_required` / `refresh_token` are single booleans, so providers that state
  them conditionally ("PKCE mandatory for public clients, optional for confidential" — Prisma) report `unknown`
  rather than an inference. A conditional value (`required_for_public_clients`) is the schema change that fixes it.
- Assembly picks ONE auth method set from `availability`: a `self_serve_oauth` provider gets an OAuth guide and
  the `auth.api_key.*` facts are never requested. Providers that genuinely offer both (Stripe keys vs Connect
  OAuth, Prisma service tokens vs OAuth) therefore show only the OAuth path.
- **Routing is curation-gated — FIXED 2026-10-29.** `catalog.py` routes any of the 1,688 catalogue
  names to a canonical name + auth families, offline (gate A), and `find.py` locates the docs. `linear`,
  `hubspot`, `airtable`, `asana`, `zendesk`, `salesforce` no longer return "unknown because uncurated".
  What remains is coverage: a provider whose portal is JS-rendered and whose index has no usable
  llms/sitemap needs the search fallback, and no search key is configured yet.
- **The discovery probe is a coin flip.** Measured 2026-10-29: `airtable.com` and `prisma.io` publish
  `.well-known` metadata (resolved, `oidc_only`); `stripe.com`, `linear.app`, `hubspot.com`, `asana.com`,
  `zendesk.com` publish none. The probe is now corroboration only, never the ladder's only bet.
- **`surfaces.json` is not wired into generation yet.** The web pipeline still reads `provider.docs`;
  a catalogue-only provider can be found but not yet guided. Next step is constructing a provider from
  a catalogue entry + confirmed surface and feeding it to `research.py`.

Remaining in P7: private `/admin/jobs/<id>`, 7-day page-hash cache table + badge.
P8 optional: MCP wrapper + the calibration run that replaces the `low` time confidence.
