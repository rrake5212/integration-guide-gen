"""GuideGen web surface: resolve -> async job -> SSE progress -> rendered guide.

Testing-only posture: no accounts, unlisted job ids, key server-side, cache left for P6/P7.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import markdown
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..env import ROOT, load_env
from ..errors import GuideError, spec as error_spec, user_payload
from ..pipeline import build_spec, forced_resolution, render_bundle
from ..probe import probe_domain
from ..resolve import Resolution, Resolver
from ..verify import verify_all

STATIC_DIR = Path(__file__).with_name("static")
DEFAULT_DB = ROOT / "data" / "guidegen.db"
JOBS_DIR = ROOT / "data" / "jobs"
STAGE_LABELS = {
    "queued": "Queued",
    "resolve": "Resolving provider",
    "docs": "Reading provider docs",
    "extract": "Extracting quoted facts",
    "assemble": "Checking the fact model",
    "render": "Rendering guide",
    "done": "Guide ready",
}
STATUS_LABELS = {"queued": "Queued", "running": "Building", "done": "Complete", "failed": "Failed"}
JOB_TIMEOUT_SECONDS = int(os.environ.get("GUIDEGEN_JOB_TIMEOUT_SECONDS", "180"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    input TEXT NOT NULL,
                    pick TEXT,
                    provider_id TEXT,
                    provider_name TEXT,
                    availability TEXT,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    message TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    spec_json TEXT,
                    human_md TEXT,
                    agent_json TEXT,
                    agent_md TEXT,
                    model TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def create(self, job_id: str, raw_input: str, pick: str | None, provider_id: str,
               provider_name: str, availability: str) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO jobs (id,input,pick,provider_id,provider_name,availability,status,stage,progress,"
                "message,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, raw_input, pick, provider_id, provider_name, availability, "queued", "queued", 0,
                 "Queued", now, now),
            )

    def update(self, job_id: str, **fields) -> None:
        allowed = {"status", "stage", "progress", "message", "error_code", "error_message", "spec_json",
                   "human_md", "agent_json", "agent_md", "model", "provider_id", "provider_name", "availability"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        columns = ", ".join(f"{key}=?" for key in updates)
        with self._conn() as conn:
            conn.execute(f"UPDATE jobs SET {columns} WHERE id=?", (*updates.values(), job_id))

    def get(self, job_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def running(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()
        return int(row[0]) if row else 0


def _public(job: dict) -> dict:
    payload = {
        "job_id": job["id"],
        "status": job["status"],
        "stage": job["stage"],
        "progress": job["progress"],
        "message": job["message"],
        "provider": {"id": job["provider_id"], "name": job["provider_name"],
                     "availability": job["availability"]},
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
    if job["status"] == "failed":
        payload["error"] = {"code": job["error_code"], "message": job["error_message"]}
    if job["status"] == "done":
        payload["links"] = {
            "guide": f"/g/{job['id']}/guide.md",
            "agent_json": f"/g/{job['id']}/agent.json",
            "agent_md": f"/g/{job['id']}/agent.md",
        }
    return payload


def _resolution_payload(res: Resolution) -> dict:
    out = {
        "input": res.raw,
        "normalized": res.normalized,
        "kind": res.kind,
        "decision": res.decision,
        "matched_by": res.matched_by,
        "confidence": res.confidence,
        "availability": res.availability,
        "notes": res.notes,
        "candidates": [
            {"id": p.id, "name": p.name, "domain": p.domain,
             "availability": p.availability}
            for p in res.alternatives
        ],
    }
    if res.provider is not None:
        out["provider"] = {
            "id": res.provider.id,
            "name": res.provider.name,
            "domains": list(res.provider.domains),
            "console": res.provider.console,
            "docs": list(res.provider.docs),
        }
        out["can_build"] = (res.provider.availability not in ("unknown", "no_public_api")
                            and bool(res.provider.docs))
    return out


def _error_json(exc: GuideError, status: int | None = None) -> JSONResponse:
    return JSONResponse(user_payload(exc.code), status_code=status or exc.spec.status)


def _resolve(raw: str, pick: str | None = None) -> Resolution:
    resolver = Resolver(probe=probe_domain)
    return forced_resolution(resolver.registry, raw, pick) if pick else resolver.resolve(raw)


def _process_job(store: JobStore, job_id: str) -> None:
    job = store.get(job_id)
    if not job:
        return
    store.update(job_id, status="running", stage="resolve", progress=2, message=STAGE_LABELS["resolve"])
    started = time.monotonic()
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []

    def log_event(stage: str, name: str, level: str = "info", **fields) -> None:
        events.append({"ts": _now(), "level": level, "job_id": job_id, "stage": stage, "event": name, **fields})

    log_event("job", "start", provider=job.get("provider_id"), input=job.get("input"))

    def progress(stage: str, percent: int, **fields) -> None:
        if time.monotonic() - started > JOB_TIMEOUT_SECONDS:
            raise GuideError("JOB_TIMEOUT", f"job {job_id} exceeded {JOB_TIMEOUT_SECONDS}s")
        message = STAGE_LABELS.get(stage, stage)
        if stage == "extract" and fields.get("chunk"):
            message += f" · chunk {fields['chunk']}/{fields['total']}"
        log_event(stage, "progress", percent=percent, **fields)
        store.update(job_id, stage=stage, progress=max(0, min(99, percent)), message=message)

    try:
        result = build_spec(job["input"], pick=job["pick"], max_chunks=10, on_progress=progress, job_id=job_id)
        human_md, agent_json, agent_md = render_bundle(result.spec)
        (job_dir / "spec.json").write_text(json.dumps(result.spec, indent=2) + "\n", encoding="utf-8")
        (job_dir / "guide.md").write_text(human_md, encoding="utf-8")
        (job_dir / "agent.json").write_text(json.dumps(agent_json, indent=2) + "\n", encoding="utf-8")
        (job_dir / "agent.md").write_text(agent_md, encoding="utf-8")
        docs_dir = job_dir / "docs"
        docs_dir.mkdir(exist_ok=True)
        for index, document in enumerate(result.research.docs):
            (docs_dir / f"{index}.html").write_text(document.raw, encoding="utf-8")
        log_event("research", "docs_stored", count=len(result.research.docs))
        issues = verify_all(result.spec, human_md, agent_json, agent_md)
        (job_dir / "validation.json").write_text(
            json.dumps({"ok": not issues, "issues": issues}, indent=2) + "\n", encoding="utf-8")
        log_event("verify", "checked", issues=len(issues))
        if issues:
            raise GuideError("VERIFY_FAILED", "; ".join(issues[:5]))
        store.update(
            job_id, status="done", stage="done", progress=100, message=STAGE_LABELS["done"],
            spec_json=json.dumps(result.spec), human_md=human_md,
            agent_json=json.dumps(agent_json), agent_md=agent_md,
            model=result.spec["generator"].get("model"),
            provider_id=result.spec["provider"]["id"], provider_name=result.spec["provider"]["name"],
            availability=result.spec["resolution"]["availability"],
        )
        log_event("job", "done", status="done", facts=result.spec["quality"]["facts_verified"])
    except GuideError as exc:
        log_event("error", exc.code, level="error", detail=exc.detail)
        store.update(job_id, status="failed", stage=exc.spec.stage, progress=100,
                     message=exc.spec.user_message, error_code=exc.code, error_message=exc.spec.user_message)
    except Exception:  # noqa: BLE001 - any uncaught failure becomes a clean INTERNAL job state
        log_event("error", "INTERNAL", level="error")
        store.update(job_id, status="failed", stage="job", progress=100,
                     message=error_spec("INTERNAL").user_message, error_code="INTERNAL",
                     error_message=error_spec("INTERNAL").user_message)
    finally:
        (job_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n", encoding="utf-8")
        final = store.get(job_id) or {}
        (job_dir / "job.json").write_text(json.dumps({
            "job_id": job_id, "input": job.get("input"), "provider": job.get("provider_id"),
            "status": final.get("status"), "stage": final.get("stage"), "progress": final.get("progress"),
            "error_code": final.get("error_code"), "created_at": final.get("created_at"),
            "updated_at": final.get("updated_at"),
        }, indent=2) + "\n", encoding="utf-8")


def _worker(app: FastAPI) -> None:
    while True:
        job_id = app.state.queue.get()
        try:
            _process_job(app.state.store, job_id)
        finally:
            app.state.queue.task_done()


def _meta_html(spec: dict) -> tuple[str, str]:
    availability = {
        "self_serve_oauth": "Self-serve OAuth app",
        "approval_gated": "Approval-gated OAuth app",
        "api_key_only": "API key only",
        "oidc_only": "OIDC only",
        "no_public_api": "No public API",
        "unknown": "Availability unclear",
    }.get(spec["resolution"]["availability"], spec["resolution"]["availability"])
    time = spec["setup"]["time"]
    if time.get("likely_minutes") is None:
        time_text = "time unknown"
    else:
        time_text = f"{time['likely_minutes']} min likely ({time['range_min_minutes']}–{time['range_max_minutes']})"
    verified = spec["quality"]["facts_verified"]
    total = spec["quality"]["facts_total"]
    meta = f"{availability} · {time_text} · {verified}/{total} facts verified"
    metrics = "".join([
        f'<div class="metric"><span class="n">{time["likely_minutes"] or "—"}</span><span class="l">min likely</span></div>',
        f'<div class="metric"><span class="n">{verified}/{total}</span><span class="l">facts verified</span></div>',
        f'<div class="metric"><span class="n">{len(spec["setup"].get("steps", []))}</span><span class="l">steps</span></div>',
        f'<div class="metric"><span class="n">{len(spec["setup"].get("blockers", []))}</span><span class="l">blockers</span></div>',
    ])
    return meta, metrics


def _warnings_html(spec: dict) -> str:
    warnings = spec.get("warnings") or []
    if not warnings:
        return ""
    items = " · ".join(f"{html.escape(w['code'])} — {html.escape(w['message'])}" for w in warnings)
    return f'<div class="warning-banner">{items}</div>'


def _render_result_page(job: dict) -> str:
    template = (STATIC_DIR / "result.html").read_text(encoding="utf-8")
    job_id = html.escape(job["id"])
    provider = html.escape(job["provider_name"] or "Guide")
    status = job["status"]
    status_label = STATUS_LABELS.get(status, status)
    if status == "done" and job["spec_json"]:
        spec = json.loads(job["spec_json"])
        provider = html.escape(spec["provider"]["name"])
        status = "complete" if spec["status"] == "complete" else "partial"
        status_label = "Complete" if spec["status"] == "complete" else "Partial"
        meta, metrics = _meta_html(spec)
        warnings = _warnings_html(spec)
        human_html = markdown.markdown(job["human_md"] or "", extensions=["tables", "fenced_code", "sane_lists"])
        human_html = re.sub(r"<h1>.*?</h1>\s*", "", human_html, count=1, flags=re.S)
        agent_json = html.escape(job["agent_json"] or "{}")
        agent_md = html.escape(job["agent_md"] or "")
        progress = ""
        tabs_hidden = ""
        panels_hidden = ""
        download = f"/g/{job_id}/guide.md"
    elif status == "failed":
        meta = html.escape(job["error_message"] or "The guide could not be built.")
        metrics = ""
        warnings = ""
        human_html = agent_json = agent_md = ""
        progress = (f'<div class="failed-card"><h2>Could not build this guide</h2>'
                    f'<p>{html.escape(job["error_message"] or "")}</p><p><a href="/">Try another app →</a></p></div>')
        tabs_hidden = "hidden"
        panels_hidden = "hidden"
        download = "#"
    else:
        meta = "Building the guide from the provider's docs. This page updates itself."
        metrics = ""
        warnings = ""
        human_html = agent_json = agent_md = ""
        progress = f"""
        <div class="progress-card" id="progress" data-job="{job_id}" data-status="{html.escape(status)}">
          <div class="progress-head"><span>Building guide</span><span id="progress-pct">{job["progress"]}%</span></div>
          <h2 class="progress-stage" id="progress-stage">{html.escape(job["message"] or "Queued")}</h2>
          <p class="progress-note" id="progress-note">Stage: {html.escape(job["stage"] or "queued")} · {job["progress"]}%</p>
          <div class="progress-track"><div class="progress-fill" id="progress-fill" style="width:{max(2, job["progress"])}%"></div></div>
          <p class="progress-note">You can leave this tab open; the guide appears when it is ready.</p>
        </div>
        """
        tabs_hidden = "hidden"
        panels_hidden = "hidden"
        download = "#"

    values = {
        "TITLE": provider,
        "JOB_ID": job_id,
        "PROVIDER": provider,
        "STATUS": status,
        "STATUS_LABEL": status_label,
        "META": meta,
        "METRICS": metrics,
        "WARNINGS": warnings,
        "PROGRESS": progress,
        "TABS_HIDDEN": tabs_hidden,
        "PANELS_HIDDEN": panels_hidden,
        "DOWNLOAD": download,
    }
    page = template
    for key, value in values.items():
        page = page.replace(f"%%{key}%%", value)
    page = page.replace("%%HUMAN%%", human_html).replace("%%AGENT_JSON%%", agent_json).replace("%%AGENT_MD%%", agent_md)
    return page


class ResolveBody(BaseModel):
    input: str


class GuideBody(BaseModel):
    input: str
    pick: str | None = None


def create_app(db_path: str | Path | None = None, start_workers: bool = True) -> FastAPI:
    load_env()
    store = JobStore(db_path or DEFAULT_DB)
    app = FastAPI(title="GuideGen", docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.queue = queue.Queue()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    if start_workers:
        for _ in range(2):
            threading.Thread(target=_worker, args=(app,), daemon=True).start()

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.post("/api/resolve")
    def api_resolve(body: ResolveBody) -> JSONResponse:
        try:
            res = _resolve(body.input)
        except GuideError as exc:
            return _error_json(exc)
        return JSONResponse(_resolution_payload(res))

    @app.post("/api/guides")
    def api_create(body: GuideBody) -> JSONResponse:
        try:
            res = _resolve(body.input, body.pick)
        except GuideError as exc:
            return _error_json(exc)
        if res.decision == "ask":
            payload = user_payload("AMBIGUOUS_PROVIDER")
            payload["candidates"] = _resolution_payload(res)["candidates"]
            return JSONResponse(payload, status_code=409)
        if res.decision != "resolved" or res.provider is None:
            return _error_json(GuideError("PROVIDER_UNKNOWN"))
        if res.provider.availability in ("unknown", "no_public_api") or not res.provider.docs:
            payload = user_payload("PROVIDER_UNKNOWN")
            payload["error"]["message"] = "We found the provider, but its setup docs are not available yet."
            return JSONResponse(payload, status_code=422)
        job_id = "job_" + uuid.uuid4().hex[:10]
        store.create(job_id, body.input, body.pick, res.provider.id, res.provider.name, res.provider.availability)
        app.state.queue.put(job_id)
        return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)

    @app.get("/api/guides/{job_id}")
    def api_job(job_id: str) -> JSONResponse:
        job = store.get(job_id)
        if not job:
            return _error_json(GuideError("JOB_NOT_FOUND"))
        return JSONResponse(_public(job))

    @app.get("/api/guides/{job_id}/events")
    async def api_events(job_id: str) -> StreamingResponse:
        async def stream():
            last = None
            while True:
                job = store.get(job_id)
                if not job:
                    yield 'data: {"error":{"code":"JOB_NOT_FOUND"}}\n\n'
                    break
                payload = _public(job)
                if payload != last:
                    yield f"data: {json.dumps(payload)}\n\n"
                    last = payload
                if job["status"] in ("done", "failed"):
                    break
                await asyncio.sleep(0.6)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/health")
    def api_health() -> JSONResponse:
        return JSONResponse({"ok": True, "queue_depth": app.state.queue.qsize(), "running": store.running()})

    @app.get("/g/{job_id}", response_class=HTMLResponse)
    def result_page(job_id: str) -> HTMLResponse:
        job = store.get(job_id)
        if not job:
            return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"), status_code=404)
        return HTMLResponse(_render_result_page(job))

    @app.get("/g/{job_id}/guide.md", response_class=PlainTextResponse)
    def guide_md(job_id: str) -> PlainTextResponse:
        job = store.get(job_id)
        if not job or not job["human_md"]:
            raise HTTPException(status_code=404, detail="not ready")
        return PlainTextResponse(job["human_md"], media_type="text/markdown; charset=utf-8")

    @app.get("/g/{job_id}/agent.json", response_class=PlainTextResponse)
    def agent_json(job_id: str) -> PlainTextResponse:
        job = store.get(job_id)
        if not job or not job["agent_json"]:
            raise HTTPException(status_code=404, detail="not ready")
        return PlainTextResponse(job["agent_json"], media_type="application/json; charset=utf-8")

    @app.get("/g/{job_id}/agent.md", response_class=PlainTextResponse)
    def agent_md(job_id: str) -> PlainTextResponse:
        job = store.get(job_id)
        if not job or not job["agent_md"]:
            raise HTTPException(status_code=404, detail="not ready")
        return PlainTextResponse(job["agent_md"], media_type="text/markdown; charset=utf-8")

    return app


app = create_app()
