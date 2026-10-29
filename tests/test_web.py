import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from guidegen.web import app as webapp
    from guidegen.web.app import create_app
except ImportError:  # pragma: no cover - system python may not have web deps
    TestClient = None
    create_app = None
    webapp = None

from guidegen.pipeline import render_bundle

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "data" / "jobs" / "job_15d800aa7a" / "spec.json"


@unittest.skipUnless(TestClient, "web dependencies are only in .venv")
class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(db_path=Path(self.tmp.name) / "test.db", start_workers=False)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_landing_and_health(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("GUIDE", response.text)
        self.assertIn("Make your agent", response.text)
        health = self.client.get("/api/health").json()
        self.assertTrue(health["ok"])
        self.assertEqual(health["queue_depth"], 0)

    def test_resolve_resolved_ambiguous_and_invalid(self) -> None:
        resolved = self.client.post("/api/resolve", json={"input": "github"}).json()
        self.assertEqual(resolved["decision"], "resolved")
        self.assertEqual(resolved["provider"]["id"], "github")
        ambiguous = self.client.post("/api/resolve", json={"input": "tally"}).json()
        self.assertEqual(ambiguous["decision"], "ask")
        self.assertGreaterEqual(len(ambiguous["candidates"]), 2)
        invalid = self.client.post("/api/resolve", json={"input": ""})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "INPUT_EMPTY")

    def test_create_job_and_running_page(self) -> None:
        response = self.client.post("/api/guides", json={"input": "github"})
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        job = self.client.get(f"/api/guides/{job_id}").json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["provider"]["id"], "github")
        page = self.client.get(f"/g/{job_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("progress-card", page.text)
        self.assertIn(job_id, page.text)

    def test_done_page_and_downloads(self) -> None:
        spec = json.loads(SPEC.read_text())
        human, agent, agent_md = render_bundle(spec)
        job_id = "job_testdone"
        store = self.app.state.store
        store.create(job_id, "github", None, "github", "GitHub", "self_serve_oauth")
        store.update(job_id, status="done", stage="done", progress=100, spec_json=json.dumps(spec),
                     human_md=human, agent_json=json.dumps(agent), agent_md=agent_md,
                     model="fake", provider_name="GitHub")
        page = self.client.get(f"/g/{job_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Human guide", page.text)
        self.assertIn("agent.json", page.text)
        self.assertIn("GITHUB_CLIENT_ID", page.text)
        self.assertEqual(self.client.get(f"/g/{job_id}/guide.md").status_code, 200)
        self.assertEqual(self.client.get(f"/g/{job_id}/agent.json").json()["provider"]["id"], "github")
        self.assertEqual(self.client.get(f"/g/{job_id}/agent.md").status_code, 200)

    def test_catalogue_name_queues_a_job_instead_of_refusing(self) -> None:
        # PLAN §13.8: a toolkit-catalogue name used to die here with PROVIDER_UNKNOWN; the job's
        # first stage is now FIND, so it gets a job like any curated provider.
        response = self.client.post("/api/guides", json={"input": "airtable"})
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["job_id"]
        job = self.client.get(f"/api/guides/{job_id}").json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["provider"]["id"], "airtable")

    def test_ambiguous_guide_requires_a_pick(self) -> None:
        response = self.client.post("/api/guides", json={"input": "tally"})
        self.assertEqual(response.status_code, 409)
        self.assertGreaterEqual(len(response.json()["candidates"]), 2)
        picked = self.client.post("/api/guides", json={"input": "tally", "pick": "tally"})
        self.assertEqual(picked.status_code, 202)

    def test_missing_job(self) -> None:
        self.assertEqual(self.client.get("/api/guides/job_missing").status_code, 404)
        self.assertEqual(self.client.get("/g/job_missing").status_code, 404)

    def test_job_timeout_marks_job_failed(self) -> None:
        store = self.app.state.store
        job_id = "job_timeout"
        store.create(job_id, "github", None, "github", "GitHub", "self_serve_oauth")

        def fake_build(raw, **kwargs):
            kwargs["on_progress"]("extract", 50, chunk=1, total=1)
            raise AssertionError("timeout should have fired first")

        old = webapp.JOB_TIMEOUT_SECONDS
        webapp.JOB_TIMEOUT_SECONDS = 0
        try:
            with patch.object(webapp, "build_spec", side_effect=fake_build):
                webapp._process_job(store, job_id)
        finally:
            webapp.JOB_TIMEOUT_SECONDS = old
        job = store.get(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_code"], "JOB_TIMEOUT")
