"""Each designed case in words, step by step: planned from the design, and
as automated from the Audit comment of its test -- whose methods are
checked against the code.

    python tools/agent/test_casedescribe.py
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
spec = importlib.util.spec_from_file_location("casedescribe_under_test", os.path.join(HERE, "casedescribe.py"))
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

JIRA = "src/test/java/com/hi/api/tests/jira/"
BASE = "src/test/java/com/hi/api/tests/StoryTestBase.java"
CASE = {"id": "TC-001", "title": "an owner enrolls a guest", "endpoint": "POST /enroll",
        "endpoint_in_spec": True, "preconditions": "a service token",
        "steps": [{"action": "enroll the owner", "data": "a valid source code", "expected": "201",
                   "source": "spec: POST /enroll 201"},
                  {"action": "enroll the guest", "data": "", "expected": "the guest id is returned",
                   "source": "story: AC 2"}],
        "requirement_refs": ["AC 2: the owner can add a guest"]}
AUDIT = """package x;
class EnrollTest extends StoryTestBase {
    /**
     * Audit: TC-001
     * Step 1: serviceToken() (StoryTestBase) -- the service token
     * Step 2: enrollActor() (StoryTestBase) -- enrolls the owner; payload templates/enroll.json
     * Step 3: captures guestId from the enroll response; step 4 uses it
     * Step 4: madeUpHelper() (StoryTestBase) -- enrolls the guest
     * Step 5: statusCode(201) (RestAssured) -- checks the guest was enrolled
     * Reference: golden test StoryTestExample; B2B_FLOWS.md "Enroll an owner"; spec POST /enroll
     */
    @Test(groups = {"jira"})
    public void ownerEnrollsAGuest() { }
}
"""


def write(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


class Planned(unittest.TestCase):
    CTX = {"label": "B2B", "flows": "B2B_FLOWS.md", "base_class": "StoryTestBase",
           "golden": ["StoryTestExample"], "spec": "accounts.yaml"}

    def test_each_step_in_words_with_where_it_came_from(self):
        lines = cd.planned(CASE, self.CTX)
        text = "\n".join(lines)
        self.assertIn("Endpoint POST /enroll -- from the API specification accounts.yaml.", lines)
        self.assertIn("Before step 1: a service token", lines)
        self.assertIn("Step 1: enroll the owner, with a valid source code. Checks: 201 "
                      "(from the API specification: POST /enroll 201)", lines)
        self.assertIn("Step 2: enroll the guest. Checks: the guest id is returned (from the story: AC 2)", lines)
        self.assertIn("Covers: AC 2: the owner can add a guest", lines)
        self.assertIn("B2B flow specification (B2B_FLOWS.md) and the golden test StoryTestExample; "
                      "the test extends StoryTestBase", text)

    def test_an_endpoint_not_in_the_spec_or_with_no_spec_says_so(self):
        out = cd.planned(dict(CASE, endpoint_in_spec=False), self.CTX)[0]
        self.assertIn("NOT in the API specification accounts.yaml: from the story", out)
        none = dict(CASE)
        none.pop("endpoint_in_spec")
        self.assertIn("no API specification was given", cd.planned(none, dict(self.CTX, spec=""))[0])


class AsAutomated(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="casedescribe-")
        self.addCleanup(shutil.rmtree, self.root, True)
        write(self.root, BASE, "abstract class StoryTestBase {\n"
                               "  protected String serviceToken() { return \"\"; }\n"
                               "  protected Actor enrollActor(String role) throws Exception { return null; }\n}\n")
        write(self.root, JIRA + "EnrollTest.java", AUDIT)

    def test_the_audit_comment_is_read_and_a_method_defined_nowhere_is_marked(self):
        got = cd.as_automated(self.root, [JIRA + "EnrollTest.java", "csv/manual/x.csv"])
        lines = got["TC-001"]["lines"]
        self.assertEqual(got["TC-001"]["file"], JIRA + "EnrollTest.java")
        self.assertEqual(lines[0], "Step 1: serviceToken() (StoryTestBase) -- the service token")
        self.assertTrue(lines[3].endswith("[not found in the code: madeUpHelper()]"), lines[3])
        self.assertNotIn("not found", lines[1], "enrollActor is defined, with throws")
        self.assertNotIn("not found", lines[4], "a library's method is not the project's to define")
        self.assertTrue(lines[-1].startswith("Reference: golden test StoryTestExample"))

    def test_a_data_driven_method_s_audit_describes_each_of_its_cases(self):
        got = cd.audits("/**\n * Audit: TC-003, TC-004 -- one method, a row each\n"
                        " * Step 1: serviceToken() (StoryTestBase) -- the token\n */\n")
        self.assertEqual(set(got), {"TC-003", "TC-004"})
        self.assertEqual(got["TC-003"], ["one method, a row each",
                                         "Step 1: serviceToken() (StoryTestBase) -- the token"])
        self.assertEqual(cd.audits("/** Not an audit: TC-001 */\n/**\n * DATA_INVENTORY: none\n */"), {})

    def test_a_job_s_cases_planned_and_as_automated_with_the_reviewed_steps(self):
        job = os.path.join(self.root, "target", "agent-jobs", "J")
        write(self.root, "target/agent-jobs/J/design.json",
              json.dumps({"test_cases": [CASE, dict(CASE, id="TC-002")]}))
        write(self.root, "target/agent-jobs/J/project.json", json.dumps({"project": "goal"}))
        write(self.root, "target/agent-jobs/J/review.json", json.dumps(
            {"state": "pending-review", "files": {"created": [JIRA + "EnrollTest.java"], "modified": []}}))
        write(self.root, "target/agent-jobs/J/case-review.json", json.dumps(
            {"design": {"TC-002": {"steps": [{"action": "a corrected step", "data": "", "expected": "200"}]}}}))
        got = cd.describe(self.root, job)
        self.assertTrue(got["TC-001"]["automated"], "TC-001 has its Audit comment")
        self.assertEqual(got["TC-002"]["automated"], [])
        self.assertIn("Step 1: a corrected step. Checks: 200", got["TC-002"]["planned"])
        self.assertIn("GOAL flow specification (GOAL_FLOWS.md)", got["TC-002"]["planned"][-1])
        self.assertIn("GoalStoryTestExample", got["TC-002"]["planned"][-1])
        xray = cd.lines_for(got["TC-001"])
        self.assertTrue(xray[0].startswith("As automated (" + JIRA + "EnrollTest.java"))
        self.assertIn("As planned:", xray)
        self.assertEqual(cd.lines_for(got["TC-002"])[0], "As planned:")
        self.assertNotEqual(cd.digest(xray), cd.digest(cd.lines_for(got["TC-002"])))
        self.assertFalse(got["TC-001"]["stale"], "no record of what its test was written for")
        # Its test was written for the steps as they were; a person edits them after.
        write(self.root, "target/agent-jobs/J/review.json", json.dumps(
            {"state": "pending-review", "change": "c1",
             "files": {"created": [JIRA + "EnrollTest.java"], "modified": []}}))
        write(self.root, "target/agent-jobs/J/automated-cases.json", json.dumps(
            {"TC-001": {"written": True, "change": "c1", "print": cd.case_print(CASE)}}))
        self.assertFalse(cd.describe(self.root, job)["TC-001"]["stale"])
        write(self.root, "target/agent-jobs/J/case-review.json", json.dumps(
            {"design": {"TC-001": {"steps": [{"action": "enroll the owner", "data": "",
                                              "expected": "201 and a member id"}]}}}))
        now = cd.describe(self.root, job)["TC-001"]
        self.assertTrue(now["stale"])
        self.assertTrue(cd.lines_for(now)[0].startswith("NOTE: the steps were changed after this test"))
        write(self.root, "target/agent-jobs/J/automated-cases.json", json.dumps(
            {"TC-001": {"written": True, "change": "an earlier, discarded change", "print": "x"}}))
        self.assertFalse(cd.describe(self.root, job)["TC-001"]["stale"], "a record of another change is not this one's")
        # No change waiting: nothing is "as automated" any more.
        write(self.root, "target/agent-jobs/J/review.json", json.dumps({"state": "rejected"}))
        self.assertEqual(cd.describe(self.root, job)["TC-001"]["automated"], [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
