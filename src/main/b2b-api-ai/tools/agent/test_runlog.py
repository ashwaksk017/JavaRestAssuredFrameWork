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


class WhatTheRunShows(unittest.TestCase):
    """Seen live on the Run tests tab: a failed test's block was shown EMPTY
    and the WARN naming the cause was not shown at all. These are that
    run's own lines."""

    LINES = [
        "14:37:19.292 [main] WARN  org.testng.TestNG - Ignoring duplicate listener : io.qameta.allure.testng.AllureTestNg",
        "14:37:19.719 [main] INFO  RUN - RUN START suite jira-job-1 | env stg | project b2b",
        "14:37:22.313 [main] INFO  com.hi.api.rest.ApiRoutes - ApiRoutes: loaded 0 path override(s) from routes.json",
        "14:37:29.785 [main] WARN  com.hi.api.tests.StoryTestBase - enroll.source_code / enroll.prop_code are not set for env 'stg'",
        "[Retry] Abc1Test.rejects_400 -- attempt 1/2",
        "14:37:31.436 [main] ERROR RUN - ==== TEST FAILED: Abc1Test.rejects_400  [case TC-001 | story ABC-1 | env stg | 0.5 s]",
        "  why        : UnresolvedPlaceholderException: Unresolved placeholders: [#enroll_source_code#, #enroll_prop_code#]",
        "  calls      : POST /v2/realms/applications/token -> 200",
        "  answered   : {\"access_token\":\"***REDACTED***\"}",
        "==== end of TEST FAILED",
        "14:37:31.448 [main] INFO  RUN - TEST END   Abc1Test.rejects_400  FAILED  [case TC-001]",
        "[digest] 0 check(s) recorded as skipped. If the run logged `[assert SKIPPED]` lines, ...",
        "[INFO] Download progress 45%",
        "14:37:33.563 [main] INFO  RUN - RUN END   suite jira-job-1 | 0 passed, 2 failed, 0 skipped",
    ]

    def test_a_failed_tests_block_whole_every_warn_and_where_the_run_is(self):
        shown = rl.Shown()
        out = [t for t in (shown(l) for l in self.LINES) if t is not None]
        text = "\n".join(out)
        for must in ("RUN START", "enroll.source_code / enroll.prop_code are not set", "[Retry]",
                     "==== TEST FAILED", "  why        : UnresolvedPlaceholderException",
                     "  calls      : POST", "  answered   :", "==== end of TEST FAILED",
                     "TEST END", "RUN END"):
            self.assertIn(must, text)
        for never in ("Ignoring duplicate listener", "[digest] 0 check(s)", "Download progress", "ApiRoutes"):
            self.assertNotIn(never, text)

    def test_a_long_block_line_is_kept_long_enough_to_read_the_server(self):
        shown = rl.Shown()
        shown("==== TEST FAILED: X")
        answer = "  answered   : " + "x" * 2000
        self.assertEqual(len(shown(answer)), len(answer))


if __name__ == "__main__":
    unittest.main(verbosity=1)
