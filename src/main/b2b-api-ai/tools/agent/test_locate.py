"""Locate decides create/update/upstream/confirm, and stops when it should.

    python tools/agent/test_locate.py

The shape index is stubbed. Building a real one reads every input XML
and takes minutes, and what is worth testing here is not whether
shape_match works -- it has its own tests -- but whether this stage
turns its verdicts into the right ACTION. The two that matter are the
stops: writing a hand-written test for a call the converter already
emits produces a file the next convert does not know about, and nobody
reconciles it.
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

_spec = importlib.util.spec_from_file_location(
    "agent_locate_under_test", os.path.join(HERE, "locate.py"))
locate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(locate)

GENERATED = "ReadyAPI XML"
HAND = "this repository"


# RAW index entries, with NO `destination` key -- which is what
# shape_match.match actually returns. Supplying the key here is what let
# a bug through: decide() read it, real entries lacked it, and every
# generated test was classified as hand-written.
def entry(generated=False, meth="create"):
    cls = ("com.hi.api.tests.imported.goal.groups.GetGroupsTest" if generated
           else "com.hi.api.tests.jira.GroupsTest")
    return {"java_class_fqn": cls, "java_method": meth,
            "csv_path": "csv/goal/x.csv" if generated else "",
            "suite": "goal" if generated else "jira"}


class Decide(unittest.TestCase):
    """The verdict-to-action mapping, which is this stage's whole job."""

    def test_no_match_is_a_create(self):
        d, e = locate.decide({"verdict": locate.shape_match.NEW})
        self.assertEqual(d, locate.CREATE)
        self.assertEqual(e, [])

    def test_a_hand_written_match_is_an_update(self):
        d, e = locate.decide({"verdict": locate.shape_match.EXACT_ONE,
                              "exact_matches": [entry()]})
        self.assertEqual(d, locate.UPDATE)
        self.assertEqual(len(e), 1)

    def test_a_generated_match_goes_upstream(self):
        """The outcome this stage exists to prevent."""
        d, e = locate.decide({"verdict": locate.shape_match.EXACT_ONE,
                              "exact_matches": [entry(generated=True)]})
        self.assertEqual(d, locate.UPSTREAM)

    def test_generated_wins_even_when_a_hand_written_test_also_matches(self):
        """Checked BEFORE the duplicate and loose cases on purpose: a
        mixed match still means the converter owns this call."""
        d, e = locate.decide({
            "verdict": locate.shape_match.EXACT_MANY,
            "exact_matches": [entry(), entry(generated=True)]})
        self.assertEqual(d, locate.UPSTREAM)
        self.assertEqual(len(e), 1)
        self.assertTrue(locate.is_generated(e[0]))

    def test_a_duplicate_suspect_asks(self):
        d, _ = locate.decide({
            "verdict": locate.shape_match.DUPLICATE_SUSPECT,
            "duplicate_candidates": [entry()]})
        self.assertEqual(d, locate.CONFIRM)

    def test_a_loose_only_match_asks(self):
        d, _ = locate.decide({"verdict": locate.shape_match.LOOSE_ONLY,
                              "loose_matches": [entry()]})
        self.assertEqual(d, locate.CONFIRM)

    def test_a_verdict_with_no_entries_is_still_a_create(self):
        """An index can name a verdict without producing a usable row;
        proposing an update against nothing would be worse."""
        d, _ = locate.decide({"verdict": locate.shape_match.EXACT_ONE,
                              "exact_matches": []})
        self.assertEqual(d, locate.CREATE)

    def test_upstream_and_confirm_are_the_stops(self):
        self.assertIn(locate.UPSTREAM, locate.STOPS)
        self.assertIn(locate.CONFIRM, locate.STOPS)
        self.assertNotIn(locate.CREATE, locate.STOPS)
        self.assertNotIn(locate.UPDATE, locate.STOPS)


class Destination(unittest.TestCase):
    """shape_match.match returns RAW index rows, not report rows."""

    def test_a_raw_generated_entry_is_recognised_without_a_destination_key(self):
        e = entry(generated=True)
        self.assertNotIn("destination", e)
        self.assertTrue(locate.is_generated(e))

    def test_a_raw_hand_written_entry_is_not(self):
        self.assertFalse(locate.is_generated(entry()))

    def test_it_asks_shape_match_rather_than_restating_the_rule(self):
        """If the two drift, the dangerous direction is a generated test
        read as hand-written: it gets edited and the next convert
        silently overwrites the edit."""
        for e in (entry(), entry(generated=True)):
            self.assertEqual(
                locate.is_generated(e),
                "ReadyAPI XML" in locate.shape_match.destination(e))


class CandidateShape(unittest.TestCase):
    def test_intake_raw_path_becomes_shape_match_path(self):
        """The two modules name it differently; a missing path matches
        nothing and would read as 'nothing covers this call'."""
        c = locate.candidate_from(
            {"verb": "post", "raw_path": "/props/ABC/groups"}, "j")
        self.assertEqual(c["steps"][0]["path"], "/props/ABC/groups")
        self.assertEqual(c["steps"][0]["verb"], "POST")

    def test_query_names_become_valueless_params(self):
        """The signature keys on names; a value would make two tests of
        the same call look like different calls."""
        c = locate.candidate_from(
            {"verb": "GET", "raw_path": "/x",
             "query_param_names": ["offset", "limit"]}, "j")
        self.assertEqual(c["steps"][0]["query_params"],
                         {"offset": "", "limit": ""})

    def test_a_body_is_carried_through(self):
        c = locate.candidate_from(
            {"verb": "POST", "raw_path": "/x", "body": '{"a":1}'}, "j")
        self.assertEqual(c["steps"][0]["body"], '{"a":1}')


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._load_index = locate.load_index
        self._match = locate.shape_match.match
        self.addCleanup(setattr, locate, "load_index", self._load_index)
        self.addCleanup(setattr, locate.shape_match, "match", self._match)
        locate.load_index = lambda p, rebuild=False: ({}, "stubbed")

    def _intake(self, requests):
        out = locate.job_dir("j", self.tmp)
        os.makedirs(out, exist_ok=True)
        with io.open(os.path.join(out, "intake.json"), "w",
                     encoding="utf-8") as fh:
            json.dump({"job": "j", "requests": requests, "ok": True}, fh)
        return out

    def _verdict(self, result):
        locate.shape_match.match = lambda cand, index: result

    def plan_md(self):
        with io.open(os.path.join(locate.job_dir("j", self.tmp), "plan.md"),
                     encoding="utf-8") as fh:
            return fh.read()

    def test_it_writes_a_plan_and_a_manifest(self):
        self._intake([{"verb": "GET", "raw_path": "/props/ABC/groups"}])
        self._verdict({"verdict": locate.shape_match.NEW})
        plan = locate.run("j", root=self.tmp)
        self.assertTrue(plan["ok"])
        self.assertIn("CREATE", self.plan_md())
        self.assertTrue(os.path.isfile(
            os.path.join(locate.job_dir("j", self.tmp), "locate.json")))

    def test_a_generated_match_makes_the_job_stop(self):
        self._intake([{"verb": "GET", "raw_path": "/props/ABC/groups"}])
        self._verdict({"verdict": locate.shape_match.EXACT_ONE,
                       "exact_matches": [entry(generated=True)]})
        plan = locate.run("j", root=self.tmp)
        self.assertFalse(plan["ok"])
        self.assertEqual(len(plan["stops"]), 1)
        self.assertIn("stop here", self.plan_md())

    def test_the_plan_names_the_test_that_already_covers_it(self):
        self._intake([{"verb": "GET", "raw_path": "/x"}])
        self._verdict({"verdict": locate.shape_match.EXACT_ONE,
                       "exact_matches": [entry()]})
        locate.run("j", root=self.tmp)
        self.assertIn("GroupsTest", self.plan_md())

    def test_no_intake_refuses_rather_than_inventing_one(self):
        os.makedirs(locate.job_dir("j", self.tmp), exist_ok=True)
        with self.assertRaises(SystemExit) as got:
            locate.run("j", root=self.tmp)
        self.assertIn("intake.py", str(got.exception))

    def test_an_intake_with_no_requests_refuses(self):
        self._intake([])
        with self.assertRaises(SystemExit) as got:
            locate.run("j", root=self.tmp)
        self.assertIn("nothing to locate", str(got.exception).lower())

    def test_a_bad_job_id_is_refused_by_intakes_rule(self):
        """One validation, not two that can disagree."""
        with self.assertRaises(ValueError):
            locate.job_dir("../escape", self.tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
