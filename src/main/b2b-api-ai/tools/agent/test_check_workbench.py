"""What a change to the workbench is checked with (check_workbench.py).

    python tools/agent/test_check_workbench.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("check_workbench_under_test", os.path.join(HERE, "check_workbench.py"))
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)


class WhatAChangeCallsFor(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cw-")
        self.addCleanup(shutil.rmtree, self.root, True)
        for rel, text in {"tools/agent/loop.py": "", "tools/agent/test_loop.py": "",
                          "tools/agent/devcode.py": "", "tools/agent/test_new_thing.py": "",
                          "tools/jira/shapes.py": "", "tools/jira/test_shapes.py": "",
                          "tools/ra_converter/ra_converter.py": "",
                          "tools/ra_converter/test_routing.py": "import ra_converter\n",
                          "tools/ra_converter/test_other.py": "nothing here\n",
                          "tools/ra_converter/helper.py": ""}.items():
            path = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(text)

    def extra(self, changed):
        return [t for t, _why in cw.tests_for(changed, self.root)]

    def test_the_ai_agents_own_rules_bring_test_loop(self):
        self.assertEqual(self.extra(["tools/agent/loop.py"]), ["tools/agent/test_loop.py"])
        self.assertEqual(self.extra(["tools/agent/cursor_call.py"]), ["tools/agent/test_loop.py"])

    def test_a_file_outside_tools_agent_brings_its_own_test_or_the_tests_that_name_it(self):
        self.assertEqual(self.extra(["tools/jira/shapes.py"]), ["tools/jira/test_shapes.py"])
        self.assertEqual(self.extra(["tools/ra_converter/ra_converter.py"]),
                         ["tools/ra_converter/test_routing.py"])
        self.assertEqual(self.extra(["tools/ra_converter/helper.py"]), [], "no test names it: nothing extra")

    def test_a_changed_or_new_test_runs_itself_and_the_fixed_list_is_not_repeated(self):
        self.assertEqual(self.extra(["tools/agent/test_new_thing.py"]), ["tools/agent/test_new_thing.py"])
        self.assertEqual(self.extra(["tools/agent/test_jobs.py", "tools/agent/devcode.py"]), [])

    def test_a_deleted_file_or_not_python_brings_nothing(self):
        self.assertEqual(self.extra(["tools/jira/gone.py", "tools/agent/ui/app.js", "README.md"]), [])


class ThePageChecks(unittest.TestCase):
    def answers(self, *results):
        said = list(results)
        calls = []

        def run(argv, timeout):
            calls.append(argv)
            return said.pop(0)
        return run, calls

    def check(self, run):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            verdict = cw.page_checks(run)
        return verdict, out.getvalue()

    def test_failing_once_then_passing_passes_and_says_the_first_was_timing(self):
        run, calls = self.answers((1, "FAIL Defects tab: a refused write\n247 of 248 checks passed"),
                                  (0, "248 of 248 checks passed"))
        verdict, said = self.check(run)
        self.assertEqual(verdict, "")
        self.assertEqual(len(calls), 2)
        self.assertIn("failed once", said)
        self.assertIn("timing", said)

    def test_failing_twice_fails(self):
        run, calls = self.answers((1, "FAIL a\n247 of 248 checks passed"), (1, "FAIL a\n247 of 248 checks passed"))
        verdict, said = self.check(run)
        self.assertEqual(verdict, "the page checks")
        self.assertIn("twice", said)

    def test_passing_first_time_runs_once_and_no_browser_is_a_skip(self):
        run, calls = self.answers((0, "248 of 248 checks passed"))
        self.assertEqual(self.check(run)[0], "")
        self.assertEqual(len(calls), 1)
        run, calls = self.answers((2, "no browser: nothing was checked"))
        self.assertEqual(self.check(run)[0], "")
        self.assertEqual(len(calls), 1)


class WhatChanged(unittest.TestCase):
    def test_changed_and_new_files_from_git_relative_to_the_project(self):
        def run(argv, timeout):
            if "diff" in argv:
                return 0, "tools/agent/loop.py\ntools\\jira\\shapes.py\n"
            return 0, "tools/agent/test_new_thing.py\n"
        self.assertEqual(cw.changed_files(run),
                         ["tools/agent/loop.py", "tools/agent/test_new_thing.py", "tools/jira/shapes.py"])

    def test_git_not_answering_means_nothing_extra(self):
        self.assertEqual(cw.changed_files(lambda argv, timeout: (128, "fatal")), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
