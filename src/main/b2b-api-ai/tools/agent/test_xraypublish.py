"""Publishing a run to Xray: the keys, one new Test Execution, the regression set.

    python tools/agent/test_xraypublish.py
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
spec = importlib.util.spec_from_file_location("xraypublish_under_test", os.path.join(HERE, "xraypublish.py"))
xp = importlib.util.module_from_spec(spec)
sys.modules["xraypublish_under_test"] = xp
spec.loader.exec_module(xp)

CONF = {"env": "stg", "base": "https://jira.example.test", "token": "tok-123456",
        "project": "ABC", "regression": "ABC-500"}


class Xray:
    def __init__(self, reg_type="Test Set"):
        self.calls, self.reg_type = [], reg_type

    def __call__(self, method, url, token, timeout, body=None):
        self.calls.append((method, url, body))
        if url.endswith("/import/execution"):
            return {"testExecIssue": {"key": "ABC-901"}}
        if "/issue/ABC-500" in url:
            return {"fields": {"issuetype": {"name": self.reg_type}}}
        if "/issue/ABC-777" in url:
            return {"fields": {"issuetype": {"name": "Test Execution"}}}
        if "/issue/ABC-778" in url:
            return {"fields": {"issuetype": {"name": "Story"}}}
        if "/test" in url:
            return {}
        raise AssertionError(url)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="xraypublish-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.job = xp.loop.intake.job_dir("j1", self.root)
        os.makedirs(self.job)
        java = os.path.join(self.root, "src", "test", "java", "com", "hi", "api", "tests", "jira")
        os.makedirs(java)
        with io.open(os.path.join(java, "StaysTest.java"), "w", encoding="utf-8") as fh:
            fh.write('class StaysTest {\n  @Test(groups = {"jira", "regression"})\n  @XrayTest("ABC-11")\n'
                     '  public void owner_200() { }\n\n  @Test(groups = {"jira"})\n  public void noKey() { }\n}\n')
        self.ran = {"at": "2026-10-15T12:00:00Z", "env": "stg", "passed": 3, "failed": 1, "skipped": 0,
                    "tests": [
                        {"class": "com.hi.api.tests.jira.StaysTest", "method": "owner_200", "status": "PASS",
                         "params": "", "groups": ["jira", "regression"]},
                        {"class": "com.hi.api.tests.jira.StaysTest", "method": "rows", "status": "PASS",
                         "params": "{test_case_id=r1, jira_xray_id=ABC-12}", "groups": ["jira", "regression"]},
                        {"class": "com.hi.api.tests.jira.StaysTest", "method": "rows", "status": "FAIL",
                         "params": "{test_case_id=r2, jira_xray_id=ABC-12}", "groups": ["jira", "regression"]},
                        {"class": "com.hi.api.tests.jira.StaysTest", "method": "noKey", "status": "PASS",
                         "params": "", "groups": ["jira"]}]}
        with io.open(os.path.join(self.job, "execution.json"), "w", encoding="utf-8") as fh:
            json.dump(self.ran, fh)
        self.change("pushed")

    def change(self, state):
        with io.open(os.path.join(self.job, "review.json"), "w", encoding="utf-8") as fh:
            json.dump({"state": state}, fh)


class Publishing(Base):
    def test_one_new_execution_with_each_key_and_its_worst_row(self):
        x = Xray()
        r = xp.run("j1", self.root, transport=x, conf=dict(CONF), mocked=False)
        self.assertEqual(r["execution"], "ABC-901")
        self.assertEqual(r["published"], {"ABC-11": "PASS", "ABC-12": "FAIL"}, "a key's worst row")
        self.assertEqual(r["not_published"], [{"test": "StaysTest.noKey",
                                               "why": "no jira_xray_id in its row and no @XrayTest on it"}])
        post = [c for c in x.calls if c[1].endswith("/rest/raven/1.0/import/execution")][0]
        self.assertEqual(post[2]["info"]["project"], "ABC")
        self.assertEqual(post[2]["info"]["testEnvironments"], ["stg"])
        self.assertNotIn("testExecutionKey", post[2], "a new Test Execution every time")
        add = [c for c in x.calls if c[1].endswith("/rest/raven/1.0/api/testset/ABC-500/test")][0]
        self.assertEqual(add[2], {"add": ["ABC-11", "ABC-12"]})
        self.assertTrue(r["ok"])

    def test_a_test_plan_takes_the_execution_too(self):
        x = Xray("Test Plan")
        xp.run("j1", self.root, transport=x, conf=dict(CONF), mocked=False)
        post = [c for c in x.calls if c[1].endswith("/import/execution")][0]
        self.assertEqual(post[2]["info"]["testPlanKey"], "ABC-500")
        self.assertTrue(any(c[1].endswith("/api/testplan/ABC-500/test") for c in x.calls))

    def test_mock_contacts_nobody(self):
        r = xp.run("j1", self.root, transport=lambda *a, **k: 1 / 0, conf={}, mocked=True)
        self.assertEqual((r["execution"], r["mock"]), ("MOCK-EXEC", True))
        self.assertEqual(r["sent"]["tests"][0]["testKey"], "ABC-11")


class TheGapsClosed(Base):
    def test_the_skill_template_annotation_is_found(self):
        java = os.path.join(self.root, "src", "test", "java", "com", "hi", "api", "tests", "jira", "SkillTest.java")
        with io.open(java, "w", encoding="utf-8") as fh:
            fh.write('class SkillTest {\n  void before() { int x = 1; }\n'
                     '  @Test(groups = {"jira"}, retryAnalyzer = RetryAnalyzer.class)\n'
                     '  @XrayTest("B2B-1234")\n  @Story("B2B-1234 activate a pending account")\n'
                     '  @Description("AC: POST /businesses/{accountId}/activate on a pending; account")\n'
                     '  public void activatePendingAccount_204() { }\n}\n')
        self.assertEqual(xp.annotation_key(self.root, "com.hi.api.tests.jira.SkillTest",
                                           "activatePendingAccount_204"), "B2B-1234")
        self.assertEqual(xp.annotation_key(self.root, "com.hi.api.tests.jira.SkillTest", "before"), "")

    def test_a_switched_off_row_is_not_published_as_todo(self):
        by_key, _ = xp.keyed(self.root, [
            {"class": "C", "method": "m", "status": "PASS", "params": "{jira_xray_id=ABC-1}"},
            {"class": "C", "method": "m", "status": "SKIP", "params": "{jira_xray_id=ABC-1}",
             "message": "execute=N -- switched off in the data sheet, not a failure", "switched_off": True}])
        self.assertEqual(by_key, {"ABC-1": "PASS"})

    def test_the_confirmed_cases_are_the_regression_ones_and_a_publish_is_not_repeated(self):
        with io.open(os.path.join(self.job, "workflow.json"), "w", encoding="utf-8") as fh:
            json.dump({"regression": {"cases": ["TC-001"]}}, fh)
        with io.open(os.path.join(self.job, "xray-tests.json"), "w", encoding="utf-8") as fh:
            json.dump({"tests": {"design:TC-001": {"key": "ABC-11"}, "design:TC-002": {"key": "ABC-12"}}}, fh)
        x = Xray()
        xp.run("j1", self.root, transport=x, conf=dict(CONF), mocked=False)
        add = [c for c in x.calls if c[1].endswith("/api/testset/ABC-500/test")][0]
        self.assertEqual(add[2], {"add": ["ABC-11"]}, "ABC-12 is in the group but was not confirmed")
        with self.assertRaises(xp.Refused) as got:
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False)
        self.assertIn("published already, as ABC-901", str(got.exception))
        # ...also after a publish into an existing execution in between.
        xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False, execution="ABC-777")
        with self.assertRaises(xp.Refused):
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False)

    def test_a_failed_regression_add_keeps_the_execution_on_record(self):
        class Refuses(Xray):
            def __call__(self, method, url, token, timeout, body=None):
                if url.endswith("/test"):
                    raise xp.search.JiraError(403, "no permission")
                return super().__call__(method, url, token, timeout, body)
        r = xp.run("j1", self.root, transport=Refuses(), conf=dict(CONF), mocked=False)
        self.assertEqual(r["execution"], "ABC-901")
        self.assertIn("HTTP 403", r["regression"]["error"])
        with io.open(os.path.join(self.job, "xray-publish.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["execution"], "ABC-901")


class AnyRunAnyExecution(Base):
    def test_a_failed_run_of_a_change_not_pushed_is_published_without_regression(self):
        self.change("pending-review")
        x = Xray()
        r = xp.run("j1", self.root, transport=x, conf=dict(CONF), mocked=False)
        self.assertEqual((r["execution"], r["change"]), ("ABC-901", "pending-review"))
        self.assertEqual(r["published"]["ABC-12"], "FAIL", "the failure is published as it ran")
        self.assertFalse(any(c[1].endswith("/test") for c in x.calls), "no regression add before the push")
        self.assertFalse(any("/issue/ABC-500" in c[1] for c in x.calls))
        self.assertIn("once the change is pushed", r["regression"]["note"])
        post = [c for c in x.calls if c[1].endswith("/import/execution")][0]
        self.assertIn("not pushed yet", post[2]["info"]["description"])

    def test_results_go_into_an_existing_test_execution(self):
        x = Xray("Test Plan")
        r = xp.run("j1", self.root, transport=x, conf=dict(CONF), mocked=False, execution="abc-777")
        post = [c for c in x.calls if c[1].endswith("/import/execution")][0]
        self.assertEqual(post[2]["testExecutionKey"], "ABC-777")
        self.assertNotIn("info", post[2], "its summary, description and plan are left as they are")
        self.assertEqual({t["testKey"]: t["status"] for t in post[2]["tests"]}, {"ABC-11": "PASS", "ABC-12": "FAIL"})
        self.assertEqual((r["execution"], r["existing"]), ("ABC-777", True))
        self.assertIn("not the Test Execution ABC-777", r["warning"], "this fake answers ABC-901: said")
        # The same run again, into the same execution: allowed (it updates).
        xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False, execution="ABC-777")

    def test_an_existing_execution_must_be_a_test_execution(self):
        with self.assertRaises(xp.Refused) as got:
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False, execution="ABC-778")
        self.assertIn("not a Test Execution", str(got.exception))
        with self.assertRaises(xp.Refused):
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False, execution="ABC 1; drop")


class GapsFromReview(Base):
    def test_published_before_the_push_then_again_after_adds_only_the_regression(self):
        self.change("pending-review")
        first = Xray()
        r1 = xp.run("j1", self.root, transport=first, conf=dict(CONF), mocked=False)
        self.assertTrue(r1["regression"]["pending"])
        self.change("pushed")
        second = Xray()
        r2 = xp.run("j1", self.root, transport=second, conf=dict(CONF), mocked=False)
        self.assertFalse(any(c[1].endswith("/import/execution") for c in second.calls), "no second execution")
        self.assertEqual(r2["execution"], "ABC-901")
        self.assertEqual(r2["regression"]["added"], ["ABC-11", "ABC-12"])
        with self.assertRaises(xp.Refused):
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False)

    def test_a_run_of_files_changed_since_is_not_published(self):
        path = os.path.join(self.root, "src", "test", "java", "com", "hi", "api", "tests", "jira", "StaysTest.java")
        rel = "src/test/java/com/hi/api/tests/jira/StaysTest.java"
        self.ran["hashes"] = {rel: xp.loop._sha(path)}
        with io.open(os.path.join(self.job, "execution.json"), "w", encoding="utf-8") as fh:
            json.dump(self.ran, fh)
        with io.open(path, "a", encoding="utf-8") as fh:
            fh.write("// edited by hand\n")
        with self.assertRaises(xp.Refused) as got:
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False)
        self.assertIn("changed since this run", str(got.exception))

    def test_no_key_says_how_a_test_gets_one(self):
        self.ran["tests"] = [t for t in self.ran["tests"] if t["method"] == "noKey"]
        with io.open(os.path.join(self.job, "execution.json"), "w", encoding="utf-8") as fh:
            json.dump(self.ran, fh)
        with self.assertRaises(xp.Refused) as got:
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False)
        self.assertIn("Create in Xray", str(got.exception))


class ReportedByTheRun(Base):
    def test_a_run_that_reported_itself_is_not_published_again(self):
        self.ran["reported_by_run"] = True
        with io.open(os.path.join(self.job, "execution.json"), "w", encoding="utf-8") as fh:
            json.dump(self.ran, fh)
        self.change("pending-review")
        with self.assertRaises(xp.Refused) as got:
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False)
        self.assertIn("reported its results to Xray itself", str(got.exception))
        self.change("pushed")
        x = Xray()
        r = xp.run("j1", self.root, transport=x, conf=dict(CONF), mocked=False)
        self.assertFalse(any(c[1].endswith("/import/execution") for c in x.calls), "no second execution")
        self.assertEqual(r["regression"]["added"], ["ABC-11", "ABC-12"], "only the regression add is left")
        # Into an existing execution: still allowed.
        xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False, execution="ABC-777")


class WhatIsRefused(Base):
    def test_no_run_no_settings_no_keys_cloud_wrong_issue_type(self):
        with self.assertRaises(xp.Refused) as got:
            xp.run("j1", self.root, transport=Xray(), conf={"base": "", "token": "", "project": ""}, mocked=False)
        self.assertIn("Settings tab", str(got.exception))
        with self.assertRaises(xp.Refused):
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF, base="https://x.atlassian.net"), mocked=False)
        with self.assertRaises(xp.Refused) as got:
            xp.run("j1", self.root, transport=Xray("Story"), conf=dict(CONF), mocked=False)
        self.assertIn("not a Test Set or a Test Plan", str(got.exception))
        os.remove(os.path.join(self.job, "execution.json"))
        with self.assertRaises(xp.Refused) as got:
            xp.run("j1", self.root, transport=Xray(), conf=dict(CONF), mocked=False)
        self.assertIn("Run the tests first", str(got.exception))


if __name__ == "__main__":
    unittest.main(verbosity=1)
