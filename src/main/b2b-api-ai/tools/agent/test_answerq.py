"""Cursor answers a design's open questions from the project, and drafts the
entry to keep -- suggestions only; a person keeps them.

    python tools/agent/test_answerq.py
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
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
spec_ = importlib.util.spec_from_file_location("answerq_under_test", os.path.join(HERE, "answerq.py"))
aq = importlib.util.module_from_spec(spec_)
sys.modules["answerq_under_test"] = aq
spec_.loader.exec_module(aq)
loop = aq._loop()

Q1 = "How is an account put in status L before POST /businesses/{accountId}/activate?"
Q2 = "Which source does the vendor report?"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="answerq-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(setattr, loop.intake, "ROOT", loop.intake.ROOT)
        loop.intake.ROOT = self.root
        self.job = loop.intake.job_dir("j1", self.root)
        os.makedirs(self.job)
        with io.open(os.path.join(self.job, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"open_questions": [Q1, Q2]}, fh)
        cfg = os.path.join(self.root, "src", "main", "resources")
        os.makedirs(cfg)
        with io.open(os.path.join(cfg, "program_configuration.json"), "w", encoding="utf-8") as fh:
            json.dump({"stg": {"database": {"host": "db-secret-host.corp.test"}}}, fh)

    def run_it(self, reply):
        seen = []

        def agent(prompt):
            seen.append(prompt)
            return reply
        out = io.StringIO()
        with redirect_stdout(out):
            rc = aq.answer("j1", self.root, agent=agent)
        return rc, out.getvalue(), seen

    def suggestions(self):
        with io.open(os.path.join(self.job, aq.SUGGESTIONS), encoding="utf-8") as fh:
            return json.load(fh)["answers"]


class Answering(Base):
    def test_each_question_gets_an_answer_where_it_was_found_and_an_entry_to_keep(self):
        reply = json.dumps({"answers": [
            {"question": Q1, "answer": "Db.execute an UPDATE setting status L, then activate",
             "evidence": ["src/main/java/com/hi/api/support/x/Hooks1.java:42 -- UPDATE account SET status"],
             "confidence": "high",
             "entry": "To activate an account the converted suites first set its status to L in the "
                      "database (db-secret-host.corp.test), then POST activate."},
            {"question": Q2, "answer": "leadspace", "evidence": [], "confidence": "medium", "entry": "x"}]})
        rc, said, seen = self.run_it("Here it is:\n```json\n" + reply + "\n```")
        self.assertEqual(rc, 0, said)
        p = seen[0]
        self.assertIn("1. " + Q1, p)
        self.assertIn("_audit/<suite>/facade_endpoints.md", p)
        self.assertIn("Never open src/main/resources/program_configuration*.json", p)
        self.assertIn("KNOWN_B2B.md", p)
        s = {x["question"]: x for x in self.suggestions()}
        self.assertEqual(s[Q1]["confidence"], "high")
        self.assertTrue(s[Q1]["suggested"])
        self.assertIn("Hooks1.java:42", s[Q1]["evidence"][0])
        self.assertNotIn("db-secret-host.corp.test", json.dumps(s), "a private value never comes back")
        self.assertIn("2 of 2 question(s) answered (1 with the files they were found in)", said)

    def test_a_question_a_person_answered_keeps_that_answer_and_gets_its_entry_drafted(self):
        loop._load("t_knowledge", os.path.join(HERE, "knowledge.py")).save(
            self.job, [{"question": Q2, "answer": "the person's answer", "keep": False}], self.root)
        rc, _said, seen = self.run_it(json.dumps({"answers": [
            {"question": Q1, "answer": "a", "entry": "e1"},
            {"question": Q2, "answer": "AI Agent's other answer", "entry": "a rule"}]}))
        self.assertEqual(rc, 0)
        self.assertIn("ANSWERED by a person: the person's answer", seen[0])
        s = {x["question"]: x for x in self.suggestions()}
        self.assertEqual(s[Q2]["answer"], "the person's answer")
        self.assertFalse(s[Q2]["suggested"])
        self.assertEqual(s[Q2]["entry"], "a rule")

    def test_nothing_usable_back_or_nothing_asked_is_said(self):
        rc, said, _ = self.run_it("I could not find it.")
        self.assertEqual(rc, 1)
        self.assertIn("no JSON object", said)
        with io.open(os.path.join(self.job, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"open_questions": []}, fh)
        rc, said, _ = self.run_it("{}")
        self.assertEqual(rc, 1)
        self.assertIn("no open question", said)


if __name__ == "__main__":
    unittest.main(verbosity=1)
