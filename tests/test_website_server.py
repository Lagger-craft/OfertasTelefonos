"""Tests for the website server's search endpoint plumbing (S6-S7)."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import website_server  # noqa: E402


def _stub_script(tmp: Path, body: str) -> str:
    script = tmp / "stub.py"
    script.write_text(body)
    return str(script)


class RunSearchTests(unittest.TestCase):
    def test_successful_search_returns_structured_result(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            stub = _stub_script(tmp_dir := Path(tmp_dir), "print('collected 4 offers')\n")
            result = website_server.run_search(stub, timeout=30)
            self.assertTrue(result["ok"])
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("collected 4 offers", result["tail"])
            self.assertGreaterEqual(result["duration_s"], 0.0)
            self.assertIn("finished_at", result)

    def test_failing_search_reports_error_and_keeps_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = _stub_script(Path(tmp), "import sys; sys.stderr.write('boom'); sys.exit(3)\n")
            result = website_server.run_search(stub, timeout=30)
            self.assertFalse(result["ok"])
            self.assertEqual(result["exit_code"], 3)
            self.assertIn("boom", result["tail"])
            for key in ("exit_code", "duration_s", "tail", "finished_at"):
                self.assertIn(key, result)

    def test_serialized_result_is_json_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = _stub_script(Path(tmp), "print('ok')\n")
            result = website_server.run_search(stub, timeout=30)
            json.dumps(result)  # must not raise

    def test_hung_search_is_killed_by_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = _stub_script(Path(tmp), "import time; time.sleep(10)\n")
            started = time.monotonic()
            result = website_server.run_search(stub, timeout=1)
            self.assertFalse(result["ok"])
            self.assertLess(time.monotonic() - started, 9)
            self.assertTrue(result["tail"])


if __name__ == "__main__":
    unittest.main()
