#!/usr/bin/env bash
# Smoke test for a running GuideGen instance.
#
#   deploy/smoke.sh                                   http://127.0.0.1:8765
#   deploy/smoke.sh https://integration-guide.100.31.120.181.sslip.io
#   deploy/smoke.sh https://... --with-job             also generate one real guide
#
# Default checks cost nothing (no model call). --with-job runs the full pipeline
# once: ~60 s and ≈$0.01 of DeepInfra inference.
set -uo pipefail

BASE="http://127.0.0.1:8765"
WITH_JOB=0
for arg in "$@"; do
  case "$arg" in
    --with-job) WITH_JOB=1 ;;
    http*) BASE="$arg" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
BASE="${BASE%/}"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAILED=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILED=$((FAILED + 1)); }

# code <curl args...> — writes body to $TMP/body, echoes the status code
code() { curl -sS --max-time 20 -o "$TMP/body" -w '%{http_code}' "$@"; }
jget() { python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],""))' "$TMP/body" "$1"; }
check_json() { EXPR="$1" python3 - "$TMP/body" <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print("yes" if eval(os.environ["EXPR"], {"d": d, "json": json}) else "no")
PY
}

echo "smoke: $BASE"

# 1. liveness
s=$(code "$BASE/api/health")
if [ "$s" = 200 ] && [ "$(check_json 'd.get("ok") is True')" = yes ]; then
  pass "GET /api/health -> ok:true (queue $(jget queue_depth))"
else
  fail "GET /api/health -> $s"
fi

# 2. UI shell
s=$(code "$BASE/")
if [ "$s" = 200 ] && grep -qi '<html' "$TMP/body"; then pass "GET / -> 200 html"; else fail "GET / -> $s"; fi

# 3. resolver, domain input
s=$(code -X POST -H 'content-type: application/json' -d '{"input":"tally.so"}' "$BASE/api/resolve")
if [ "$s" = 200 ] && [ "$(check_json 'd.get("decision")=="resolved" and (d.get("provider") or {}).get("id")=="tally" and d.get("availability")=="api_key_only"')" = yes ]; then
  pass "POST /api/resolve tally.so -> Tally, api_key_only"
else
  fail "POST /api/resolve tally.so -> $s"
fi

# 4. resolver, ambiguous name must STOP and offer candidates
s=$(code -X POST -H 'content-type: application/json' -d '{"input":"tally"}' "$BASE/api/resolve")
if [ "$s" = 200 ] && [ "$(check_json 'd.get("decision")=="ask" and len(d.get("candidates") or [])>=2')" = yes ]; then
  pass "POST /api/resolve tally -> ask + candidates"
else
  fail "POST /api/resolve tally -> $s (ambiguity must not auto-pick)"
fi

# 5. same ambiguity over the job endpoint must be rejected, not guessed
s=$(code -X POST -H 'content-type: application/json' -d '{"input":"tally"}' "$BASE/api/guides")
if [ "$s" = 409 ]; then pass "POST /api/guides tally -> 409 (refuses to guess)"; else fail "POST /api/guides tally -> $s (expected 409)"; fi

# 6. unknown job id is a clean 404-class error, not a crash
s=$(code "$BASE/api/guides/job_doesnotexist")
if [ "$(check_json '"JOB_NOT_FOUND" in json.dumps(d)')" = yes ]; then pass "GET /api/guides/<bogus> -> JOB_NOT_FOUND ($s)"; else fail "GET /api/guides/<bogus> -> $s"; fi

# 7. optional: one real guide end to end
if [ "$WITH_JOB" = 1 ]; then
  echo "  ....  generating one real guide (github), this takes ~60 s"
  s=$(code -X POST -H 'content-type: application/json' -d '{"input":"github"}' "$BASE/api/guides")
  job=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("job_id",""))' "$TMP/body" 2>/dev/null)
  if [ "$s" = 202 ] && [ -n "$job" ]; then
    pass "POST /api/guides github -> $job"
    status=""
    for _ in $(seq 1 80); do
      code "$BASE/api/guides/$job" >/dev/null
      status=$(jget status)
      if [ "$status" = "done" ] || [ "$status" = "failed" ]; then break; fi
      sleep 3
    done
    if [ "$status" = "done" ]; then pass "job $job -> done"; else fail "job $job -> $status"; fi

    if [ "$status" = "done" ]; then
      s=$(code "$BASE/g/$job/agent.json")
      if [ "$s" = 200 ] && [ "$(check_json 'bool(d.get("schema_version")) and bool((d.get("provider") or {}).get("name"))')" = yes ]; then
        pass "GET /g/$job/agent.json -> schema_version + provider"
      else
        fail "GET /g/$job/agent.json -> $s"
      fi
      s=$(code "$BASE/g/$job/guide.md")
      if [ "$s" = 200 ] && [ -s "$TMP/body" ] && grep -q '^#' "$TMP/body"; then
        pass "GET /g/$job/guide.md -> markdown ($(wc -c <"$TMP/body") bytes)"
      else
        fail "GET /g/$job/guide.md -> $s"
      fi
      # SSE: one data frame for a finished job, then close
      if curl -sS -N --max-time 8 "$BASE/api/guides/$job/events" | grep -q '^data: '; then
        pass "GET /g/$job/events -> SSE frames flow"
      else
        fail "GET /g/$job/events -> no SSE frames (buffering?)"
      fi
    fi
  else
    fail "POST /api/guides github -> $s"
  fi
fi

echo
if [ "$FAILED" -eq 0 ]; then echo "SMOKE OK ($BASE)"; else echo "SMOKE FAILED: $FAILED check(s) ($BASE)"; exit 1; fi
