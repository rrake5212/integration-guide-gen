#!/usr/bin/env python3
"""Probe an OpenAI-compatible LLM provider and record which structured-output rung works.

Reads the key from .env / the environment. Never prints or records the key, headers, or the
Authorization value. Records only status codes, timings, usage and schema-validity.

Usage:
    python3 scripts/probe_llm.py                    # DeepInfra from .env
    python3 scripts/probe_llm.py --provider opencode
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = "oauth-guide-gen-probe/0.1"

FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "model": {"type": "string"},
        "rung": {"type": "integer"},
    },
    "required": ["ok", "model", "rung"],
    "additionalProperties": False,
}


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def request(url: str, key: str | None, payload: dict | None = None, timeout: int = 90) -> tuple[int, str, int]:
    """Return (status, body_text, elapsed_ms). Never include the key in anything returned."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", UA)
    if key:
        req.add_header("Authorization", "Bearer " + key)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body, int((time.monotonic() - started) * 1000)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body, int((time.monotonic() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        return 0, f"{type(exc).__name__}: {exc}", int((time.monotonic() - started) * 1000)


def validate(obj: object) -> tuple[bool, str | None]:
    try:
        import jsonschema

        jsonschema.validate(obj, FACTS_SCHEMA)
        return True, None
    except ImportError:
        if isinstance(obj, dict) and set(obj) == {"ok", "model", "rung"}:
            return isinstance(obj["ok"], bool) and isinstance(obj["model"], str) and isinstance(obj["rung"], int), None
        return False, "shape mismatch (jsonschema not installed)"
    except Exception as exc:  # noqa: BLE001 - validation error text is the result
        return False, str(exc)[:200]


def parse_json_content(body: str) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None, "response is not JSON"
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, "no choices[0].message.content"
    if not content:
        return None, "empty content"
    try:
        return json.loads(content), None
    except Exception:  # noqa: BLE001
        return None, f"content is not JSON: {content[:120]!r}"


def usage_of(body: str) -> dict | None:
    try:
        return json.loads(body).get("usage")
    except Exception:  # noqa: BLE001
        return None


def record(attempts: list[dict], name: str, status: int, body: str, ms: int, extra: dict | None = None) -> None:
    entry = {
        "name": name,
        "http": status,
        "ok": 200 <= status < 300,
        "ms": ms,
        "usage": usage_of(body),
        "error": None if 200 <= status < 300 else body[:300],
    }
    if extra:
        entry.update(extra)
    attempts.append(entry)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="deepinfra", choices=["deepinfra", "opencode"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--record", default=None)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    load_env()

    if args.provider == "deepinfra":
        base = args.base_url or os.environ.get("DEEPINFRA_BASE_URL", "https://api.deepinfra.com/v1/openai")
        key = os.environ.get("DEEPINFRA_API_KEY", "")
        model = args.model or os.environ.get("LLM_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash-1017")
        session = None
        record_path = ROOT / "data" / "probe-deepinfra.json"
    else:
        base = args.base_url or os.environ.get("OPENCODE_BASE_URL", "https://opencode.ai/inference/go")
        key = os.environ.get("OPENCODE_API_KEY", "")
        model = args.model or os.environ.get("LLM_MODEL", "claude-haiku-5-5")
        session = "ses_" + os.urandom(12).hex()
        record_path = ROOT / "data" / "probe-opencode.json"
    if args.record:
        record_path = Path(args.record)

    if not key:
        print(f"missing key for {args.provider} (.env / environment)", file=sys.stderr)
        return 1

    chat_url = base.rstrip("/") + "/chat/completions"
    models_url = base.rstrip("/") + "/models"
    attempts: list[dict] = []

    # 0. model catalogue
    status, body, ms = request(models_url, key, timeout=30)
    model_found = False
    context_length = None
    pricing = None
    if 200 <= status < 300:
        try:
            catalogue = json.loads(body)
            for item in catalogue.get("data", []):
                if item.get("id") == model:
                    model_found = True
                    meta = item.get("metadata") or {}
                    context_length = meta.get("context_length")
                    pricing = meta.get("pricing")
                    break
        except Exception:  # noqa: BLE001
            pass
    record(attempts, "models", status, body, ms, {"model": model, "model_found": model_found,
                                                 "context_length": context_length, "pricing": pricing})

    # 1. route exists without a key (401 expected)
    status, body, ms = request(chat_url, None, {"model": model, "messages": [{"role": "user", "content": "ping"}],
                                                "max_tokens": 5}, timeout=30)
    record(attempts, "no_key", status, body, ms)

    # 2. key + endpoint + model
    status, body, ms = request(chat_url, key, {"model": model, "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
                                               "max_tokens": 20, "temperature": 0}, timeout=args.timeout)
    pong = ""
    if 200 <= status < 300:
        try:
            pong = json.loads(body)["choices"][0]["message"]["content"] or ""
        except Exception:  # noqa: BLE001
            pass
    record(attempts, "plain", status, body, ms, {"content": pong[:80]})
    if not (200 <= status < 300):
        # Key/endpoint failure: nothing else can work.
        write_record(record_path, args.provider, base, model, attempts, None, False, False)
        print_summary(attempts, None, False)
        return 2

    def call_json_schema() -> tuple[bool, str | None, dict]:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content":
                          f"Return a JSON object matching the schema: ok=true, model={model!r}, rung=1."}],
            "max_tokens": 200,
            "temperature": 0,
            "response_format": {"type": "json_schema", "json_schema": {"name": "probe_facts", "strict": True,
                                                                       "schema": FACTS_SCHEMA}},
        }
        st, bd, elapsed = request(chat_url, key, payload, timeout=args.timeout)
        obj = None
        err = None
        if 200 <= st < 300:
            obj, err = parse_json_content(bd)
        valid, verr = (validate(obj) if obj is not None else (False, err or "no object"))
        record(attempts, "json_schema", st, bd, elapsed, {"rung": 1, "schema_valid": valid,
                                                          "validation_error": verr if not valid else None})
        return valid, verr, {"ms": elapsed}

    def call_tool() -> tuple[bool, str | None, dict]:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content":
                          f"Emit the probe facts with ok=true, model={model!r}, rung=2 using the emit_facts tool."}],
            "max_tokens": 200,
            "temperature": 0,
            "tools": [{"type": "function", "function": {"name": "emit_facts",
                                                        "description": "Emit the probe facts",
                                                        "parameters": FACTS_SCHEMA}}],
            "tool_choice": {"type": "function", "function": {"name": "emit_facts"}},
        }
        st, bd, elapsed = request(chat_url, key, payload, timeout=args.timeout)
        valid = False
        verr = None
        if 200 <= st < 300:
            try:
                call = json.loads(bd)["choices"][0]["message"]["tool_calls"][0]["function"]
                obj = json.loads(call["arguments"])
                valid, verr = validate(obj)
            except Exception as exc:  # noqa: BLE001
                verr = f"tool call parse failed: {exc}"[:200]
        record(attempts, "tool_call", st, bd, elapsed, {"rung": 2, "schema_valid": valid,
                                                        "validation_error": verr if not valid else None})
        return valid, verr, {"ms": elapsed}

    def call_json_object() -> tuple[bool, str | None, dict]:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content":
                          f"Return only a JSON object with ok=true, model={model!r}, rung=5."}],
            "max_tokens": 200,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        st, bd, elapsed = request(chat_url, key, payload, timeout=args.timeout)
        obj = None
        err = None
        if 200 <= st < 300:
            obj, err = parse_json_content(bd)
        valid, verr = (validate(obj) if obj is not None else (False, err or "no object"))
        record(attempts, "json_object", st, bd, elapsed, {"rung": 5, "schema_valid": valid,
                                                          "validation_error": verr if not valid else None})
        return valid, verr, {"ms": elapsed}

    winner = None
    winner_name = None
    for name, fn in (("json_schema", call_json_schema), ("tool_call", call_tool), ("json_object", call_json_object)):
        valid, _err, _meta = fn()
        if valid:
            winner, winner_name = fn, name
            break

    deterministic = False
    if winner is not None:
        valid, _err, _meta = winner()
        deterministic = valid
        attempts[-1]["name"] = f"{winner_name}_repeat"

    success = winner is not None and deterministic
    write_record(record_path, args.provider, base, model, attempts, winner_name, success, deterministic)
    print_summary(attempts, winner_name, success)
    return 0 if success else 3


def write_record(path: Path, provider: str, base: str, model: str, attempts: list[dict],
                 winner: str | None, success: bool, deterministic: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": provider,
        "base_url": base,
        "model": model,
        "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attempts": attempts,
        "winning_rung": winner,
        "success": success,
        "deterministic": deterministic,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {path.relative_to(ROOT)}")


def print_summary(attempts: list[dict], winner: str | None, success: bool) -> None:
    for a in attempts:
        if a["name"] == "no_key":
            flag = "ok  " if a["http"] == 401 else "FAIL"
        else:
            flag = "ok  " if a["ok"] and a.get("schema_valid") in (True, None) else "FAIL"
        detail = f"schema_valid={a['schema_valid']}" if "schema_valid" in a else ""
        if a["name"] == "no_key":
            detail = "expected 401"
        print(f"{flag:<4} {a['name']:<20} http={a['http']:<3} {a['ms']:>5}ms {detail}".rstrip())
    print(f"winning rung: {winner or 'none'} · success={success}")


if __name__ == "__main__":
    sys.exit(main())
