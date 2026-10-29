"""Build a provider spec from live docs: resolve -> fetch -> extract -> assemble -> validate.

Usage:
    python3 -m guidegen.build "google"
    python3 -m guidegen.build "github" --max-chunks 4 --json

Exit codes: 0 built · 2 ambiguous · 3 unknown · 4 invalid input · 5 build failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .assemble import assemble
from .bridge import provider_for_catalogue
from .env import ROOT, load_env
from .errors import GuideError, user_payload
from .llm import LLMClient
from .render_agent import render_agent_json, render_agent_md
from .render_human import render_human
from .research import research
from .resolve import Resolver
from .verify import verify_all


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _print_summary(spec: dict, job_dir: Path) -> None:
    quality = spec["quality"]
    print(f"provider   {spec['provider']['name']} ({spec['provider']['id']})")
    print(f"status     {spec['status']}")
    print(f"facts      {quality['facts_verified']} verified / {quality['unknown_count']} unknown")
    print(f"methods    {', '.join(spec['auth']['methods']) or 'none'}")
    print(f"sources    {len(spec['sources'])}")
    print(f"spec       {job_dir / 'spec.json'}")
    print(f"guide      {job_dir / 'guide.md'}")
    print(f"agent      {job_dir / 'agent.json'} + agent.md")
    if spec["warnings"]:
        print(f"warnings   {', '.join(w['code'] for w in spec['warnings'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guidegen.build", description="Build a provider spec from live docs")
    parser.add_argument("query")
    parser.add_argument("--max-chunks", type=int, default=8)
    parser.add_argument("--jobs-dir", default=str(ROOT / "data" / "jobs"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    load_env()
    job_id = "job_" + uuid.uuid4().hex[:10]
    job_dir = Path(args.jobs_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    events_path = job_dir / "events.jsonl"
    events: list[dict] = []

    def log(stage: str, event: str, **fields) -> None:
        events.append({"ts": _now(), "job_id": job_id, "stage": stage, "event": event, **fields})

    started = time.monotonic()
    try:
        resolver = Resolver(probe=None)
        resolution = resolver.resolve(args.query)
        log("resolve", "resolved", provider=resolution.provider.id if resolution.provider else None,
            decision=resolution.decision, matched_by=resolution.matched_by, confidence=resolution.confidence)
        if resolution.decision == "ask":
            for candidate in resolution.alternatives:
                print(f"  {candidate.id:<12} {candidate.name} ({candidate.domain})")
            log("resolve", "ask", alternatives=[c.id for c in resolution.alternatives])
            return 2
        provider = resolution.provider
        if provider is None and resolution.decision == "catalogue" and resolution.catalogue is not None:
            # Catalogue name with no curated row: locate the setup docs (ladder + search).
            log("find", "start", entry=resolution.catalogue.slug)
            try:
                provider = provider_for_catalogue(resolution.catalogue)
            except GuideError as exc:
                print(user_payload(exc.code)["error"]["message"], file=sys.stderr)
                log("find", "unknown", detail=exc.detail)
                return 3
            log("find", "done", provider=provider.id, docs=list(provider.docs))
        if resolution.decision not in ("resolved", "catalogue") or provider is None:
            print("We couldn't match that. Try the app's website address instead.", file=sys.stderr)
            log("resolve", "unknown")
            return 3
        if provider.availability in ("unknown", "no_public_api"):
            message = "The docs don't state enough to write a setup guide."
            print(message, file=sys.stderr)
            log("resolve", "no_auth_surface", availability=provider.availability)
            return 5

        client = LLMClient.from_env()
        log("research", "start", docs=list(provider.docs), discovery=provider.discovery)
        research_result = research(provider, client, max_chunks=args.max_chunks)
        log("research", "done", docs=len(research_result.docs), chunks=len(research_result.chunks),
            facts=len(research_result.facts), scopes=len(research_result.scopes),
            conflicts=len(research_result.conflicts))
        for chunk in research_result.chunks:
            log("extract", "chunk", url=chunk.url, index=chunk.index, chars=chunk.chars,
                facts=chunk.facts, scopes=chunk.scopes, ms=chunk.ms, model=chunk.model,
                total_tokens=(chunk.usage or {}).get("total_tokens"),
                cost=(chunk.usage or {}).get("estimated_cost"),
                scope_ms=chunk.scope_ms, scope_tokens=(chunk.scope_usage or {}).get("total_tokens"),
                scope_cost=(chunk.scope_usage or {}).get("estimated_cost"))

        log("research", "steps", count=len(research_result.steps),
            model=(research_result.steps_meta or {}).get("model"),
            ms=(research_result.steps_meta or {}).get("ms"),
            total_tokens=(research_result.steps_meta or {}).get("total_tokens"),
            cost=(research_result.steps_meta or {}).get("estimated_cost"))

        spec = assemble(provider, resolution, research_result, client.model)
        log("assemble", "validated", facts=spec["quality"]["facts_verified"],
            unknown=spec["quality"]["unknown_count"], sources=len(spec["sources"]))

        (job_dir / "docs").mkdir(exist_ok=True)
        for index, document in enumerate(research_result.docs):
            (job_dir / "docs" / f"{index}.html").write_text(document.raw, encoding="utf-8")
        (job_dir / "spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        human_md, agent_data, agent_md = render_human(spec), render_agent_json(spec), render_agent_md(spec)
        (job_dir / "guide.md").write_text(human_md, encoding="utf-8")
        (job_dir / "agent.json").write_text(json.dumps(agent_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (job_dir / "agent.md").write_text(agent_md, encoding="utf-8")
        issues = verify_all(spec, human_md, agent_data, agent_md)
        (job_dir / "validation.json").write_text(
            json.dumps({"ok": not issues, "issues": issues}, indent=2) + "\n", encoding="utf-8")
        log("verify", "checked", issues=len(issues))
        if issues:
            raise GuideError("VERIFY_FAILED", "; ".join(issues[:5]))
        log("render", "written", guide=len(human_md), agent_json=len(agent_data), agent_md=len(agent_md))
        log("done", "built", ms=int((time.monotonic() - started) * 1000), path=str(job_dir / "spec.json"))

        if args.json:
            print(json.dumps({"job_id": job_id, "path": str(job_dir / "spec.json"),
                              "provider": provider.id, "status": spec["status"],
                              "facts": spec["quality"], "warnings": [w["code"] for w in spec["warnings"]]}, indent=2))
        else:
            _print_summary(spec, job_dir)
        return 0
    except GuideError as exc:
        log("error", exc.code, detail=exc.detail)
        if args.json:
            print(json.dumps(user_payload(exc.code), indent=2))
        else:
            print(f"ERROR  {exc.spec.user_message}", file=sys.stderr)
        return 5
    finally:
        events_path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
                               encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
