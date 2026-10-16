"""The workflow: what is final, what is regression, and the gates.

    python tools/agent/test_workflow.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


jobs = _load("jobs_for_workflow", os.path.join(HERE, "jobs.py"))
wf = jobs.workflow
cr = jobs.casereview
sm = cr._suitemap()


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="workflow-")
        self.addCleanup(shutil.rmtree, self.root, True)
        for owner in (jobs, jobs.intake):
            self.addCleanup(setattr, owner, "ROOT", owner.ROOT)
            owner.ROOT = self.root
        self.job = jobs.job_dir("j1")
        os.makedirs(self.job)
        self.write("design.json", {"test_cases": [
            {"id": "TC-001", "title": "Owner lists stays", "type": "positive", "priority": "high",
             "steps": [{"action": "GET", "data": "", "expected": "200"}]},
            {"id": "TC-002", "title": "Bad id", "type": "negative", "priority": "low",
             "steps": [{"action": "GET", "data": "", "expected": "404"}]}]})

    def write(self, name, data):
        with io.open(os.path.join(self.job, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)


class Finalizing(Base):
    def test_finalized_holds_until_an_approved_case_changes(self):
        with self.assertRaises(ValueError):
            wf.finalize(self.job, cr, sm)
        cr.update(self.job, "design", ["TC-001"], state="approved")
        self.assertIn("not finalized", jobs.workflow_gate("j1", "agent-automate", {}))
        wf.finalize(self.job, cr, sm)
        self.assertEqual(jobs.workflow_gate("j1", "agent-automate", {}), "")
        self.assertEqual(jobs.workflow_gate("j1", "xray-create", {"--what": "design"}), "")
        cr.update(self.job, "design", ["TC-001"], title="Owner lists every stay")
        self.assertIn("changed after they were finalized", jobs.workflow_gate("j1", "agent-automate", {}))
        self.assertEqual(jobs.workflow_gate("j1", "xray-create", {"--what": "manual"}), "", "manual is not gated")


class Regression(Base):
    def test_confirmed_for_the_change_waiting_and_needed_to_run(self):
        cr.update(self.job, "design", ["TC-001", "TC-002"], state="approved")
        wf.finalize(self.job, cr, sm)
        self.write("review.json", {"state": "pending-review", "change": "c1"})
        self.write("automated-cases.json", {"TC-001": {"written": True, "change": "c1"},
                                             "TC-002": {"written": True, "change": "c1"}})
        self.assertIn("confirm which automated cases", jobs.workflow_gate("j1", "agent-run-tests", {}))
        r = jobs.workflow_act("j1", "confirm-regression")
        self.assertEqual(r["regression"]["cases"], ["TC-001", "TC-002"], "both carry `regression`")
        self.assertEqual(jobs.workflow_gate("j1", "agent-run-tests", {}), "")
        self.write("review.json", {"state": "pending-review", "change": "c2"})
        self.assertIn("earlier change", jobs.workflow_gate("j1", "agent-run-tests", {}))

    def test_publish_waits_for_a_run_not_for_the_push(self):
        self.write("review.json", {"state": "pending-review"})
        self.assertIn("run the tests first", jobs.workflow_gate("j1", "xray-publish", {}))
        self.write("execution.json", {"ok": False, "at": "2026-10-16T10:00:00Z"})
        self.assertEqual(jobs.workflow_gate("j1", "xray-publish", {}), "",
                         "a failed run of a change not pushed is published too")


class Refining(Base):
    def test_the_request_and_the_cases_as_they_are_now(self):
        cr.update(self.job, "design", ["TC-001"], state="approved", title="Owner lists every stay")
        cr.update(self.job, "design", ["TC-002"], state="rejected")
        listing = jobs.refine_listing("j1", "Split TC-001 by role.")
        self.assertNotIn("Split TC-001", listing, "the request goes among the instructions, not here")
        self.assertIn("TC-001 [APPROVED] [positive, high]  -- Owner lists every stay", listing, "the correction")
        self.assertIn("TC-002 [REJECTED]", listing)
        self.assertIn("do: GET | expect: 200", listing)
        with self.assertRaises(ValueError):
            jobs.refine_listing("j1", "  ")


class TheGatesHold(Base):
    def test_no_code_for_a_designed_job_until_its_cases_are_final(self):
        cr.update(self.job, "design", ["TC-001"], state="approved")
        for runnable, options in (("agent-generate", {"--scope": "new-test"}), ("agent-followup", {}),
                                  ("agent-approve", {}), ("agent-run-tests", {})):
            self.assertIn("not finalized", jobs.workflow_gate("j1", runnable, options), runnable)
        self.assertEqual(jobs.workflow_gate("j1", "agent-discard", {}), "", "Discard is never held back")

    def test_a_follow_up_after_the_confirmation_needs_it_again(self):
        cr.update(self.job, "design", ["TC-001"], state="approved")
        wf.finalize(self.job, cr, sm)
        self.write("review.json", {"state": "pending-review", "change": "c1", "started": "t1"})
        jobs.workflow_act("j1", "confirm-regression")
        self.assertEqual(jobs.workflow_gate("j1", "agent-run-tests", {}), "")
        self.write("review.json", {"state": "pending-review", "change": "c1", "started": "t2"})
        self.assertIn("added to after the regression cases were confirmed",
                      jobs.workflow_gate("j1", "agent-run-tests", {}))

    def test_a_workbench_job_runs_only_its_own_commands(self):
        self.assertIn("job for changing the workbench", jobs.workflow_gate("workbench-1", "agent-run-tests", {}))
        self.assertIn("job for changing the workbench", jobs.workflow_gate("workbench-1", "agent-automate", {}))
        self.assertEqual(jobs.workflow_gate("workbench-1", "agent-generate", {"--scope": "workbench"}), "")


class TheStrip(Base):
    def test_nine_stages_in_order(self):
        names = [s["name"] for s in wf.stages(self.job, cr, sm)]
        self.assertEqual(names, ["Generate", "Review", "Finalize", "Xray tests", "Automate", "Regression",
                                 "Run on stg", "Push", "Publish"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
