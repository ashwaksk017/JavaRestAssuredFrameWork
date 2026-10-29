"""After a convert: compiled; each suite that does not compile is fixed by
Cursor in a job of its own, and reaches review only once it compiles.

    python tools/agent/test_convertfix.py
"""
from __future__ import annotations

import os as _os
_os.environ["WORKBENCH_MOCK"] = "0"

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("convertfix_under_test", os.path.join(HERE, "convertfix.py"))
cf = importlib.util.module_from_spec(spec)
sys.modules["convertfix_under_test"] = cf
spec.loader.exec_module(cf)

AMEX = "src/test/java/com/hi/api/tests/imported/amex/AmexTest.java"
AMEX_STEPS = "src/main/java/com/hi/api/support/amex/AmexSteps.java"
SHARED = "src/main/java/com/hi/api/rest/utilities/RestStep.java"


def err(path, msg="cannot find symbol"):
    return f"[ERROR] /x/{path}:[3,5] {msg}\n"


class Placing(unittest.TestCase):
    def test_a_file_belongs_to_its_suite_or_is_shared(self):
        self.assertEqual(cf.suite_of(AMEX), "amex")
        self.assertEqual(cf.suite_of(AMEX_STEPS), "amex")
        self.assertEqual(cf.suite_of("src/main/resources/templates/goal/x.json"), "goal")
        # A --classic convert writes its tests under tests/classic/<suite>/.
        self.assertEqual(cf.suite_of("src/test/java/com/hi/api/tests/classic/amex/x/ATest.java"), "amex")
        # Its fixes are stored and re-applied like a phase-mode suite's.
        pspec = importlib.util.spec_from_file_location("patches_for_classic", os.path.join(HERE, "patches.py"))
        patches = importlib.util.module_from_spec(pspec)
        pspec.loader.exec_module(patches)
        self.assertIn("src/test/java/com/hi/api/tests/classic/amex/", patches.suite_roots("amex"))
        self.assertEqual(cf.suite_of("src/main/java/com/hi/api/rest/clients/AmexClient.java"), "amex")
        self.assertEqual(cf.suite_of(SHARED), "")
        self.assertEqual(cf.suite_of("src\\test\\java\\com\\hi\\api\\tests\\imported\\Goal\\G.java"), "goal")


class AfterAConvert(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="convertfix-")
        self.addCleanup(shutil.rmtree, self.root, True)
        saved = cf.loop.intake.ROOT
        cf.loop.intake.ROOT = self.root
        self.addCleanup(setattr, cf.loop.intake, "ROOT", saved)
        for k in ("WORKBENCH_CURSOR_LOG_MIRROR", "WORKBENCH_CURSOR_LOG"):
            self.addCleanup(os.environ.pop, k, None)
            os.environ.pop(k, None)
        self.asked = []

    def compiler(self, output, rc=1):
        def run(argv):
            return subprocess.CompletedProcess(argv, rc, output.encode(), b"")
        return run

    def generate(self, ends="pending-review"):
        def gen(job_dir, root, policy, scope, suite, must_fix=()):
            self.asked.append((os.path.basename(job_dir), scope, suite, list(must_fix)))
            with io.open(os.path.join(job_dir, "review.json"), "w", encoding="utf-8") as fh:
                json.dump({"state": ends}, fh)
            return 0 if ends == "pending-review" else 1
        return gen

    def run_it(self, output, rc=1, use_cursor=True, ends="pending-review"):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cf.run("conv1", self.root, use_cursor=use_cursor,
                          runner=self.compiler(output, rc), generate=self.generate(ends))
        with io.open(os.path.join(self.root, ".workbench", "conv1", cf.RESULT), encoding="utf-8") as fh:
            result = json.load(fh)
        return code, out.getvalue(), result

    def test_a_convert_that_compiles_is_ready_and_cursor_is_not_asked(self):
        code, said, result = self.run_it("", rc=0)
        self.assertEqual(code, 0)
        self.assertTrue(result["compiles"])
        self.assertEqual(self.asked, [])
        self.assertIn("ready to review", said)

    def test_a_suite_that_does_not_compile_is_fixed_in_its_own_job_and_must_compile(self):
        code, said, result = self.run_it(err(AMEX) + err(AMEX_STEPS, "incompatible types"))
        self.assertEqual(code, 0, said)
        self.assertEqual(self.asked, [("conv1-fix-amex", "converted", "amex", sorted([AMEX, AMEX_STEPS]))])
        with io.open(os.path.join(self.root, ".workbench", "conv1-fix-amex", "brief.md"), encoding="utf-8") as fh:
            brief = fh.read()
        self.assertIn("incompatible types", brief)
        self.assertIn("Do not delete a test", brief)
        self.assertEqual(result["suites"]["amex"]["state"], "pending-review")
        self.assertIn("waiting for review", said)
        self.assertEqual(os.environ["WORKBENCH_CURSOR_LOG_MIRROR"],
                         os.path.join(self.root, ".workbench", "conv1", "migration-cursor.log"),
                         "the fix's conversation is also in the Convert tab's AI Agent log")

    def test_a_suite_cursor_could_not_fix_is_not_called_ready(self):
        code, said, result = self.run_it(err(AMEX), ends="verify-failed")
        self.assertEqual(code, 1)
        self.assertEqual(result["suites"]["amex"]["state"], "verify-failed")
        self.assertIn("still does not compile", said)

    def test_shared_framework_errors_are_said_and_left_to_a_person(self):
        code, said, result = self.run_it(err(SHARED))
        self.assertEqual(code, 1)
        self.assertEqual(self.asked, [])
        self.assertEqual(result["shared"], [SHARED])
        self.assertIn("not a converted suite's to fix", said)

    def test_with_cursor_off_it_is_only_checked(self):
        code, said, result = self.run_it(err(AMEX), use_cursor=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.asked, [])
        self.assertIn("AI Agent is off", said)

    def test_a_fix_already_waiting_is_not_asked_again(self):
        sub = os.path.join(self.root, ".workbench", "conv1-fix-amex")
        os.makedirs(sub)
        with io.open(os.path.join(sub, "review.json"), "w", encoding="utf-8") as fh:
            json.dump({"state": "pending-review"}, fh)
        code, said, result = self.run_it(err(AMEX))
        self.assertEqual(self.asked, [])
        self.assertIn("already holds a fix", said)

    def test_it_is_a_runnable_and_its_result_is_readable(self):
        jobs = cf._load("convertfix_jobs", os.path.join(HERE, "jobs.py"))
        self.assertEqual(set(jobs.RUNNABLES["convert-fix"]["options"]), {"--job", "--no-cursor"})
        self.assertIn("convert-fix", jobs.EXCLUSIVE)
        self.assertNotIn("not a readable", str(self._readable(jobs, "convert-fix.json")))
        self.assertNotIn("not a readable", str(self._readable(jobs, "xray-publish.json")),
                         "Publish to Xray's result: the page reads it")

    def test_the_records_after_a_convert_are_readable(self):
        jobs = cf._load("convertfix_jobs", os.path.join(HERE, "jobs.py"))
        for name in ("convert-check.json", "convert-suites.json"):
            self.assertNotIn("not a readable", str(self._readable(jobs, name)), name)

    @staticmethod
    def _readable(jobs, name):
        try:
            return jobs.read_artifact("no-such-job-here", name)
        except ValueError as e:
            return str(e)
        except Exception:                                # noqa: BLE001
            return "readable"


GOAL = "src/test/java/com/hi/api/tests/imported/goal/GoalTest.java"


class TheConvertsOwnSuites(AfterAConvert):
    """The convert lists the suites it wrote (convert-suites.json): the
    check names them and the XML to run each with, and sends only them to
    AI Agent -- another suite's errors are said, not fixed (2026-10-29)."""

    # The fixture is borrowed; AfterAConvert's own tests run once, there.
    test_a_convert_that_compiles_is_ready_and_cursor_is_not_asked = None
    test_a_suite_that_does_not_compile_is_fixed_in_its_own_job_and_must_compile = None
    test_a_suite_cursor_could_not_fix_is_not_called_ready = None
    test_shared_framework_errors_are_said_and_left_to_a_person = None
    test_with_cursor_off_it_is_only_checked = None
    test_a_fix_already_waiting_is_not_asked_again = None
    test_it_is_a_runnable_and_its_result_is_readable = None
    test_the_records_after_a_convert_are_readable = None

    def setUp(self):
        super().setUp()
        d = os.path.join(self.root, ".workbench", "conv1")
        os.makedirs(d, exist_ok=True)
        with io.open(os.path.join(d, cf.CONVERTED), "w", encoding="utf-8") as fh:
            json.dump({"suites": [{"suite": "Amex", "xml": "Suites/Amex_Regression.xml"}]}, fh)

    def test_it_names_the_suite_and_the_xml_to_run_it_with(self):
        code, said, result = self.run_it("", rc=0)
        self.assertEqual(code, 0)
        self.assertIn("this convert wrote suite(s) amex", said)
        self.assertIn("suite amex compiles: run Suites/Amex_Regression.xml", said)
        self.assertEqual(result["converted"], {"amex": "Suites/Amex_Regression.xml"})

    def test_only_the_converts_suite_goes_to_ai_agent(self):
        code, said, result = self.run_it(err(AMEX) + err(GOAL))
        self.assertEqual([a[2] for a in self.asked], ["amex"])
        self.assertEqual(list(result["others"]), ["goal"])
        self.assertIn("suite(s) goal do not compile either", said)
        self.assertIn("once it is approved, run Suites/Amex_Regression.xml", said)
        self.assertEqual(code, 1, "the project does not compile while goal does not")

    def test_another_suites_errors_alone_ask_nobody(self):
        code, said, result = self.run_it(err(GOAL))
        self.assertEqual((code, self.asked), (1, []))
        self.assertIn("suite amex has no errors of its own -- it runs once the files above compile", said)

    def test_without_the_list_every_suite_is_the_checks_as_before(self):
        os.remove(os.path.join(self.root, ".workbench", "conv1", cf.CONVERTED))
        code, said, result = self.run_it(err(AMEX) + err(GOAL))
        self.assertEqual(sorted(a[2] for a in self.asked), ["amex", "goal"])
        self.assertEqual(result["others"], {})


if __name__ == "__main__":
    unittest.main(verbosity=1)
