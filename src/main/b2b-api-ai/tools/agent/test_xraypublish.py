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
