"""target/b2b-full-run.log — hints when it did not refresh.

    python tools/agent/test_runlog.py
"""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("runlog_under_test", os.path.join(HERE, "runlog.py"))
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)


class RunLog(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="runlog-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_missing_log_says_surefire_never_started(self):
        text = rl.text_for_page(self.root)
        self.assertIn("WHY IT DID NOT UPDATE", text)
        self.assertIn("Surefire starts TestNG", text)

    def test_stale_log_when_results_are_newer(self):
        log = os.path.join(self.root, rl.RUN_LOG_REL)
        res = os.path.join(self.root, rl.RESULTS_REL)
        os.makedirs(os.path.dirname(log), exist_ok=True)
        os.makedirs(os.path.dirname(res), exist_ok=True)
        with io.open(log, "w", encoding="utf-8") as fh:
            fh.write("old line\n")
        old = time.time() - 120
        os.utime(log, (old, old))
        with io.open(res, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?><testng-results total="2" passed="2" failed="0" skipped="0">'
                       '<suite name="s"><test name="t"><class name="C">'
                       '<test-method status="PASS" name="a"/></class></test></suite></testng-results>')
        text = rl.text_for_page(self.root)
        self.assertIn("WHY IT LOOKS STALE", text)
        self.assertIn("tests in results: 2", text)

    def test_zero_tests_is_called_out(self):
        res = os.path.join(self.root, rl.RESULTS_REL)
        os.makedirs(os.path.dirname(res), exist_ok=True)
        with io.open(res, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?><testng-results total="0" passed="0" failed="0" skipped="0"/>')
        self.assertIn("zero tests", rl.text_for_page(self.root).lower())

    def test_after_maven_warns_when_mtime_unchanged(self):
        log = os.path.join(self.root, rl.RUN_LOG_REL)
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with io.open(log, "w", encoding="utf-8") as fh:
            fh.write("line\n")
        past = time.time() - 300
        os.utime(log, (past, past))
        self.assertIn("not refreshed", rl.after_maven(self.root, time.time())["warn"])

    def test_log_level_flag_defaults_to_debug(self):
        self.assertEqual(rl.log_level_flag({}), "-Dlog.level=debug")
        self.assertEqual(rl.log_level_flag({"run_log_level": "info"}), "-Dlog.level=info")

    def test_run_log_with_workbench_xray_block_is_called_out(self):
        log = os.path.join(self.root, rl.RUN_LOG_REL)
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with io.open(log, "w", encoding="utf-8") as fh:
            fh.write("line\n--- workbench Xray diagnostics (Run the tests) ---\n")
        text = rl.text_for_page(self.root)
        self.assertIn("appended an Xray trace", text)

    def test_fresh_log_and_results_still_name_surefire(self):
        log = os.path.join(self.root, rl.RUN_LOG_REL)
        res = os.path.join(self.root, rl.RESULTS_REL)
        os.makedirs(os.path.dirname(log), exist_ok=True)
        os.makedirs(os.path.dirname(res), exist_ok=True)
        now = time.time()
        with io.open(log, "w", encoding="utf-8") as fh:
            fh.write("recent framework line\n")
        with io.open(res, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?><testng-results total="1" passed="1" failed="0" skipped="0"/>')
        os.utime(log, (now, now))
        os.utime(res, (now, now))
        text = rl.text_for_page(self.root)
        self.assertIn("Surefire", text)
        self.assertNotIn("WHY IT LOOKS STALE", text)


class DiagApi(unittest.TestCase):
    def test_jobs_exposes_the_run_log(self):
        spec = importlib.util.spec_from_file_location("jobs_runlog_test", os.path.join(HERE, "jobs.py"))
        jobs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(jobs)
        root = tempfile.mkdtemp(prefix="runlog-jobs-")
        self.addCleanup(shutil.rmtree, root, True)
        res = os.path.join(root, rl.RESULTS_REL)
        os.makedirs(os.path.dirname(res), exist_ok=True)
        with io.open(res, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?><testng-results total="1" passed="1" failed="0" skipped="0"/>')
        orig = jobs.ROOT
        jobs.ROOT = root
        self.addCleanup(setattr, jobs, "ROOT", orig)
        text = jobs.read_diag("", which="run")
        self.assertIn("testng-results.xml", text)
        self.assertIn("Surefire", text)
        self.assertIn("XRAY TRACE", text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
