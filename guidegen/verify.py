"""Verifier: schema, provenance, cross-format consistency, and secret leakage.

This is the gate between rendering and publishing. It does not fetch anything by default.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .spec import validate_spec, walk_facts

SECRET_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])oc_sk_[A-Za-z0-9]{8,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
)


def _norm(text: str) -> str:
    return " ".join(str(text).replace("\u00a0", " ").split())


def verify_spec(spec: dict) -> list[str]:
    issues = list(validate_spec(spec))
    seen: set[str] = set()
    for source in spec.get("sources", []):
        url = source.get("url")
        if not url:
            issues.append("source without url")
            continue
        if url in seen:
            issues.append(f"duplicate source: {url}")
        seen.add(url)
        doc_hash = str(source.get("doc_hash") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", doc_hash):
            issues.append(f"source {url}: invalid doc_hash")
    return issues


def verify_quotes(spec: dict, text_by_url: dict[str, str]) -> list[str]:
    issues: list[str] = []
    for path, fact in walk_facts(spec):
        if fact.get("value") is None:
            continue
        quote = fact.get("quote")
        source_url = fact.get("source_url")
        if not quote or not source_url:
            issues.append(f"{path}: missing quote/source")
            continue
        if source_url not in text_by_url:
            issues.append(f"{path}: no fetched text for {source_url}")
            continue
        if path == "auth.oauth.discovery_url" and quote == source_url:
            continue
        if _norm(quote) not in _norm(text_by_url[source_url]):
            issues.append(f"{path}: quote not found in {source_url}")
    return issues


def _check_secret(text: str, label: str) -> list[str]:
    return [f"{label}: possible secret material" for pattern in SECRET_PATTERNS if pattern.search(text)]


def verify_rendered(spec: dict, human: str, agent: dict, agent_md: str | None = None) -> list[str]:
    issues: list[str] = []
    human_norm = html.unescape(human).replace("\\|", "|")
    agent_text = json.dumps(agent, ensure_ascii=False)
    agent_text = agent_text.replace('\\"', '"')

    for path, fact in walk_facts(spec):
        value = fact.get("value")
        if value is None:
            continue
        if isinstance(value, bool):
            needle = "Yes" if value else "No"
            agent_needle = "true" if value else "false"
        else:
            needle = str(value)
            agent_needle = needle
        if needle and needle not in human_norm:
            issues.append(f"{path}: value missing from guide.md")
        if agent_needle and agent_needle not in agent_text:
            issues.append(f"{path}: value missing from agent.json")

    for step in spec["setup"].get("steps", []):
        for key in ("title", "detail"):
            if step[key] not in human_norm:
                issues.append(f"step {step['order']} {key}: missing from guide.md")
            if step[key] not in agent_text:
                issues.append(f"step {step['order']} {key}: missing from agent.json")
    for blocker in spec["setup"].get("blockers", []):
        for key in ("symptom", "fix"):
            if blocker[key] not in human_norm:
                issues.append(f"blocker {blocker['id']} {key}: missing from guide.md")
            if blocker[key] not in agent_text:
                issues.append(f"blocker {blocker['id']} {key}: missing from agent.json")
    for item in spec.get("env", []):
        if item["name"] not in human_norm or item["name"] not in agent_text:
            issues.append(f"env {item['name']}: missing from a projection")

    if spec.get("unknown_fields") != agent.get("unknown_fields"):
        issues.append("unknown_fields differ between spec and agent.json")
    if spec["setup"]["time"] != agent["setup"].get("time"):
        issues.append("time differs between spec and agent.json")
    for path in spec.get("unknown_fields", []):
        if path not in human_norm:
            issues.append(f"{path}: unknown field not shown in guide.md")

    for label, text in (("guide.md", human), ("agent.json", agent_text), ("agent.md", agent_md or "")):
        issues.extend(_check_secret(text, label))
    return issues


def verify_all(spec: dict, human: str, agent: dict, agent_md: str | None = None,
               text_by_url: dict[str, str] | None = None) -> list[str]:
    issues = verify_spec(spec) + verify_rendered(spec, human, agent, agent_md)
    if text_by_url:
        issues += verify_quotes(spec, text_by_url)
    return issues


def _load_job(job_dir: Path):
    spec = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
    human = (job_dir / "guide.md").read_text(encoding="utf-8")
    agent = json.loads((job_dir / "agent.json").read_text(encoding="utf-8"))
    agent_md_path = job_dir / "agent.md"
    agent_md = agent_md_path.read_text(encoding="utf-8") if agent_md_path.exists() else None
    return spec, human, agent, agent_md


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="guidegen.verify", description="Verify a spec + rendered guides")
    parser.add_argument("job_dir", nargs="?", help="directory with spec.json, guide.md, agent.json, agent.md")
    parser.add_argument("--golden", help="directory of job directories to verify")
    args = parser.parse_args(argv)

    dirs = []
    if args.golden:
        dirs = [p for p in Path(args.golden).iterdir() if (p / "spec.json").exists()]
    elif args.job_dir:
        dirs = [Path(args.job_dir)]
    else:
        parser.print_help()
        return 2

    failed = 0
    for job_dir in sorted(dirs):
        try:
            spec, human, agent, agent_md = _load_job(job_dir)
        except Exception as exc:  # noqa: BLE001 - a broken artifact is a verification failure
            print(f"INVALID {job_dir}: {exc}")
            failed += 1
            continue
        issues = verify_all(spec, human, agent, agent_md)
        if issues:
            failed += 1
            print(f"INVALID {job_dir}: {len(issues)} issue(s)")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print(f"VALID   {job_dir}: {spec['provider']['name']} · {spec['status']} · "
                  f"{spec['quality']['facts_verified']}/{spec['quality']['facts_total']} verified")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
