"""Cursor's suite suggestion is checked, kept apart, and decides nothing.

    python tools/agent/test_suitesuggest.py
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


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ss = _load("suitesuggest_under_test", os.path.join(HERE, "suitesuggest.py"))
mock = _load("mock_for_suitesuggest", os.path.join(HERE, "mock.py"))


def case(cid, ctype="positive", prio="high", endpoint="GET /a"):
    return {"id": cid, "title": f"case {cid}", "endpoint": endpoint, "type": ctype, "priority": prio,
            "steps": [{"action": "send", "data": "", "expected": "status 200"}]}


class Suggesting(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="b2b-ss-")
        self.job = "job-ss"
        self.out = os.path.join(self.root, ".workbench", self.job)
        os.makedirs(self.out)
        os.makedirs(os.path.join(self.root, "Suites", "jira"))
        with io.open(os.path.join(self.root, "Suites", "jira", "group-nightly.xml"), "w") as fh:
            fh.write("<suite/>")
        with io.open(os.path.join(self.out, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_cases": [case("TC-001"), case("TC-002", "negative", "medium"),
                                      case("TC-003", "positive", "high", "GET /a"),
                                      case("TC-004", "security", "low")]}, fh)
        with io.open(os.path.join(self.out, "case-review.json"), "w", encoding="utf-8") as fh:
            json.dump({"design": {"TC-004": {"state": "rejected"}}}, fh)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_it(self, agent, ids=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ss.run(self.job, ids, root=self.root, agent=agent)
        return rc, buf.getvalue()

    def result(self):
        with io.open(os.path.join(self.out, "suite-suggest.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_mock_rule_makes_one_smoke_case_per_endpoint(self):
        sent = {}

        def agent(prompt, followup):
            sent["prompt"] = prompt
            return mock.cursor(prompt, followup)
        rc, said = self.run_it(agent)
        self.assertEqual(rc, 0, said)
        r = self.result()
        by = {s["id"]: s for s in r["suggestions"]}
        self.assertEqual(set(by), {"TC-001", "TC-002", "TC-003"})   # the rejected case is not asked about
        self.assertEqual(by["TC-001"]["groups"], ["jira", "smoke", "regression"])
        self.assertEqual(by["TC-003"]["groups"], ["jira", "regression"])
        self.assertIn("nightly", r["groups"])                        # a suite file is a suite
        self.assertIn("===== SUITE ASSIGNMENT =====", sent["prompt"])
        self.assertFalse(by["TC-002"]["same"])                       # the rules say negative too

    def test_bad_names_unknown_ids_and_repeats_are_dropped(self):
        def agent(prompt, followup):
            return {"result": json.dumps({"suggestions": [
                {"id": "TC-001", "groups": ["Smoke", "bad name!", "regression"], "confidence": "high", "why": "x"},
                {"id": "TC-001", "groups": ["other"]},
                {"id": "TC-999", "groups": ["smoke"]},
                {"id": "TC-002", "groups": "regression", "confidence": "sure"}]}), "model": "m"}
        rc, said = self.run_it(agent)
        self.assertEqual(rc, 0, said)
        r = self.result()
        by = {s["id"]: s for s in r["suggestions"]}
        self.assertEqual(by["TC-001"]["groups"], ["jira", "smoke", "regression"])
        self.assertEqual(by["TC-002"]["groups"], ["jira", "regression"])
        self.assertEqual(by["TC-002"]["confidence"], "medium")
        self.assertEqual(r["missing"], ["TC-003"])

    def test_nothing_is_written_to_the_review(self):
        self.run_it(lambda p, f: mock.cursor(p, f))
        with io.open(os.path.join(self.out, "case-review.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"design": {"TC-004": {"state": "rejected"}}})

    def test_only_the_chosen_cases_are_asked_about(self):
        sent = {}

        def agent(prompt, followup):
            sent["prompt"] = prompt
            return mock.cursor(prompt, followup)
        self.run_it(agent, ["TC-002"])
        cases = sent["prompt"].split("===== CASES =====")[1]
        self.assertIn("TC-002", cases)
        self.assertNotIn("TC-001", cases)

    def test_an_unusable_answer_leaves_no_result(self):
        rc, said = self.run_it(lambda p, f: {"result": "I cannot", "model": "m"})
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(os.path.join(self.out, "suite-suggest.json")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
