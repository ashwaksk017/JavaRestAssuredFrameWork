"""Unit tests for tools/allure_history.py (no Maven / Allure CLI)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import allure_history as ah  # noqa: E402


class AllureHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.history = self.root / "allure-history"
        self.results = self.root / "target" / "allure-results"
        self.report = self.root / "target" / "allure-report"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_history(self, folder: Path, **files: str) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        payload = files or {"history-trend.json": "[]", "history.json": "{}"}
        for name, body in payload.items():
            (folder / name).write_text(body, encoding="utf-8")

    def test_restore_copies_cache_into_results_history(self):
        self._write_history(self.history, **{"history-trend.json": "[1]", "history.json": '{"a":1}'})
        n = ah.restore(self.history, self.results)
        self.assertEqual(n, 2)
        dest = self.results / "history"
        self.assertEqual((dest / "history-trend.json").read_text(encoding="utf-8"), "[1]")
        self.assertEqual((dest / "history.json").read_text(encoding="utf-8"), '{"a":1}')

    def test_restore_empty_cache_is_noop(self):
        n = ah.restore(self.history, self.results)
        self.assertEqual(n, 0)
        self.assertFalse((self.results / "history").exists())

    def test_save_replaces_cache_from_report(self):
        self._write_history(self.history, **{"history-trend.json": "old"})
        self._write_history(
            self.report / "history",
            **{"history-trend.json": "new", "duration-trend.json": "[]"},
        )
        n = ah.save(self.report, self.history)
        self.assertEqual(n, 2)
        self.assertEqual((self.history / "history-trend.json").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.history / "duration-trend.json").exists())
        self.assertFalse((self.history / "stale.json").exists())

    def test_save_without_report_history_keeps_cache(self):
        self._write_history(self.history, **{"history.json": "keep"})
        n = ah.save(self.report, self.history)
        self.assertEqual(n, 0)
        self.assertEqual((self.history / "history.json").read_text(encoding="utf-8"), "keep")

    def test_cli_restore_then_save_roundtrip(self):
        self._write_history(self.history, **{"history-trend.json": '[{"buildOrder":1}]'})
        rc = ah.main(
            [
                "restore",
                "--root",
                str(self.root),
                "--history-dir",
                "allure-history",
                "--results-dir",
                "target/allure-results",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue((self.results / "history" / "history-trend.json").exists())
        self._write_history(
            self.report / "history",
            **{"history-trend.json": '[{"buildOrder":1},{"buildOrder":2}]'},
        )
        rc = ah.main(
            [
                "save",
                "--root",
                str(self.root),
                "--history-dir",
                "allure-history",
                "--report-dir",
                "target/allure-report",
            ]
        )
        self.assertEqual(rc, 0)
        trend = (self.history / "history-trend.json").read_text(encoding="utf-8")
        self.assertIn("buildOrder\":2", trend.replace(" ", ""))

    def test_github_executor_from_env(self):
        payload = ah.executor_from_env(
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_REPOSITORY": "acme/api-tests",
                "GITHUB_RUN_ID": "99",
                "GITHUB_RUN_NUMBER": "7",
                "GITHUB_RUN_ATTEMPT": "1",
            }
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["type"], "github")
        self.assertEqual(payload["buildOrder"], 7)
        self.assertEqual(payload["buildUrl"], "https://github.com/acme/api-tests/actions/runs/99")

    def test_gitlab_executor_from_env(self):
        payload = ah.executor_from_env(
            {
                "GITLAB_CI": "true",
                "CI_PROJECT_URL": "https://gitlab.example/g/api",
                "CI_PIPELINE_URL": "https://gitlab.example/g/api/-/pipelines/12",
                "CI_PIPELINE_ID": "12",
                "CI_PIPELINE_IID": "4",
                "CI_PAGES_URL": "https://g.gitlab.io/api",
            }
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["type"], "gitlab")
        self.assertEqual(payload["buildOrder"], 4)
        self.assertEqual(payload["reportUrl"], "https://g.gitlab.io/api")

    def test_executor_skipped_locally(self):
        self.assertIsNone(ah.executor_from_env({}))

    def test_write_executor_json(self):
        path = ah.write_executor(self.results, {"name": "GitHub Actions", "buildOrder": 1})
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["buildOrder"], 1)


if __name__ == "__main__":
    unittest.main()
