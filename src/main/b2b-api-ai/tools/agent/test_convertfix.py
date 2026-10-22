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
                         "the fix's conversation is also in the Convert tab's Cursor log")

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
        self.assertIn("Cursor is off", said)

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

    @staticmethod
    def _readable(jobs, name):
        try:
            return jobs.read_artifact("no-such-job-here", name)
        except ValueError as e:
            return str(e)
        except Exception:                                # noqa: BLE001
            return "readable"


if __name__ == "__main__":
    unittest.main(verbosity=1)
