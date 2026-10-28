"""Debug a job from its artifact directory: timeline, spec state, validation, paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .env import ROOT


def _job_dir(job_id: str, jobs_dir: Path) -> Path:
    candidate = Path(job_id)
    if candidate.exists():
        return candidate
    return jobs_dir / job_id


def debug(job_id: str, jobs_dir: Path | None = None) -> int:
    directory = _job_dir(job_id, jobs_dir or (ROOT / "data" / "jobs"))
    if not directory.exists():
        print(f"job not found: {directory}")
        return 2
    print(f"job        {directory.name}")
    print(f"directory  {directory}")
    files = sorted(p.name for p in directory.iterdir() if p.is_file())
    print(f"files      {', '.join(files) or 'none'}")

    events_path = directory / "events.jsonl"
    if events_path.exists():
        lines = events_path.read_text(encoding="utf-8").splitlines()[-12:]
        print("\nlast events")
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            detail = event.get("detail")
            if not detail and event.get("chunk"):
                detail = f"chunk {event['chunk']}/{event.get('total', '?')}"
            if not detail and event.get("percent") is not None:
                detail = f"{event['percent']}%"
            print(f"  {event.get('ts', '?')}  {event.get('stage', '?'):10}  {event.get('event', '?')}"
                  + (f"  {detail}" if detail else ""))

    spec_path = directory / "spec.json"
    if spec_path.exists():
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        quality = spec.get("quality", {})
        print(f"\nstatus     {spec.get('status')}")
        print(f"facts      {quality.get('facts_verified')} verified / {quality.get('unknown_count')} unknown")
        print(f"provider   {spec.get('provider', {}).get('name')} ({spec.get('provider', {}).get('id')})")
        warnings = [warning.get("code") for warning in spec.get("warnings", [])]
        print(f"warnings   {', '.join(warnings) or 'none'}")
        unknowns = spec.get("unknown_fields", [])
        if unknowns:
            print(f"unknowns   {', '.join(unknowns)}")

    validation_path = directory / "validation.json"
    if validation_path.exists():
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        print(f"\nvalidation {'OK' if validation.get('ok') else 'FAILED'}")
        for issue in validation.get("issues", []):
            print(f"  - {issue}")

    print(f"\nreplay     python3 -m guidegen.replay {directory.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guidegen.debug", description="Inspect a job artifact directory")
    parser.add_argument("job_id")
    parser.add_argument("--jobs-dir", default=str(ROOT / "data" / "jobs"))
    args = parser.parse_args(argv)
    return debug(args.job_id, Path(args.jobs_dir))


if __name__ == "__main__":
    raise SystemExit(main())
