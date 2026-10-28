"""Load and validate the canonical provider spec.

Two layers:

* structural — JSON Schema 2020-12 (``schema/provider.spec.schema.json``);
* semantic — rules the schema cannot express: availability vs auth method, time-range
  sanity, provenance (every fact's ``source_url`` must be in ``sources``), the
  unknown-field ledger, duplicate ids/orders, and the quality counters.

``validate_spec`` returns a list of human-readable issues; ``assert_valid`` raises
``SpecError``. Both are used by assembly and by the verifier.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator, FormatChecker

from .errors import WARNINGS

SCHEMA_PATH = Path(__file__).with_name("schema") / "provider.spec.schema.json"
_SCHEMA_CACHE: dict[str, Any] | None = None


class SpecError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        preview = "; ".join(issues[:3])
        super().__init__(f"{len(issues)} spec issue(s): {preview}")


def load_schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE


def _path(parts) -> str:
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out or "$"


def walk_facts(node: Any, path: str = "") -> Iterator[tuple[str, dict]]:
    """Yield (dotted_path, fact) for every {value, quote, source_url} object."""
    if isinstance(node, dict):
        if set(node) == {"value", "quote", "source_url"}:
            yield path, node
            return
        for key, value in node.items():
            yield from walk_facts(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk_facts(value, f"{path}[{index}]")


def validate_spec(spec: dict) -> list[str]:
    issues: list[str] = []
    validator = Draft202012Validator(load_schema(), format_checker=FormatChecker())
    structural = sorted(validator.iter_errors(spec), key=lambda e: list(e.path))
    for err in structural:
        issues.append(f"{_path(list(err.path))}: {err.message}")
    if issues:
        return issues

    resolution = spec["resolution"]
    auth = spec["auth"]
    setup = spec["setup"]
    quality = spec["quality"]
    status = spec["status"]

    if resolution["decision"] != "resolved":
        issues.append("resolution.decision: a spec is only built for a resolved provider")

    if auth["primary"] is not None and auth["primary"] not in auth["methods"]:
        issues.append(f"auth.primary: {auth['primary']!r} not in auth.methods")

    availability = resolution["availability"]
    if availability == "api_key_only":
        if auth["api_key"] is None:
            issues.append("auth.api_key: api_key_only requires api_key details")
        if auth["oauth"] is not None:
            issues.append("auth.oauth: api_key_only must not carry oauth details")
    elif availability == "oidc_only":
        if auth["oauth"] is None:
            issues.append("auth.oauth: oidc_only requires oauth details")
        elif auth["oauth"]["discovery_url"]["value"] is None:
            issues.append("auth.oauth.discovery_url: oidc_only requires a discovery URL")
    elif availability in ("self_serve_oauth", "approval_gated"):
        if auth["oauth"] is None:
            issues.append(f"auth.oauth: {availability} requires oauth details")
        else:
            for field in ("authorization_url", "token_url"):
                if auth["oauth"][field]["value"] is None:
                    issues.append(f"auth.oauth.{field}: {availability} requires a value")
    elif availability == "no_public_api":
        if auth["methods"] not in ([], ["none"]):
            issues.append("auth.methods: no_public_api requires [] or ['none']")
        if auth["oauth"] is not None or auth["api_key"] is not None:
            issues.append("auth: no_public_api must not carry oauth or api_key details")

    if status == "complete":
        if not setup["steps"]:
            issues.append("setup.steps: a complete guide needs at least one step")
        if not spec["sources"]:
            issues.append("sources: a complete guide needs at least one source")
        if quality["confidence"] == "none":
            issues.append("quality.confidence: a complete guide cannot have no confidence")

    for name in ("steps", "verification"):
        orders = [item["order"] for item in setup[name]]
        if len(set(orders)) != len(orders):
            issues.append(f"setup.{name}: duplicate order values")
        if orders and sorted(orders) != list(range(1, len(orders) + 1)):
            issues.append(f"setup.{name}: orders must run 1..n")

    blocker_ids = [b["id"] for b in setup["blockers"]]
    if len(set(blocker_ids)) != len(blocker_ids):
        issues.append("setup.blockers: duplicate ids")

    time = setup["time"]
    lo, hi, likely = time["range_min_minutes"], time["range_max_minutes"], time["likely_minutes"]
    if lo is not None and hi is not None and lo > hi:
        issues.append("setup.time: range_min_minutes > range_max_minutes")
    if None not in (lo, hi, likely) and not (lo <= likely <= hi):
        issues.append("setup.time: likely_minutes outside the stated range")

    env_names = [item["name"] for item in spec["env"]]
    if len(set(env_names)) != len(env_names):
        issues.append("env: duplicate variable names")
    if auth["api_key"] is not None and auth["api_key"]["env_var"] not in env_names:
        issues.append("auth.api_key.env_var: not declared in env[]")

    source_urls = [item["url"] for item in spec["sources"]]
    if len(set(source_urls)) != len(source_urls):
        issues.append("sources: duplicate urls")

    facts = list(walk_facts(spec))
    total = len(facts)
    verified = sum(1 for _, fact in facts if fact["value"] is not None)
    unknown_paths = {path for path, fact in facts if fact["value"] is None}
    if quality["facts_total"] != total:
        issues.append(f"quality.facts_total: {quality['facts_total']} != {total} facts found")
    if quality["facts_verified"] != verified:
        issues.append(f"quality.facts_verified: {quality['facts_verified']} != {verified} verified facts")
    if quality["unknown_count"] != len(unknown_paths):
        issues.append(f"quality.unknown_count: {quality['unknown_count']} != {len(unknown_paths)} unknown facts")

    listed = set(spec["unknown_fields"])
    for path in sorted(unknown_paths - listed):
        issues.append(f"unknown_fields: missing {path!r}")
    for path in sorted(listed - unknown_paths):
        issues.append(f"unknown_fields: {path!r} is not an unknown fact")

    source_set = set(source_urls)
    for path, fact in facts:
        url = fact["source_url"]
        if url is not None and url not in source_set:
            issues.append(f"{path}: source_url not in sources[]: {url}")

    for warning in spec["warnings"]:
        if warning["code"] not in WARNINGS:
            issues.append(f"warnings: unknown code {warning['code']!r}")

    return issues


def assert_valid(spec: dict) -> None:
    issues = validate_spec(spec)
    if issues:
        raise SpecError(issues)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guidegen.spec", description="Validate a provider.spec.json")
    parser.add_argument("path")
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    args = parser.parse_args(argv)

    spec = json.loads(Path(args.path).read_text(encoding="utf-8"))
    issues = validate_spec(spec)
    if args.json:
        print(json.dumps({"valid": not issues, "issues": issues}, indent=2, ensure_ascii=False))
    elif issues:
        print(f"INVALID — {len(issues)} issue(s)", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
    else:
        facts = list(walk_facts(spec))
        print(f"VALID — {spec['provider']['name']} · {spec['status']} · {len(facts)} facts "
              f"({spec['quality']['facts_verified']} verified, {spec['quality']['unknown_count']} unknown)")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
