"""Which suites an automated case belongs to.

    python tools/agent/test_suitemap.py
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


sm = _load("suitemap_under_test", os.path.join(HERE, "suitemap.py"))
cr = _load("casereview_for_suitemap", os.path.join(HERE, "casereview.py"))


class TheRules(unittest.TestCase):
    def setUp(self):
        self.mapping = sm.load()

    def test_the_committed_map_is_read_without_a_problem(self):
        self.assertNotIn("problem", self.mapping)
        self.assertEqual(self.mapping["always"][:2], ["jira", "regression"])

    def test_a_case_gets_its_groups_by_type_and_priority(self):
        g = lambda **c: sm.groups_for(c, self.mapping)
        self.assertEqual(g(type="positive", priority="high"), ["jira", "regression", "smoke"])
        self.assertEqual(g(type="positive", priority="medium"), ["jira", "regression"])
        self.assertEqual(g(type="boundary", priority="low"), ["jira", "regression", "negative"])
        self.assertEqual(g(type="security", priority="high"), ["jira", "regression", "security"])

    def test_a_persons_choice_wins_and_jira_is_never_dropped(self):
        self.assertEqual(sm.groups_for({"type": "positive", "priority": "high"}, self.mapping, ["nightly"]),
                         ["jira", "nightly"])
        self.assertEqual(sm.clean_groups("Smoke, smoke ,jira"), ["jira", "smoke"])
        for bad in (["has space"], ["<x>"], ["a" * 40], [str(n) + "g" for n in range(9)]):
            with self.assertRaises(ValueError):
                sm.clean_groups(bad)

    def test_a_broken_map_is_not_half_used(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "m.json")
        with io.open(p, "w", encoding="utf-8") as fh:
            json.dump({"always": ["jira"], "rules": [{"group": "Bad Name", "type": ["positive"]}]}, fh)
        m = sm.load(p)
        self.assertIn("not a group name", m["problem"])
        self.assertEqual(m["always"], ["jira", "regression"])
        self.assertEqual(sm.load(os.path.join(d, "none.json"))["always"], ["jira", "regression"])


class TheSuites(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="suitemap-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_a_group_suite_runs_the_package_by_group_and_is_written_once(self):
        written = sm.write_suites(self.root, {"jira", "smoke", "regression"})
        self.assertEqual(written, [sm.suite_rel("regression"), sm.suite_rel("smoke")], "jira has testng-jira.xml")
        path = os.path.join(self.root, *sm.suite_rel("smoke").split("/"))
        with io.open(path, encoding="utf-8") as fh:
            xml = fh.read()
        self.assertIn('<include name="smoke"/>', xml)
        self.assertIn('<package name="com.hi.api.tests.jira"/>', xml)
        self.assertNotIn("--", xml.split("<!--", 1)[1].split("-->", 1)[0], "a valid XML comment")
        self.assertEqual(sm.write_suites(self.root, {"smoke"}), [], "an existing suite is left as it is")

    def test_the_groups_named_in_code_are_found(self):
        java = ('@Test(groups = {"jira", "regression", "smoke"}, dataProvider = "rows")\n'
                '@Test(groups = { "jira","negative" })\n')
        self.assertEqual(sm.groups_in_code([java]), {"jira", "regression", "smoke", "negative"})


class AReviewSetsThem(unittest.TestCase):
    def setUp(self):
        self.job = tempfile.mkdtemp(prefix="suitemap-job-")
        self.addCleanup(shutil.rmtree, self.job, True)
        with io.open(os.path.join(self.job, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_cases": [{"id": "TC-001", "type": "positive", "priority": "high"},
                                      {"id": "TC-002", "type": "negative", "priority": "low"}]}, fh)

    def test_set_by_hand_then_back_to_the_rules(self):
        self.assertEqual(sm.for_job(self.job)["TC-001"], {"groups": ["jira", "regression", "smoke"], "chosen": False})
        cr.update(self.job, "design", ["TC-001"], groups="nightly, smoke")
        self.assertEqual(sm.for_job(self.job)["TC-001"], {"groups": ["jira", "nightly", "smoke"], "chosen": True})
        cr.update(self.job, "design", ["TC-001"], groups="")
        self.assertFalse(sm.for_job(self.job)["TC-001"]["chosen"])
        with self.assertRaises(ValueError):
            cr.update(self.job, "design", ["TC-002"], groups="<script>")


if __name__ == "__main__":
    unittest.main(verbosity=1)
