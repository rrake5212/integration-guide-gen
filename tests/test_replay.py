import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from guidegen.debug import debug
from guidegen.replay import replay

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden" / "github"


class ReplayTests(unittest.TestCase):
    def test_replay_rebuilds_and_passes_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job_golden"
            job.mkdir()
            shutil.copy(GOLDEN / "spec.json", job / "spec.json")
            out = Path(tmp) / "out"
            self.assertEqual(replay(job, out), 0)
            self.assertTrue((out / "guide.md").exists())
            self.assertTrue((out / "agent.json").exists())
            validation = json.loads((out / "validation.json").read_text())
            self.assertTrue(validation["ok"])

    def test_debug_prints_state_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job_x"
            job.mkdir()
            shutil.copy(GOLDEN / "spec.json", job / "spec.json")
            (job / "events.jsonl").write_text(json.dumps({"ts": "t", "stage": "extract", "event": "chunk"}) + "\n")
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = debug(str(job))
            self.assertEqual(code, 0)
            output = buffer.getvalue()
            self.assertIn("job_x", output)
            self.assertIn("complete", output)
            self.assertIn("python3 -m guidegen.replay job_x", output)
