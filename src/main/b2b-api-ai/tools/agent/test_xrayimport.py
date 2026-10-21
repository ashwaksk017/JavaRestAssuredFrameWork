"""A story's existing Xray tests become the cases to automate.

    python tools/agent/test_xrayimport.py
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


xi = _load("xrayimport_under_test", os.path.join(HERE, "xrayimport.py"))

CONN = {"base": "https://jira.example.test", "token": "tok", "api": "/rest/api/2", "timeout": 5}
LINK = {"name": "Tests", "inward": "is tested by", "outward": "tests"}


def link(key, summary, kind="Test", ltype=LINK, side="inwardIssue"):
    return {"type": ltype, side: {"key": key, "fields": {"summary": summary, "issuetype": {"name": kind},
                                                          "status": {"name": "Open"}}}}


class Jira:
    def __init__(self, links, steps):
        self.links, self.steps, self.calls = links, steps, []

    def __call__(self, method, url, token, timeout, body=None):
        self.calls.append((method, url))
        if "/rest/raven/2.0/api/test/" in url:
            key = url.split("/test/")[1].split("/")[0]
            if key not in self.steps:
                raise xi.search.JiraError(404, "no such test")
            return [{"index": n, "step": {"raw": a}, "data": {"raw": d}, "result": {"raw": e}}
                    for n, (a, d, e) in enumerate(self.steps[key], 1)]
        if "/issue/ABC-1?fields=summary,issuelinks" in url:
            return {"key": "ABC-1", "fields": {"summary": "the story", "issuelinks": self.links}}
        raise AssertionError(f"unexpected {method} {url}")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xrayimport-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(setattr, xi.loop.intake, "ROOT", xi.loop.intake.ROOT)
        xi.loop.intake.ROOT = self.root
        self.job = xi.loop.intake.job_dir("j1", self.root)
        os.makedirs(self.job)
        with io.open(os.path.join(self.job, "intake.json"), "w", encoding="utf-8") as fh:
            json.dump({"story": {"key": "ABC-1"}}, fh)
        with io.open(os.path.join(self.job, "brief.md"), "w", encoding="utf-8") as fh:
            fh.write("# brief\nthe story\n")
        self.jira = Jira(
            [link("ABC-11", "GET spendDetail returns 200 for the coordinator"),
             link("ABC-12", "A test with no steps"),
             link("ABC-13", "A bug, not a test", kind="Bug"),
             link("ABC-14", "Blocked by", ltype={"name": "Blocks", "inward": "is blocked by", "outward": "blocks"}),
             link("ABC-15", "Linked the other way round", side="outwardIssue"),
             link("ABC-16", "POST x creates one")],
            {"ABC-11": [("GET /members/{memberId}/spendDetail as the coordinator", "a coordinator token",
                         "200 and a JSON array")],
             "ABC-12": [],
             "ABC-15": [("POST /x", "", "201")],
             "ABC-16": [("POST /x", "", "201")]})

    def run_it(self, **kw):
        return xi.run("j1", self.root, conn=dict(CONN), transport=self.jira, cfg={}, **kw)

    def read(self, name):
        with io.open(os.path.join(self.job, name), encoding="utf-8") as fh:
            return fh.read()


class Importing(Base):
    def test_the_linked_tests_with_steps_become_the_design_with_their_keys(self):
        r = self.run_it()
        self.assertEqual((r["ok"], r["story"], r["count"]), (True, "ABC-1", 2))
        self.assertEqual(r["keys"], ["ABC-11", "ABC-16"], "the bug, the Blocks link and the reversed link are left out")
        self.assertEqual([n["key"] for n in r["not_used"]], ["ABC-15", "ABC-12"])
        self.assertIn("linked the other way round", r["not_used"][0]["why"], "named, not lost")
        self.assertEqual(r["not_used"][1], {"key": "ABC-12", "why": "it has no steps in Xray"})
        d = json.loads(self.read("design.json"))
        self.assertEqual((d["source"], d["service"], d["mock"]), ("xray", "ABC-1", False))
        self.assertEqual(d["brief"], xi.loop.brief_fingerprint(self.job), "made from the brief the job has")
        c = d["test_cases"][0]
        self.assertEqual((c["id"], c["xray_key"], c["title"]),
                         ("TC-001", "ABC-11", "GET spendDetail returns 200 for the coordinator"))
        self.assertEqual(c["steps"], [{"action": "GET /members/{memberId}/spendDetail as the coordinator",
                                       "data": "a coordinator token", "expected": "200 and a JSON array"}])
        self.assertIn("## TC-001 [Xray ABC-11]", self.read("design.md"))
        self.assertIn("- ABC-12: it has no steps in Xray", self.read("design.md"))

    def test_the_keys_reach_the_agentic_step_and_create_in_xray_creates_nothing(self):
        self.run_it()
        keys = xi.xt.keys_for(self.job, "design", False)
        self.assertEqual(keys, {"TC-001": "ABC-11", "TC-002": "ABC-16"})
        xi.loop.casereview.update(self.job, "design", ["TC-001", "TC-002"], state="approved")
        text, note = xi.loop.designed_cases(self.job, keys=keys)
        self.assertIn("TC-001", text)
        self.assertIn("[Xray ABC-11]", text)
        entry = json.loads(self.read("xray-tests.json"))["tests"]["design:TC-001"]
        shaped = xi.xt.cases_of(json.loads(self.read("design.json")), "design")[0]
        self.assertEqual(entry["fingerprint"], xi.xt.fingerprint(shaped), "Create in Xray sees it as already there")
        self.assertTrue(entry["imported"])


class TheKeysReachTheDataSheet(Base):
    def test_an_imported_tests_key_is_filled_in_the_rows_of_its_case(self):
        """An existing Xray test with no automation: once its case is
        automated, the data sheet's row carries the test's key -- the run
        reports to the test that was already there."""
        self.run_it()
        rel = "src/test/resources/csv/manual/SpendTest/spend.csv"
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path))
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("description,test_case_id,execute\nhappy,TC-001,Y\ncreate,TC-002,Y\n")
        touched = xi.loop.ensure_manual_csv_xray(self.root, self.job, [rel], mocked=False)
        self.assertEqual(touched, [rel])
        with io.open(path, encoding="utf-8") as fh:
            rows = list(__import__("csv").DictReader(fh))
        self.assertEqual([(r["test_case_id"], r["jira_xray_id"], r["jira_issue"]) for r in rows],
                         [("TC-001", "ABC-11", "ABC-1"), ("TC-002", "ABC-16", "ABC-1")])


class TheLinksTaken(Base):
    def test_over_the_cap_the_rest_are_named(self):
        self.jira.links = [link(f"ABC-{100 + n}", f"test {n}") for n in range(xi.MAX_TESTS + 2)]
        for n in range(xi.MAX_TESTS + 2):
            self.jira.steps[f"ABC-{100 + n}"] = [("GET /y", "", "200")]
        r = self.run_it()
        self.assertEqual(r["count"], xi.MAX_TESTS)
        over = [n for n in r["not_used"] if "not read" in n["why"]]
        self.assertEqual([n["key"] for n in over], [f"ABC-{100 + xi.MAX_TESTS}", f"ABC-{101 + xi.MAX_TESTS}"])
        self.assertIn(f"ABC-{100 + xi.MAX_TESTS}: over the first", self.read("design.md"))

    def test_only_reversed_links_is_refused_with_the_reason(self):
        self.jira.links = [link("ABC-15", "the other way", side="outwardIssue")]
        with self.assertRaises(xi.Refused) as got:
            self.run_it()
        self.assertIn("linked the other way round: ABC-15", str(got.exception))


class NumberingAfterAClear(Base):
    def test_an_old_decision_never_lands_on_a_different_test(self):
        # TC-001 of a cleared design was approved and edited; TC-002 was
        # created in Xray as ABC-15.
        xi.loop.casereview.update(self.job, "design", ["TC-001", "TC-002"], state="approved")
        kept = xi.xt.read_keys(self.job)
        kept["tests"]["design:TC-002"] = {"key": "ABC-16", "title": "x", "fingerprint": "f"}
        xi.xt.write_keys(self.job, kept)
        self.run_it()
        ids = {c["xray_key"]: c["id"] for c in json.loads(self.read("design.json"))["test_cases"]}
        self.assertEqual(ids["ABC-16"], "TC-002", "the same Xray test keeps its case, and its decision")
        self.assertNotIn(ids["ABC-11"], ("TC-001", "TC-002"), "a new test takes an id nobody decided on")
        self.assertEqual(xi.xt.read_keys(self.job)["tests"]["design:TC-002"]["key"], "ABC-16")


class EndpointsAndTheSpecification(Base):
    def test_the_endpoint_is_read_from_the_steps_and_checked_against_the_specification(self):
        with io.open(os.path.join(self.job, "design-swagger.txt"), "w", encoding="utf-8") as fh:
            json.dump({"swagger": "2.0", "info": {"title": "t"}, "paths": {
                "/members/{memberId}/spendDetail": {"get": {"responses": {"200": {"description": "ok"}}}}}}, fh)
        self.jira.steps["ABC-16"] = [("POST: /x/y as a member", "", "201")]
        r = self.run_it()
        d = json.loads(self.read("design.json"))
        by_key = {c["xray_key"]: c for c in d["test_cases"]}
        self.assertEqual(by_key["ABC-11"]["endpoint"], "GET /members/{memberId}/spendDetail")
        self.assertTrue(by_key["ABC-11"]["endpoint_in_spec"])
        self.assertEqual(by_key["ABC-16"]["endpoint"], "POST /x/y")
        self.assertNotIn("endpoint_in_spec", by_key["ABC-16"], "never False: an Xray test is still automated")
        self.assertEqual(d["warnings"], ["1 test(s) name an endpoint the specification does not have: ABC-16: POST /x/y"])
        self.assertIn("## Warnings", self.read("design.md"))
        self.assertEqual(r["count"], 2)

    def test_steps_that_name_no_endpoint_leave_it_empty(self):
        self.assertEqual(xi.endpoint_in_steps([{"action": "Log in as a member", "data": "", "expected": "ok"}]), "")
        self.assertEqual(xi.endpoint_in_steps([{"action": "send it", "data": "GET /a/b, then", "expected": ""}]),
                         "GET /a/b")


class WhatIsRefused(Base):
    def test_a_design_already_there_no_story_no_links_no_steps(self):
        with io.open(os.path.join(self.job, "design.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with self.assertRaises(xi.Refused) as got:
            self.run_it()
        self.assertIn("Clear the design first", str(got.exception))
        os.remove(os.path.join(self.job, "design.json"))
        os.remove(os.path.join(self.job, "intake.json"))
        with self.assertRaises(xi.Refused) as got:
            self.run_it()
        self.assertIn("Read it", str(got.exception))
        self.jira.links = []
        with self.assertRaises(xi.Refused) as got:
            self.run_it(story="ABC-1")
        self.assertIn("test_link_type", str(got.exception))
        self.jira.links = [link("ABC-12", "no steps")]
        with self.assertRaises(xi.Refused) as got:
            self.run_it(story="ABC-1")
        self.assertIn("none of the 1 test(s)", str(got.exception))
        self.assertFalse(os.path.exists(os.path.join(self.job, "design.json")), "nothing written when refused")

    def test_another_link_type_name_is_used_when_configured(self):
        self.jira.links = [link("ABC-21", "verified", ltype={"name": "Verifies", "inward": "is verified by",
                                                             "outward": "verifies"})]
        self.jira.steps["ABC-21"] = [("GET /y", "", "200")]
        with self.assertRaises(xi.Refused):
            self.run_it()
        r = xi.run("j1", self.root, conn=dict(CONN), transport=self.jira,
                   cfg={"publish": {"test_link_type": "Verifies"}})
        self.assertEqual(r["keys"], ["ABC-21"])


class TheStandIn(unittest.TestCase):
    def test_the_mock_jira_has_a_story_with_linked_tests_and_steps(self):
        root = tempfile.mkdtemp(prefix="xrayimport-mock-")
        self.addCleanup(shutil.rmtree, root, True)
        saved = xi.loop.intake.ROOT
        xi.loop.intake.ROOT = root
        self.addCleanup(setattr, xi.loop.intake, "ROOT", saved)
        job = xi.loop.intake.job_dir("m1", root)
        os.makedirs(job)
        r = xi.run("m1", root, story="ABC-1", conn={"mock": True, "base": "https://jira.example.test",
                                                     "token": "", "api": "/rest/api/2", "timeout": 5})
        self.assertTrue(r["ok"] and r["mock_jira"])
        self.assertGreaterEqual(r["count"], 1)
        self.assertTrue(json.loads(io.open(os.path.join(job, "design.json"), encoding="utf-8").read())["mock"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
