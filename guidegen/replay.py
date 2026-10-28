"""Deterministic replay from the stored spec.

This re-renders guide.md / agent.json / agent.md and re-runs the verifier. It makes no network
or model calls, so it is safe to run while debugging. `--from-cache` is accepted for the command
shape in PLAN.md; the spec already is the cache.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .env import ROOT
from .pipeline import render_bundle
from .verify import verify_all


def replay(job_dir: Path, out_dir: Path | None = None) -> int:
    spec_path = job_dir / "spec.json"
    if not spec_path.exists():
        print(f"no spec at {spec_path}")
        return 2
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    human, agent, agent_md = render_bundle(spec)
    target = out_dir or (job_dir / "replayed")
    target.mkdir(parents=True, exist_ok=True)
    (target / "guide.md").write_text(human, encoding="utf-8")
    (target / "agent.json").write_text(json.dumps(agent, indent=2) + "\n", encoding="utf-8")
    (target / "agent.md").write_text(agent_md, encoding="utf-8")
    issues = verify_all(spec, human, agent, agent_md)
    (target / "validation.json").write_text(
        json.dumps({"ok": not issues, "issues": issues}, indent=2) + "\n", encoding="utf-8")
    print(f"{'VALID  ' if not issues else 'INVALID'} {job_dir.name} -> {target}")
    for issue in issues:
        print(f"  - {issue}")
    return 0 if not issues else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guidegen.replay", description="Re-render and verify a stored spec")
    parser.add_argument("job_id", help="job id under data/jobs, or a path to a job directory")
    parser.add_argument("--jobs-dir", default=str(ROOT / "data" / "jobs"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--from-cache", action="store_true", help="accepted; replay makes no network calls")
    args = parser.parse_args(argv)
    path = Path(args.job_id)
    if not path.exists():
        path = Path(args.jobs_dir) / args.job_id
    return replay(path, Path(args.out) if args.out else None)


if __name__ == "__main__":
    raise SystemExit(main())
