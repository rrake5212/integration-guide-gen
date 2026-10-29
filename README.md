# Integration setup guide generator

A tool that turns an app name or website into a working setup guide for its OAuth or API-key
integration — one guide a person follows, one machine brief an agent executes.

**Live:** https://integration-guide.100.31.120.181.sslip.io · **Repo:** `rrake5212/integration-guide-gen` (public)

---

## What it does

You give it a name — `"github"`, `"tally.so"`, `"google sheets"`, `"acme.com"` — and it produces:

| Output | For | Contents |
|---|---|---|
| `guide.md` | a developer | time and prerequisites, numbered console steps, a verification check, auth details, scopes, a blocker playbook, what could not be verified, sources |
| `agent.json` | the developer's agent | the same facts as flat fields plus `provenance` (fact path → source URL), env var names, order of operations, `must_ask_human`, `never`, failure signatures |
| `agent.md` | pasting into Claude Code / Cursor | a 20–30 line render of the same brief |
| `provider.spec.json` | the verifier and any consumer | the canonical fact model both outputs are rendered from |

Every guide opens with the verdict that decides the reader's day:

```
self_serve_oauth   register it yourself today
approval_gated     OAuth exists, needs partner or app review
api_key_only       no third-party OAuth; a key is the path
oidc_only          OIDC without self-serve registration
no_public_api      nothing to integrate
```

## How it works

```
input ─► resolve ─► find ─► research ─► extract ─► assemble ─► render ─► verify
```

**resolve** — normalise the input (URL, bare domain, display name, product name), then match
domain → exact alias → product/catalogue name → fuzzy → live probe. Three outcomes only:
`resolved`, `ask` (a tie, with candidates), `unknown`. The decision is recorded in the spec as
`resolution.matched_by`, `confidence` and `alternatives`.

**find** — locate the page that actually documents app registration. A ranked ladder over free
signals (`llms.txt`, sitemap, nav links, `.well-known/openid-configuration` and
`.well-known/oauth-authorization-server`, path guesses), each candidate gated on content rather than
HTTP status, then a search fallback on the residual. The winner is persisted as a provider entry.

**research** — fetch the located docs and any discovery metadata, convert to text, select sections by
heading vocabulary (oauth, scopes, redirect, credentials, permissions, verify), chunk at ~6k chars.

**extract** — per chunk, the model fills an object keyed by fact path; every value carries a verbatim
quote. Scopes and setup steps run as separate passes. A fact the page does not state stays `null`.

**assemble** — merge chunks deterministically, resolve conflicts by source hierarchy
(discovery > provider docs > troubleshooting FAQ), add time, steps, blockers, env names and quality
counters, then validate against the schema.

**render** — one spec, two projections.

**verify** — schema and semantic checks, quote-to-page verification, cross-format consistency both
ways, duplicate-source and secret-pattern scans. A failure fails the job; nothing is served.

## Quick start

```
git clone git@github.com:rrake5212/integration-guide-gen.git
cd integration-guide-gen
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # set DEEPINFRA_API_KEY; SERPER_API_KEY optional
.venv/bin/uvicorn guidegen.web.app:app --port 8765
```

Open http://127.0.0.1:8765, type an app name, watch the job, read the three tabs.

### From a zip or a fresh clone

No key is needed to resolve, find docs, validate a spec, verify the golden set or replay a stored job:

```
python3 -m guidegen "tally.so"                      # resolve: provider + availability verdict
python3 -m guidegen find "linear" --no-network      # route from the catalogue, no fetch
python3 -m guidegen.spec data/golden/github/spec.json
python3 -m guidegen.verify --golden data/golden
```

A key is needed for anything that reads docs and writes a guide — `guidegen.build`, and the site's
generate button. Without one the job fails cleanly as `LLM_NOT_CONFIGURED` rather than hanging.

Two things a zip does not contain, because they are gitignored: `data/jobs/` and `data/cache/`. The
first is why the test suite is not green from a clean checkout — see Tests.

## Command line

```
python3 -m guidegen "tally.so"                  # resolve: provider + availability verdict
python3 -m guidegen "tally"                     # ambiguous -> candidates, exit 2
python3 -m guidegen "tally" --pick tally        # force a choice
python3 -m guidegen "example.com" --no-network  # offline resolution only
python3 -m guidegen "tally" --json              # machine-readable

python3 -m guidegen find "linear"               # locate the setup docs, ranked
python3 -m guidegen find "zendesk" --save       # store the proposal
python3 -m guidegen batch "asana" "notion"      # find + save, one line each
python3 -m guidegen review --limit 20           # what still needs a human
python3 -m guidegen confirm linear 1            # freeze proposal #1

python3 -m guidegen.build "github"              # full run: docs -> spec -> guide + agent files
python3 -m guidegen.spec data/golden/github/spec.json   # VALID / issues
python3 -m guidegen.verify --golden data/golden         # publish gate over the golden set
python3 -m guidegen.debug <job_id>              # job timeline and state
python3 -m guidegen.replay <job_id> --from-cache        # deterministic re-render
```

Exit codes: `0` resolved · `2` ambiguous · `3` unknown · `4` invalid input · `5` build failed.

## HTTP API

| Route | Purpose |
|---|---|
| `GET /` | landing page |
| `POST /api/resolve` | `{input}` → resolved / ask + candidates / unknown |
| `POST /api/guides` | `{input, pick?}` → `{job_id}` (202); 409 on candidates, 422 when no auth surface |
| `GET /api/guides/{job_id}` | status, stage, progress, links |
| `GET /api/guides/{job_id}/events` | SSE progress stream |
| `GET /g/{job_id}` | result page — human guide / agent.json / agent.md tabs |
| `GET /g/{job_id}/guide.md` | download |
| `GET /g/{job_id}/agent.json` · `agent.md` | download |
| `GET /api/health` | queue depth and running jobs |

Jobs are SQLite-backed on two worker threads, ids are unlisted UUIDs, and the per-job budget is
`GUIDEGEN_JOB_TIMEOUT_SECONDS` (default 300 — FIND runs inside the job).

## The fact model

`provider.spec.json` is the source of truth. Top level:

```
schema_version  generator{name,version,model,prompt_version}  generated_at  status
provider    {id, name, domains, docs, console, discovery, note}
resolution  {input, normalized, kind, decision, matched_by, confidence,
             availability, alternatives, notes}
auth        {methods, primary, oauth{…}, api_key{…}}
setup       {time{likely_minutes, range_min_minutes, range_max_minutes,
                  review_wait_days, drivers, confidence},
             prerequisites, steps[{order,title,detail,url}],
             verification[{order,method,command,expected}],
             blockers[{id,symptom,cause,fix,severity,likelihood}]}
env         [{name, description, required, secret}]
sources     [{url, title, fetched_at, http_status, doc_hash}]
quality     {facts_total, facts_verified, unknown_count, confidence}
unknown_fields  warnings  notes
```

Every *fact* — an endpoint, a scope, a boolean, a redirect rule — is stored as a triple:

```json
{ "value": "https://github.com/login/oauth/authorize",
  "quote": "GET https://github.com/login/oauth/authorize",
  "source_url": "https://docs.github.com/…/authorizing-oauth-apps" }
```

A fact the docs do not state is `null` with an entry in `unknown_fields`. `status` is `complete`,
`partial` or `failed`; a partial guide renders with a visible warning rather than dying.

`agent.json` is the same model flattened for execution — plain values instead of triples, plus
`provenance` mapping every fact path to the URL it came from.

## Configuration

| Variable | Purpose |
|---|---|
| `DEEPINFRA_API_KEY` | model access (required) |
| `LLM_PROVIDER`, `LLM_MODEL`, `DEEPINFRA_BASE_URL` | model selection; one OpenAI-compatible client |
| `SEARCH_PROVIDER`, `SERPER_API_KEY` | optional search rung — `serper`, `brave` or `searchapi`; no key means the free rungs stand alone |
| `GUIDEGEN_SURFACES` | writable provider-entry store (`/app/data/surfaces.json` in the container) |
| `GUIDEGEN_JOB_TIMEOUT_SECONDS` | per-job budget, default 300 |

The model key never reaches the browser, never enters the image, and is never logged.

## Project layout

```
guidegen/
  resolve.py probe.py registry.py   input -> provider; discovery metadata; 9 curated providers
  catalog.py find.py search.py      catalogue router (1,688 names); ranked docs ladder; search fallback
  surfaces.py bridge.py             provider-entry store; catalogue entry -> Provider
  fetch.py extract.py research.py   docs fetch + chunking; quoted extraction; merge + conflicts
  spec.py assemble.py               canonical model + semantic validator; deterministic assembly
  effort.py steps.py blockers.py    time bands; docs-mined console steps; blocker playbook
  render_human.py render_agent.py   guide.md / agent.json + agent.md
  verify.py debug.py replay.py      publish gate; job timeline; deterministic re-render
  pipeline.py build.py cli.py       shared path; python3 -m guidegen.build; python3 -m guidegen
  web/                              FastAPI: landing, resolve, jobs, SSE, result, downloads
  schema/                           provider.spec + model-facing chunk schemas
  data/                             providers.json · surfaces.json · toolkits.json
tests/                              201 tests
tools/                              find_audit.py, find_benchmark.py (no keys, no spend)
deploy/                             Dockerfile, compose, Caddyfile, deploy.sh, smoke.sh
```

`data/toolkits.json` (1,688 rows) is a name index only — it routes a name to a provider. No guide
fact may come from it; facts come from the provider's own docs and published discovery metadata.

## Tests

```
.venv/bin/python -m unittest discover -s tests      # 201 tests
```

**In a clean checkout or zip, 9 of the 201 error** — they read a stored job under
`data/jobs/job_15d800aa7a/` (spec, guide, agent files, fetched docs) and that directory is gitignored,
so it exists only where a build has actually run. The nine are `test_done_page_and_downloads`,
`test_human_guide_contains_steps_sources_and_unknowns`, `test_google_guide_has_scopes_and_verification`,
`test_a_spec_with_no_confirmed_auth_method_still_renders`, `test_doc_text_is_html_escaped`,
`test_no_key_material_is_rendered`, `test_conflicting_sources_are_shown_in_the_guide`,
`test_no_conflict_section_when_sources_agree` and `test_quote_provenance_against_stored_docs`. They pass
in the development checkout and in the deployed image, where `data/jobs/` is synced. Pointing those
fixtures at `data/golden/` is the fix.

They cover resolution and input validation, the spec schema and its semantic rules, quoted extraction
and assembly, time and blocker selection, both renderers, the web surface and its error paths, the
verifier's mutation set, replay, catalogue routing, the FIND ladder and ranker, the search adapters,
the entry store and the bridge.

## Deployment

```
deploy/deploy.sh --bootstrap     # once: docker, swap, caddy import line
deploy/deploy.sh                 # sync, build, restart, reload caddy, smoke
deploy/smoke.sh https://<host> --with-job
```

Topology: `internet → Caddy :443 → 127.0.0.1:8765 → container (uid 10001)`, artifacts in a bind mount
at `runtime/`. Caddy holds the Let's Encrypt certificate and redirects `http` → `https`. Full runbook:
`deploy/README.md`.

## Status and limitations

Built and running. 201 tests pass; the golden GitHub and Google guides verify (75/78 and 12/14 facts).

- **It does not register OAuth apps or log into provider consoles.** Console steps are documented,
  not observed; approval timelines are modelled from docs with the trigger named.
- Providers documented on a third-party host (Reddit: a GitHub wiki) fail with `unknown`.
- FIND yields one page, so single-page providers produce partial guides.
- One auth method per guide: a provider offering both keys and OAuth shows only the OAuth path.
- `pkce`, `client_secret_required`, `refresh_token` are booleans, so conditional rules report `unknown`.
- No rate limiting or token budget — testing deployment; anyone with the link can spend model credits.
- Nothing prunes `data/jobs`; chunk-level request/response artifacts are not persisted.

The working document — decision gates, the routing redesign, measured audits, and each correction with
its evidence — is `PLAN.md`.
