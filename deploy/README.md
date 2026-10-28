# GuideGen deploy — Docker container + Caddy TLS on Lightsail

    internet -> Caddy :443  (Let's Encrypt, HTTPS, gzip)
             -> 127.0.0.1:8765  (loopback only, never public)
             -> guidegen container (uvicorn, non-root uid 10001)
             -> /app/data  <- bind mount -> /home/ubuntu/guidegen/runtime  (SQLite + job artifacts)

Two moving parts, one box. Caddy is already there (it serves `journey-builder-mcp`
on `100.31.120.181.sslip.io`); GuideGen is a second site block, not a replacement.

## Facts this runbook assumes

| | |
|---|---|
| Instance | Lightsail `journey-builder-mcp`, us-east-1a, Ubuntu 24.04, micro_3_0 (1 GB RAM) |
| IP | 100.31.120.181 (public IPv4 included in the Lightsail flat rate) |
| Hostname | `integration-guide.100.31.120.181.sslip.io` — sslip.io resolves it to the IP, so no DNS record |
| Ports | 443 open (Lightsail firewall + ufw). 8765 is **not** opened publicly |
| RAM | shared with journey-builder-mcp (~60 MB) — the container is capped at 512 MB |
| Key material | `.env` stays on the box, never synced from the laptop |

## One-time

    cp .env.example .env                 # on the laptop, fill DEEPINFRA_API_KEY
    export GUIDEGEN_SSH_KEY=~/.ssh/lightsail-default.pem     # the key that already reaches the box

    # 1. server prep: docker + compose plugin, swapfile, caddy import line
    deploy/deploy.sh --bootstrap

    # 2. put .env on the box (once; never overwritten by later deploys)
    ssh -i "$GUIDEGEN_SSH_KEY" ubuntu@100.31.120.181 "cat > ~/guidegen/.env" < .env
    ssh -i "$GUIDEGEN_SSH_KEY" ubuntu@100.31.120.181 "chmod 600 ~/guidegen/.env"

## Every deploy

    deploy/deploy.sh

It rsyncs the source (excluding `.env`, `runtime/`, `.venv`), builds the image on the box,
recreates the container, installs/validates the Caddy vhost, reloads Caddy, then runs
the smoke test twice — once against `http://127.0.0.1:8765` **on the box**, once against
the public `https://` URL.

    deploy/deploy.sh --no-caddy     # app only
    deploy/deploy.sh --bootstrap    # include the one-time prep (idempotent)

## Verify

    deploy/smoke.sh https://integration-guide.100.31.120.181.sslip.io --with-job

Free checks (default) prove: health, UI shell, resolver happy path, the ambiguity STOP,
the 409 refusal on `/api/guides`, and clean `JOB_NOT_FOUND`. `--with-job` additionally
runs one real GitHub guide end to end (~60 s, ≈$0.01 DeepInfra) and asserts the three
downloads plus SSE frames.

Test suite inside the built image, with no volume and no network:

    ssh ... "cd ~/guidegen && sudo docker run --rm guidegen:local python -m unittest discover -s tests"
    ssh ... "cd ~/guidegen && sudo docker run --rm guidegen:local python -m guidegen.verify --golden data/golden"

(`python -m guidegen.verify` on the host against `data/golden` will fail: the bind mount
replaces `/app/data`, so the golden specs live only in the image.)

## Operate

    ssh ... "cd ~/guidegen && sudo docker compose ps"             # health, uptime
    ssh ... "cd ~/guidegen && sudo docker compose logs -f app"    # stage/job log
    ssh ... "cd ~/guidegen && sudo docker compose restart app"
    ssh ... "cd ~/guidegen && sudo docker compose down"           # stop (Caddy then 502s)

Per-job inspection happens on the host, no docker exec needed:

    ssh ... "cd ~/guidegen && python3 -m guidegen.debug job_d6eac30c26"     # needs the venv on the box
    ls ~/guidegen/runtime/jobs/<job>/   # job.json, events.jsonl, spec.json, guide.md, agent.*, validation.json

Rollback: the image is tagged `guidegen:local`, so rollback means rebuilding the previous
source. There is no registry — keep a copy of the last-good tree (`rsync` it aside first).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `502` from Caddy, container `unhealthy` | `docker compose logs app`; usually a missing `.env` var or an import error — the image's `CMD` exits and `restart: unless-stopped` loops |
| `permission denied` writing `/app/data/guidegen.db` | bind-mount ownership: `sudo chown -R 10001:10001 ~/guidegen/runtime` |
| SSE progress bar freezes until the end | something is buffering the stream: check the `@sse` branch still has `flush_interval -1` and no `encode` |
| cert not issued on first request | Let's Encrypt needs :80 reachable; check `sudo journalctl -u caddy -n 50`, and that sslip.io resolves to 100.31.120.181 |
| `caddy validate` fails on reload | the site block and the existing journey-builder block are both in the validated config — a parse error anywhere takes reload down, so fix before `reload` |
| box goes unresponsive during build | 1 GB RAM: the swapfile from `bootstrap.sh` is what keeps journey-builder alive; build with `--no-cache` off a quiet box if needed |

## Security posture

- The app port is published on `127.0.0.1` only; Caddy is the sole public entry.
- Container runs as uid 10001, `cap_drop: ALL`, `no-new-privileges`, 512 MB memory cap.
- `.env` is never in the image (`dockerignore`), never synced by `deploy.sh`, `chmod 600` on the box.
- Provider docs are fetched server-side; user input is validated and SSRF-guarded (`guidegen/input.py`).
- No accounts, no rate limiting in v0 — this is a testing site for a few people. Job URLs are
  unlisted UUIDs. Do not open it up without adding limits first.
