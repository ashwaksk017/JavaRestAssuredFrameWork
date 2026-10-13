"""A person's decision on proposed cases is kept, validated, and applied.

    python tools/agent/test_casereview.py
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
_spec = importlib.util.spec_from_file_location("casereview_under_test", os.path.join(HERE, "casereview.py"))
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)

CASES = [{"id": "TC-001", "title": "one", "steps": [{"action": "a", "data": "", "expected": "1"}]},
         {"id": "TC-002", "title": "two", "steps": [{"action": "b", "data": "", "expected": "2"}]},
         {"id": "TC-003", "title": "three", "steps": [{"action": "c", "data": "", "expected": "3"}]}]


class Base(unittest.TestCase):
    def setUp(self):
        self.job = tempfile.mkdtemp(prefix="casereview-")
        self.addCleanup(shutil.rmtree, self.job, True)


class Deciding(Base):
    def test_nothing_decided_is_every_case_proposed(self):
        review = cr.read(self.job)
        self.assertEqual(review["design"], {})
        got = cr.apply(CASES, review["design"])
        self.assertEqual([c["review"]["state"] for c in got], ["proposed"] * 3)
        self.assertFalse(cr.reviewed(review["design"]))
        self.assertEqual(cr.approved(CASES, review["design"]), [])

    def test_a_decision_is_kept_and_applied(self):
        cr.update(self.job, "design", "TC-001", state="approved")
        cr.update(self.job, "design", ["TC-002", "TC-003"], state="rejected", note="dupes")
        entries = cr.read(self.job)["design"]
        self.assertTrue(cr.reviewed(entries))
        self.assertEqual([c["id"] for c in cr.approved(CASES, entries)], ["TC-001"])
        self.assertEqual(cr.summary(CASES, entries),
                         {"total": 3, "approved": 1, "rejected": 2, "proposed": 0, "automate": 1,
                          "reviewed": True, "stale_review": False})
        self.assertEqual(entries["TC-002"]["note"], "dupes")

    def test_a_corrected_title_and_steps_replace_the_proposed_ones(self):
        cr.update(self.job, "design", "TC-001", title="  one, but clearer \n", state="approved",
                  steps=[{"action": "do it", "data": "", "expected": "201"},
                         {"action": "", "data": "", "expected": ""}])
        got = cr.apply(CASES, cr.read(self.job)["design"])[0]
        self.assertEqual(got["title"], "one, but clearer")
        self.assertEqual(got["steps"], [{"action": "do it", "data": "", "expected": "201"}])
        self.assertEqual(got["review"]["edited"], ["title", "steps"])
        self.assertEqual(cr.apply(CASES, cr.read(self.job)["design"])[1]["review"]["edited"], [])

    def test_automate_is_its_own_switch(self):
        cr.update(self.job, "design", ["TC-001", "TC-002"], state="approved")
        cr.update(self.job, "design", "TC-002", automate=False)
        entries = cr.read(self.job)["design"]
        self.assertEqual([c["id"] for c in cr.approved(CASES, entries)], ["TC-001", "TC-002"])
        self.assertEqual([c["id"] for c in cr.approved(CASES, entries, for_automation=True)], ["TC-001"])

    def test_a_field_left_out_is_left_alone(self):
        cr.update(self.job, "design", "TC-001", state="approved", title="t")
        cr.update(self.job, "design", "TC-001", note="n")
        e = cr.read(self.job)["design"]["TC-001"]
        self.assertEqual((e["state"], e["title"], e["note"]), ("approved", "t", "n"))

    def test_design_and_manual_decisions_are_kept_apart(self):
        cr.update(self.job, "design", "TC-001", state="approved")
        cr.update(self.job, "manual", "MT-001", state="rejected")
        review = cr.read(self.job)
        self.assertEqual(list(review["design"]), ["TC-001"])
        self.assertEqual(list(review["manual"]), ["MT-001"])


class AfterARedesign(Base):
    """A decision is about a case, not a number: once a re-design has
    renumbered the cases, old decisions do not count for the new ones."""

    def test_decisions_about_cases_that_are_gone_do_not_make_the_new_ones_reviewed(self):
        cr.update(self.job, "design", "TC-001", state="approved")
        entries = cr.read(self.job)["design"]
        renumbered = [dict(c, id="TC-00%d" % n) for n, c in enumerate(CASES, 4)]
        self.assertTrue(cr.reviewed(entries))
        self.assertTrue(cr.reviewed(entries, CASES))
        self.assertFalse(cr.reviewed(entries, renumbered))
        self.assertTrue(cr.stale(entries, renumbered))
        self.assertFalse(cr.stale(entries, CASES))
        s = cr.summary(renumbered, entries)
        self.assertEqual((s["reviewed"], s["stale_review"], s["approved"]), (False, True, 0))
        self.assertFalse(cr.stale({}, renumbered), "nothing decided is not stale, it is undecided")

    def test_a_decision_for_a_case_the_result_no_longer_has_is_refused(self):
        with io.open(os.path.join(self.job, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_cases": [{"id": "TC-004"}, {"id": "TC-005"}]}, fh)
        self.assertEqual(cr.current_ids(self.job, "design"), {"TC-004", "TC-005"})
        self.assertIsNone(cr.current_ids(self.job, "manual"), "no result: nothing is known")
        with self.assertRaises(ValueError) as got:
            cr.update(self.job, "design", "TC-001", state="approved", known=cr.current_ids(self.job, "design"))
        self.assertIn("TC-001 is not a case of this job's design result now", str(got.exception))
        self.assertEqual(cr.read(self.job)["design"], {})
        cr.update(self.job, "design", "TC-004", state="approved", known=cr.current_ids(self.job, "design"))
        cr.update(self.job, "manual", "MT-001", state="approved", known=cr.current_ids(self.job, "manual"))
        self.assertEqual(cr.read(self.job)["design"]["TC-004"]["state"], "approved")


class WhatIsRefused(Base):
    def test_bad_input_is_refused_and_nothing_is_written(self):
        for kw in ({"what": "other", "ids": "TC-1", "state": "approved"},
                   {"what": "design", "ids": "../x", "state": "approved"},
                   {"what": "design", "ids": "TC-1", "state": "maybe"},
                   {"what": "design", "ids": "TC-1", "automate": "yes"},
                   {"what": "design", "ids": "TC-1", "title": "   "},
                   {"what": "design", "ids": "TC-1", "steps": "not a list"},
                   {"what": "design", "ids": "TC-1", "steps": [{"action": "", "data": "", "expected": ""}]},
                   {"what": "design", "ids": [], "state": "approved"},
                   {"what": "design", "ids": [f"TC-{n}" for n in range(300)], "state": "approved"}):
            with self.assertRaises(ValueError, msg=str(kw)):
                cr.update(self.job, **kw)
        self.assertFalse(os.path.isfile(os.path.join(self.job, cr.FILE)))

    def test_text_is_bounded_and_one_line(self):
        cr.update(self.job, "design", "TC-1", title="a\nb" + "x" * 1000, note="n\x00o" + "y" * 1000,
                  steps=[{"action": "z" * 9000, "data": 5, "expected": None}])
        e = cr.read(self.job)["design"]["TC-1"]
        self.assertEqual(len(e["title"]), cr.MAX_TITLE)
        self.assertTrue(e["title"].startswith("a bx"))
        self.assertEqual(len(e["note"]), cr.MAX_NOTE)
        self.assertEqual(len(e["steps"][0]["action"]), cr.MAX_STEP)
        self.assertEqual(e["steps"][0]["data"], "5")

    def test_a_broken_file_reads_as_nothing_decided(self):
        with io.open(os.path.join(self.job, cr.FILE), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(cr.read(self.job)["design"], {})
        with io.open(os.path.join(self.job, cr.FILE), "w", encoding="utf-8") as fh:
            json.dump({"design": {"../x": {"state": "approved"}, "TC-1": "approved"}}, fh)
        self.assertEqual(cr.read(self.job)["design"], {})


if __name__ == "__main__":
    unittest.main(verbosity=1)
