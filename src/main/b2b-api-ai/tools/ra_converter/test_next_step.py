"""A convert ends by saying what comes next: each suite it wrote, the
TestNG XML to run it with, and that it is compiled first.

Asked for (2026-10-29): the log of a convert ended with the files it
wrote, and nothing in it said which suite was now to be compiled and run.
Under the workbench (WORKBENCH_CONVERT_SUITES) the same list is written
for the compile check that runs after the convert (tools/agent/convertfix.py).

    python tools/ra_converter/test_next_step.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402


class NextStep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="next-step-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for table in (rc._SUITES_THIS_RUN, rc._SUITE_RUN_XML):
            saved = table.copy()
            self.addCleanup(lambda t=table, s=saved: (t.clear(), t.update(s)))
            table.clear()
        self.addCleanup(os.environ.pop, "WORKBENCH_CONVERT_SUITES", None)
        os.environ.pop("WORKBENCH_CONVERT_SUITES", None)

    def said(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc._report_next_step()
        return out.getvalue()

    def test_each_suite_is_named_with_the_xml_to_run_it(self):
        rc._SUITES_THIS_RUN.update({"amex", "goal"})
        rc._SUITE_RUN_XML.update({"amex": "Suites/Amex_Regression.xml"})
        said = self.said()
        self.assertIn("NEXT: compile what was converted, then run it", said)
        self.assertIn("suite amex: run Suites/Amex_Regression.xml", said)
        self.assertIn("suite goal: run (no suite XML was written for it)", said)
        self.assertIn("mvn -q -DskipTests test-compile", said, "outside the workbench: how to compile")

    def test_under_the_workbench_the_list_is_written_for_the_compile_check(self):
        path = os.path.join(self.tmp, "convert-suites.json")
        os.environ["WORKBENCH_CONVERT_SUITES"] = path
        rc._SUITES_THIS_RUN.add("amex")
        rc._SUITE_RUN_XML["amex"] = "Suites/Amex_Regression.xml"
        said = self.said()
        self.assertIn("the workbench compiles amex now", said)
        with io.open(path, encoding="utf-8") as fh:
            listed = json.load(fh)
        self.assertEqual(listed["suites"], [{"suite": "amex", "xml": "Suites/Amex_Regression.xml"}])

    def test_nothing_converted_says_nothing(self):
        self.assertEqual(self.said(), "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
