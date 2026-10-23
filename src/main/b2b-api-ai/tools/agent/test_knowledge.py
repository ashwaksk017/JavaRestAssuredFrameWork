"""Answers to a design's open questions: the job's, and those kept for
later designs in the project's KNOWN file, pushed with the job.

    python tools/agent/test_knowledge.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec_ = importlib.util.spec_from_file_location("knowledge_under_test", os.path.join(HERE, "knowledge.py"))
kn = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(kn)

Q1 = "How is an account put in status L before POST /businesses/{accountId}/activate?"
Q2 = "Which attestation source does the vendor report for a fraud result?"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="knowledge-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.job = os.path.join(self.root, ".workbench", "j1")
        os.makedirs(self.job)
        with io.open(os.path.join(self.job, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"open_questions": [Q1, Q2, Q1]}, fh)

    def known_text(self):
        with io.open(os.path.join(self.root, *kn.known_rel("b2b").split("/")), encoding="utf-8") as fh:
            return fh.read()


class TheJobsAnswers(Base):
    def test_the_design_s_questions_are_listed_once_with_what_was_answered(self):
        qs = kn.questions(self.job)
        self.assertEqual([q["question"] for q in qs], [Q1, Q2])
        kn.save(self.job, [{"question": Q2, "answer": "leadspace", "keep": False}], self.root)
        self.assertEqual(kn.questions(self.job)[1]["answer"], "leadspace")
        self.assertFalse(os.path.exists(os.path.join(self.root, *kn.known_rel("b2b").split("/"))),
                         "an answer not kept writes no knowledge file")

    def test_a_kept_answer_is_one_entry_updated_in_place_and_removed_when_unkept(self):
        r = kn.save(self.job, [{"question": Q1, "answer": "UPDATE account SET status='L' WHERE web_site=?",
                                "keep": True}], self.root, "b2b", job="j1")
        self.assertEqual(r["file"], ".cursor/skills/jira-to-restassured/KNOWN_B2B.md")
        text = self.known_text()
        self.assertIn("# Known answers -- B2B", text)
        self.assertIn(f"### {Q1}", text)
        self.assertIn("Endpoints: POST /businesses/{accountId}/activate", text)
        self.assertIn("(job j1)", text)
        kn.save(self.job, [{"question": Q1, "answer": "Db.execute the status update first", "keep": True}],
                self.root, "b2b")
        text = self.known_text()
        self.assertEqual(text.count(f"### {Q1}"), 1, "updated in place, never twice")
        self.assertIn("Db.execute the status update first", text)
        self.assertNotIn("web_site=?", text)
        kn.save(self.job, [{"question": Q1, "answer": "Db.execute the status update first", "keep": False}],
                self.root, "b2b")
        self.assertNotIn(Q1, self.known_text(), "un-kept: its entry goes")

    def test_what_is_refused(self):
        with self.assertRaises(ValueError) as got:
            kn.save(self.job, [{"question": Q1, "answer": "call db.corp.example.test", "keep": True}],
                    self.root, private={"db.corp.example.test": "database.host"})
        self.assertIn("public", str(got.exception))
        for bad, why in (([{"answer": "x"}], "names its question"),
                         ([{"question": Q1, "answer": "x" * 3001}], "at most"),
                         ("not a list", "as a list")):
            with self.assertRaises(ValueError) as got:
                kn.save(self.job, bad, self.root)
            self.assertIn(why, str(got.exception))


class WhatLaterRunsGet(Base):
    def test_kept_answers_for_the_endpoints_and_the_job_s_own_are_handed_over(self):
        kn.save(self.job, [{"question": Q1, "answer": "set status L in the database", "keep": True},
                           {"question": Q2, "answer": "leadspace", "keep": False}], self.root, "b2b")
        other = os.path.join(self.root, ".workbench", "j2")
        os.makedirs(other)
        text = kn.block(self.root, other, "b2b", ["POST /businesses/{accountId}/activate"])
        self.assertIn("Answered before, kept in .cursor/skills/jira-to-restassured/KNOWN_B2B.md", text)
        self.assertIn("A: set status L in the database", text)
        self.assertNotIn("leadspace", text, "not kept: only the job that answered it has it")
        self.assertEqual(kn.block(self.root, other, "b2b", ["GET /guests/{guestId}"]), "",
                         "an entry about another endpoint is not handed over")
        mine = kn.block(self.root, self.job, "b2b", ["GET /guests/{guestId}"])
        self.assertIn("A: leadspace", mine, "the job's own answers always")


class ThePush(Base):
    def test_the_file_goes_with_the_job_only_as_the_job_left_it(self):
        self.assertEqual(kn.to_push(self.job, self.root), ([], ""))
        kn.save(self.job, [{"question": Q1, "answer": "status L", "keep": True}], self.root, "b2b")
        self.assertEqual(kn.to_push(self.job, self.root), ([kn.known_rel("b2b")], ""))
        self.assertIn("+### " + Q1, kn.diff(self.job, self.root)["diff"])
        with io.open(os.path.join(self.root, *kn.known_rel("b2b").split("/")), "a", encoding="utf-8") as fh:
            fh.write("an edit by hand\n")
        paths, why = kn.to_push(self.job, self.root)
        self.assertEqual(paths, [])
        self.assertIn("changed since this job kept its answers", why)


if __name__ == "__main__":
    unittest.main(verbosity=1)
