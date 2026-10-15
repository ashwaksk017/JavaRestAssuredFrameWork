"""Reading a run's results, and the push gate's verdict.

    python tools/agent/test_execution.py
"""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("execution_under_test", os.path.join(HERE, "execution.py"))
ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex)

RESULTS = '<?xml version="1.0" encoding="UTF-8"?>\n<testng-results ignored="0" total="4" passed="1" failed="1" skipped="2">\n  <suite name="s"><test name="t">\n    <class name="com.hi.api.tests.jira.NewTest">\n      <test-method status="PASS" name="setUp" is-config="true" duration-ms="1"/>\n      <test-method status="PASS" name="get_200" duration-ms="120"/>\n      <test-method status="FAIL" name="get_403" duration-ms="80">\n        <exception class="java.lang.AssertionError"><message><![CDATA[expected [403] but found [200]]]></message></exception>\n      </test-method>\n      <test-method status="SKIP" name="get_404" duration-ms="1">\n        <exception class="org.testng.SkipException"><message><![CDATA[config key jira.abc-1.owner.guest_id is not set]]></message></exception>\n      </test-method>\n      <test-method status="SKIP" name="row_off" duration-ms="1">\n        <exception class="org.testng.SkipException"><message><![CDATA[execute=N [test_case_id=r] -- switched off in the data sheet, not a failure]]></message></exception>\n      </test-method>\n    </class>\n  </test></suite>\n</testng-results>\n'


class Reading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="execution-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "testng-results.xml")
        with io.open(self.path, "w", encoding="utf-8") as fh:
            fh.write(RESULTS)

    def test_each_test_with_its_status_and_why(self):
        r = ex.read(self.path)
        self.assertEqual((r["passed"], r["failed"], r["skipped"], r["switched_off"]), (1, 1, 1, 1))
        self.assertEqual([t["method"] for t in r["tests"]], ["get_200", "get_403", "get_404", "row_off"],
                         "a configuration method that passed is not a test")
        self.assertEqual(r["tests"][1]["message"], "expected [403] but found [200]")

    def test_the_gate_wants_everything_to_pass(self):
        self.assertEqual(ex.verdict(ex.read(self.path))[0], False)
        self.assertEqual(ex.verdict({"passed": 2, "failed": 0, "skipped": 0, "switched_off": 3})[0], True,
                         "a row switched off in its sheet does not block")
        self.assertIn("skipped", ex.verdict({"passed": 2, "failed": 0, "skipped": 1, "switched_off": 0})[1])
        self.assertIn("nothing was verified", ex.verdict({"passed": 0, "failed": 0, "skipped": 0, "switched_off": 0})[1])

    def test_no_results_is_said(self):
        with self.assertRaises(ValueError):
            ex.read(os.path.join(self.dir, "none.xml"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
