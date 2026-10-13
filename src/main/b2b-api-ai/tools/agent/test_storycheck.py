"""Has the story changed since the job read it?

    python tools/agent/test_storycheck.py
"""
from __future__ import annotations

import os as _os
_os.environ["WORKBENCH_MOCK"] = "0"

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


sc = _load("storycheck_under_test", os.path.join(HERE, "storycheck.py"))
intake = sc.intake

CONN = {"base": "https://jira.example.test", "bases": ["https://jira.example.test"],
        "token": "tok", "timeout": 5, "api": "/rest/api/2", "cleartext": False}


def issue(desc, updated="2026-10-01T10:00:00.000+0000"):
    return {"key": "ABC-1", "fields": {"summary": "Add a business", "description": desc,
                                       "status": {"name": "Open"}, "issuetype": {"name": "Story"},
                                       "comment": {"comments": [], "total": 0}, "updated": updated}}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="storycheck-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.job = intake.job_dir("j", self.root)
        os.makedirs(self.job)
        self.now = issue("h2. Acceptance Criteria\n* a\n* b\n\nPOST /a/b\n")

        def transport(url, token, timeout):
            if "/comment" in url:
                return {"comments": [], "total": 0}
            return json.loads(json.dumps(self.now))
        self.transport = transport
        for owner, name, value in ((intake.jira_search, "connect", lambda: dict(CONN)),
                                   (intake.jira_fetch, "_urllib_transport", transport),
                                   (intake.projectconfig, "section", lambda n: ({}, "t"))):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
            setattr(owner, name, value)
        # The job read the story as it is now.
        intake.build("j", root=self.root, links=["ABC-1"])

    def check(self):
        return sc.check("j", self.root, connect=lambda: dict(CONN), transport=self.transport)


class Checking(Base):
    def test_an_unchanged_story_is_said_to_be_as_it_was(self):
        r = self.check()
        self.assertTrue(r["ok"])
        self.assertFalse(r["changed"])
        self.assertIn("is as this job read it", r["message"])
        self.assertEqual(r["diff"], "")

    def test_a_changed_text_is_shown_as_a_diff_with_what_is_at_stake(self):
        with io.open(os.path.join(self.job, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_cases": [{"id": "TC-001"}]}, fh)
        with io.open(os.path.join(self.job, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("# plan\n")
        self.now = issue("h2. Acceptance Criteria\n* a\n* b\n* c, the new one\n\nPOST /a/b\n",
                         updated="2026-10-02T10:00:00.000+0000")
        r = self.check()
        self.assertTrue(r["ok"] and r["changed"])
        self.assertIn("its text changed", r["message"])
        self.assertIn("\n+* c, the new one", r["diff"])
        self.assertTrue(any("a design of 1 case(s)" in s for s in r["at_stake"]))
        self.assertTrue(any("a plan" in s for s in r["at_stake"]))
        self.assertEqual(r["before"]["revision"][:10], "2026-10-01")
        self.assertEqual(r["after"]["revision"][:10], "2026-10-02")

    def test_a_revision_change_with_the_same_text_is_said_as_such(self):
        self.now = issue(self.now["fields"]["description"], updated="2026-10-03T10:00:00.000+0000")
        r = self.check()
        self.assertTrue(r["changed"])
        self.assertIn("revision changed, though the text", r["message"])
        self.assertEqual(r["diff"], "")

    def test_a_view_or_a_watch_is_not_a_change(self):
        # Jira's hash of the whole issue moves with lastViewed and watches;
        # the `updated` time does not.
        self.now = dict(self.now, fields=dict(self.now["fields"], lastViewed="2026-10-09T01:02:03.000+0000",
                                              watches={"watchCount": 3}))
        r = self.check()
        self.assertTrue(r["ok"])
        self.assertFalse(r["changed"], r["message"])

    def test_a_long_story_that_did_not_change_is_not_said_to_have(self):
        long = "h2. Acceptance Criteria\n" + "\n".join(f"* criterion {n} " + "x" * 60 for n in range(300))
        self.now = issue(long + "\n\nPOST /a/b\n")
        intake.build("j", root=self.root, links=["ABC-1"])
        told = json.load(io.open(os.path.join(self.job, "intake.json"), encoding="utf-8"))
        self.assertGreater(told["story_text_cut"], 0, "the fixture must be longer than the brief keeps")
        r = self.check()
        self.assertFalse(r["changed"], r["message"])

    def test_criteria_that_disappear_are_named(self):
        self.now = issue("Just prose now.\n\nPOST /a/b\n", updated="2026-10-04T10:00:00.000+0000")
        r = self.check()
        self.assertIn("Acceptance criteria disappeared", r["message"])

    def test_a_job_with_no_story_and_a_story_that_cannot_be_read_again(self):
        other = intake.job_dir("k", self.root)
        os.makedirs(other)
        r = sc.check("k", self.root)
        self.assertFalse(r["ok"])
        self.assertIn("no story", r["message"])

        def gone(url, token, timeout):
            raise intake.jira_search.JiraError(404, "Issue Does Not Exist")
        r = sc.check("j", self.root, connect=lambda: dict(CONN), transport=gone)
        self.assertFalse(r["ok"])
        self.assertIn("could not be read again", r["message"])
        self.assertFalse(os.path.exists(os.path.join(self.job, "story-check")), "scratch is cleaned on failure too")

    def test_the_diff_is_bounded_and_the_scratch_folder_is_cleaned(self):
        self.now = issue("\n".join(f"line {n}" for n in range(2000)) + "\n\nPOST /a/b\n",
                         updated="2026-10-05T10:00:00.000+0000")
        r = self.check()
        self.assertLessEqual(len(r["diff"].splitlines()), sc.MAX_DIFF_LINES)
        self.assertGreater(r["diff_cut"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.job, "story-check")))

    def test_the_command_writes_its_result(self):
        real = intake.ROOT
        self.addCleanup(setattr, intake, "ROOT", real)
        intake.ROOT = self.root
        self.assertEqual(sc.main(["--job", "j"]), 0)
        with io.open(os.path.join(self.job, sc.RESULT), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["kind"], "story-check")


if __name__ == "__main__":
    unittest.main(verbosity=1)
