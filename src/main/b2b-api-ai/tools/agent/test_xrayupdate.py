"""Update in Xray: three versions of each test, and nothing sent over a
change made in Xray.    python tools/agent/test_xrayupdate.py
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


xu = _load("xrayupdate_under_test", os.path.join(HERE, "xrayupdate.py"))

CONN = {"base": "https://jira.example.test", "token": "tok", "api": "/rest/api/2", "timeout": 5}
CFG = {"publish": {"create_tests": True}}
STEP = {"action": "GET /a", "data": "a token", "expected": "200"}


class Xray:
    """A Jira + Xray Server holding tests: summary, updated, steps with ids."""

    def __init__(self, tests):
        self.tests = tests          # key -> {"summary", "updated", "steps": [step dicts], "by"}
        self.calls, self.next_id = [], 100

    def __call__(self, method, url, token, timeout, body=None):
        self.calls.append((method, url, body))
        path = url.split("jira.example.test", 1)[1]
        if path.startswith("/rest/api/2/issue/"):
            key = path.split("/issue/")[1].split("?")[0]
            t = self.tests[key]
            if method == "GET":
                return {"key": key, "fields": {"summary": t["summary"], "updated": t["updated"],
                                               "description": t.get("description", "")},
                        "changelog": {"histories": [{"created": t["updated"],
                                                     "author": {"displayName": t.get("by", "Someone")}}]}}
            if method == "PUT":
                t["summary"] = body["fields"].get("summary", t["summary"])
                t["description"] = body["fields"].get("description", t.get("description", ""))
                t["updated"] = "2026-10-16T12:00:00.000+0000"
                return None
        if path.startswith("/rest/raven/1.0/api/test/"):
            rest = path.split("/api/test/")[1]
            key, _, tail = rest.partition("/step")
            t = self.tests[key]
            if method == "GET":
                return [{"id": s["id"], "index": n, "step": {"raw": s["action"]}, "data": {"raw": s["data"]},
                         "result": {"raw": s["expected"]}} for n, s in enumerate(t["steps"], 1)]
            if method == "POST":
                sid = int(tail.strip("/"))
                for s in t["steps"]:
                    if s["id"] == sid:
                        s.update(action=body["step"], data=body["data"], expected=body["result"])
                return {}
            if method == "PUT":
                self.next_id += 1
                t["steps"].append({"id": self.next_id, "action": body["step"], "data": body["data"],
                                   "expected": body["result"]})
                return {"id": self.next_id}
            if method == "DELETE":
                sid = int(tail.strip("/"))
                t["steps"] = [s for s in t["steps"] if s["id"] != sid]
                return None
        raise AssertionError(f"unexpected {method} {url}")


def xstep(n, **over):
    return dict(STEP, id=n, **over)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xrayupdate-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(setattr, xu.loop.intake, "ROOT", xu.loop.intake.ROOT)
        xu.loop.intake.ROOT = self.root
        self.job = xu.loop.intake.job_dir("j1", self.root)
        os.makedirs(self.job)
        base = {"title": "Owner lists stays", "preconditions": "", "steps": [dict(STEP)]}
        # Four cases, one per outcome. The design is the case as it is NOW.
        self.cases = {
            "TC-001": ("ABC-1", "Owner lists stays", [dict(STEP)]),                                 # same
            "TC-002": ("ABC-2", "Owner lists stays", [dict(STEP, expected="200 and a list")]),      # ours changed
            "TC-003": ("ABC-3", "Owner lists stays", [dict(STEP)]),                                 # xray changed
            "TC-004": ("ABC-4", "Owner lists stays", [dict(STEP, data="an admin token")]),          # both
        }
        self.write("design.json", {"kind": "design", "test_cases": [
            {"id": cid, "title": title, "endpoint": "GET /a", "type": "positive", "priority": "high",
             "preconditions": "", "steps": steps} for cid, (_k, title, steps) in self.cases.items()]})
        self.write("xray-tests.json", {"kind": "xray-tests", "tests": {
            f"design:{cid}": {"key": key, "title": base["title"], "base": base, "story": "ABC-100",
                              "synced_updated": "2026-10-16T09:00:00.000+0000"}
            for cid, (key, _t, _s) in self.cases.items()}})
        old = "2026-10-16T09:00:00.000+0000"
        new = "2026-10-16T10:42:00.000+0000"
        self.xray = Xray({
            "ABC-1": {"summary": "Owner lists stays", "updated": old, "steps": [xstep(1)]},
            "ABC-2": {"summary": "Owner lists stays", "updated": old, "steps": [xstep(2)]},
            "ABC-3": {"summary": "Owner lists stays", "updated": new, "by": "Pat Q",
                      "steps": [xstep(3, expected="200, sorted by date")]},
            "ABC-4": {"summary": "Owner lists stays", "updated": new,
                      "steps": [xstep(4, data="a coordinator token")]}})

    def write(self, name, data):
        with io.open(os.path.join(self.job, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def plan(self):
        return xu.plan("j1", self.root, conn=dict(CONN), transport=self.xray, xray_cfg={})

    def apply(self, choices):
        return xu.apply("j1", choices, self.root, conn=dict(CONN), transport=self.xray, cfg=CFG, xray_cfg={})


class ThePreview(Base):
    def test_each_test_is_put_in_its_place_and_nothing_is_sent(self):
        r = self.plan()
        status = {row["id"]: row["status"] for row in r["tests"]}
        self.assertEqual(status, {"TC-001": "same", "TC-002": "update", "TC-003": "xray_newer",
                                  "TC-004": "conflict"})
        self.assertFalse([c for c in self.xray.calls if c[0] != "GET"], "a preview only reads")
        row3 = [row for row in r["tests"] if row["id"] == "TC-003"][0]
        self.assertTrue(row3["changed_after_sync"], "Jira's updated is after our last sync")
        self.assertEqual(row3["last_by"], "Pat Q")
        self.assertEqual(row3["default"], "skip", "Xray newer: not overwritten unless chosen")
        self.assertEqual([row for row in r["tests"] if row["id"] == "TC-002"][0]["default"], "send")


class Applying(Base):
    def test_send_updates_the_step_in_place_and_keeps_a_backup(self):
        self.plan()
        r = self.apply({"TC-002": "send"})
        self.assertEqual([s["id"] for s in r["sent"]], ["TC-002"])
        self.assertEqual(self.xray.tests["ABC-2"]["steps"][0]["expected"], "200 and a list")
        self.assertEqual(self.xray.tests["ABC-2"]["steps"][0]["id"], 2, "edited in place, not replaced")
        with io.open(os.path.join(self.job, xu.BACKUP), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["tests"][0]["before"]["steps"][0]["expected"], "200")
        # The new base: a second preview finds it in step.
        status = {row["id"]: row["status"] for row in self.plan()["tests"]}
        self.assertEqual(status["TC-002"], "same")

    def test_take_puts_xrays_steps_into_the_case_and_sends_nothing(self):
        self.plan()
        r = self.apply({"TC-003": "take"})
        self.assertEqual([s["id"] for s in r["taken"]], ["TC-003"])
        self.assertFalse([c for c in self.xray.calls if c[0] != "GET"])
        entries = xu.casereview.read(self.job)["design"]
        self.assertEqual(entries["TC-003"]["steps"][0]["expected"], "200, sorted by date")
        self.assertEqual({row["id"]: row["status"] for row in self.plan()["tests"]}["TC-003"], "same")

    def test_a_test_changed_in_xray_after_the_preview_is_not_overwritten(self):
        self.plan()
        self.xray.tests["ABC-4"]["steps"][0]["data"] = "changed again, by someone"
        r = self.apply({"TC-004": "send"})
        self.assertEqual(r["sent"], [])
        self.assertIn("changed in Xray since the preview", r["skipped"][0]["why"])
        self.assertEqual(self.xray.tests["ABC-4"]["steps"][0]["data"], "changed again, by someone")

    def test_extra_steps_are_added_and_surplus_ones_removed(self):
        self.cases["TC-002"] = ("ABC-2", "Owner lists stays", [dict(STEP), dict(STEP, action="GET /b")])
        self.write("design.json", {"kind": "design", "test_cases": [
            {"id": cid, "title": t, "endpoint": "GET /a", "type": "positive", "priority": "high",
             "preconditions": "", "steps": s} for cid, (_k, t, s) in self.cases.items()]})
        self.plan()
        self.apply({"TC-002": "send"})
        self.assertEqual([s["action"] for s in self.xray.tests["ABC-2"]["steps"]], ["GET /a", "GET /b"])
        self.xray.tests["ABC-1"]["steps"].append(xstep(9, action="GET /extra"))
        self.xray.tests["ABC-1"]["updated"] = "2026-10-16T11:00:00.000+0000"
        row = {r["id"]: r for r in self.plan()["tests"]}["TC-001"]
        self.assertEqual(row["status"], "xray_newer", "a step added in Xray is Xray's change")

    def test_sending_needs_the_switch_and_a_preview(self):
        with self.assertRaises(xu.Refused):
            self.apply({"TC-002": "send"})                    # no preview yet
        self.plan()
        with self.assertRaises(xu.Refused):                   # switched off on purpose
            xu.apply("j1", {"TC-002": "send"}, self.root, conn=dict(CONN), transport=self.xray,
                     cfg={"publish": {"create_tests": False}}, xray_cfg={})
        r = xu.apply("j1", {"TC-003": "take"}, self.root, conn=dict(CONN), transport=self.xray,
                     cfg={"publish": {}}, xray_cfg={})
        self.assertEqual(len(r["taken"]), 1, "taking changes the case, not Jira: no switch needed")

    def test_choices_are_read_strictly(self):
        self.assertEqual(xu.parse_choices("TC-001=send, TC-002=skip"), {"TC-001": "send", "TC-002": "skip"})
        for bad in ("TC-001=delete", "TC-001", "../x=send"):
            with self.assertRaises(xu.Refused):
                xu.parse_choices(bad)


class Timestamps(unittest.TestCase):
    def test_jiras_times_are_compared_as_times(self):
        self.assertTrue(xu.after("2026-10-16T10:42:00.000+0000", "2026-10-16T09:00:00.000+0000"))
        self.assertFalse(xu.after("2026-10-16T09:00:00.000+0000", "2026-10-16T09:00:00.000+0000"))
        self.assertTrue(xu.after("2026-10-16T05:00:00.000-0500", "2026-10-16T09:00:00.000+0000"),
                        "10:00 UTC is after 09:00 UTC, whatever the zone")
        self.assertFalse(xu.after("", "2026-10-16T09:00:00.000+0000"))


class TheDescription(Base):
    """Once a case is automated, its Description -- the Audit comment of its
    test -- is sent to its Xray test on its own; the steps stay as they are."""

    def test_an_automated_case_s_description_is_sent_alone_and_then_is_in_step(self):
        rel = "src/test/java/com/hi/api/tests/jira/ListTest.java"
        p = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(p))
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write("class ListTest {\n  /**\n   * Audit: TC-001\n   * Step 1: listStays() (ListTest) -- lists\n"
                     "   */\n  void listStays() { }\n}\n")
        self.write("review.json", {"state": "pending-review", "files": {"created": [rel], "modified": []}})
        rows = {r["id"]: r for r in self.plan()["tests"]}
        self.assertEqual(rows["TC-001"]["status"], "describe")
        self.assertEqual(rows["TC-001"]["default"], "send")
        self.assertIn("Step 1: listStays() (ListTest) -- lists", rows["TC-001"]["description"])
        self.assertEqual(rows["TC-003"]["status"], "xray_newer", "a description never hides a change")
        self.xray.calls.clear()
        r = self.apply({"TC-001": "send"})
        self.assertEqual([s["id"] for s in r["sent"]], ["TC-001"])
        puts = [c for c in self.xray.calls if c[0] == "PUT" and c[1].endswith("/issue/ABC-1")]
        self.assertEqual(len(puts), 1)
        self.assertIn("As automated", puts[0][2]["fields"]["description"])
        self.assertNotIn("summary", puts[0][2]["fields"])
        self.assertFalse([c for c in self.xray.calls if "/api/test/ABC-1/step" in c[1] and c[0] != "GET"],
                         "no step is touched")
        self.assertEqual({r["id"]: r["status"] for r in self.plan()["tests"]}["TC-001"], "same")

    def test_a_description_holding_a_private_value_is_not_sent(self):
        cfg = os.path.join(self.root, "src", "main", "resources")
        os.makedirs(cfg)
        with io.open(os.path.join(cfg, "program_configuration.json"), "w", encoding="utf-8") as fh:
            json.dump({"jira_config": {"pat": "tok-SECRET-0123456789"}}, fh)
        rel = "src/test/java/com/hi/api/tests/jira/ListTest.java"
        p = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(p))
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write("class ListTest {\n  /**\n   * Audit: TC-001\n   * Step 1: sends tok-SECRET-0123456789\n"
                     "   */\n  void listStays() { }\n}\n")
        self.write("review.json", {"state": "pending-review", "files": {"created": [rel], "modified": []}})
        self.assertEqual({r["id"]: r["status"] for r in self.plan()["tests"]}["TC-001"], "describe")
        self.xray.calls.clear()
        r = self.apply({"TC-001": "send"})
        self.assertIn("private value", r["failed"][0]["why"])
        self.assertFalse([c for c in self.xray.calls if c[0] == "PUT"], "nothing was sent")

    def automate_tc001(self, step):
        rel = "src/test/java/com/hi/api/tests/jira/ListTest.java"
        p = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write("class ListTest {\n  /**\n   * Audit: TC-001\n   * " + step + "\n   */\n"
                     "  void listStays() { }\n}\n")
        self.write("review.json", {"state": "pending-review", "files": {"created": [rel], "modified": []}})

    def test_a_test_taken_from_the_story_keeps_its_authors_words_and_gets_our_automation_after(self):
        keys = json.load(io.open(os.path.join(self.job, "xray-tests.json"), encoding="utf-8"))
        keys["tests"]["design:TC-001"]["imported"] = True
        self.write("xray-tests.json", keys)
        self.xray.tests["ABC-1"]["description"] = "Their own words about the test."
        self.assertEqual({r["id"]: r["status"] for r in self.plan()["tests"]}["TC-001"], "same",
                         "not automated yet: nothing of ours to add")
        self.automate_tc001("Step 1: listStays() (ListTest) -- lists")
        self.assertEqual({r["id"]: r["status"] for r in self.plan()["tests"]}["TC-001"], "describe")
        self.apply({"TC-001": "send"})
        now = self.xray.tests["ABC-1"]["description"]
        self.assertTrue(now.startswith("Their own words about the test.\n\nAUTOMATION BY THE TEST WORKBENCH"))
        self.assertIn("listStays() (ListTest)", now)
        self.assertNotIn("Created by the test workbench", now, "it was not made here")
        self.assertEqual({r["id"]: r["status"] for r in self.plan()["tests"]}["TC-001"], "same")
        # The test changes: our section is replaced, theirs stays, nothing twice.
        self.automate_tc001("Step 1: listStays() (ListTest) -- lists, sorted")
        self.assertEqual({r["id"]: r["status"] for r in self.plan()["tests"]}["TC-001"], "describe")
        self.apply({"TC-001": "send"})
        now = self.xray.tests["ABC-1"]["description"]
        self.assertEqual(now.count(xu.xt.AUTOMATION_START), 1)
        self.assertEqual(now.count(xu.xt.AUTOMATION_END), 1)
        self.assertEqual(now.count("Their own words about the test."), 1)
        self.assertIn("lists, sorted", now)

    def test_a_case_not_automated_yet_does_not_ask_to_send_its_plan_to_an_older_key(self):
        self.assertEqual({r["id"]: r["status"] for r in self.plan()["tests"]}["TC-001"], "same")


if __name__ == "__main__":
    unittest.main(verbosity=1)
