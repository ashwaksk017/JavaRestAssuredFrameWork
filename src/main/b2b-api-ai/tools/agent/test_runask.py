"""Ask Cursor to fix the failures of a run, from the Run tests tab.

    python tools/agent/test_runask.py

What is tested is the decision -- which existing way a run's failures go
to Cursor, and when neither applies -- the request the person is shown,
that nothing private is in it, and that the send reuses the two existing
paths (the job's agent-followup; a fix brief and agent-generate in a new
job) and refuses rather than writes when it cannot.
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
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ra = _load("runask_under_test", os.path.join(HERE, "runask.py"))
jobs = _load("jobs_for_runask", os.path.join(HERE, "jobs.py"))

IMPORTED = "com.hi.api.tests.imported.amex.spend.SpendTest"
JIRA = "com.hi.api.tests.jira.H4bMemberStaysTest"


def write(root, rel, text):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def test(cls, method, status="FAIL", message="", params="", config=False):
    return {"class": cls, "method": method, "status": status, "message": message, "ms": 10,
            "config": config, "params": params, "groups": []}


def run(*tests, suite="Suites/jira/job-1.xml", at="2026-10-20T10:00:00Z"):
    return {"kind": "mvnrun", "what": "test", "at": at, "env": "stg", "project": "b2b", "suite": suite,
            "test": "", "group": "", "ok": False, "verdict": "1 test(s) failed",
            "passed": sum(t["status"] == "PASS" for t in tests),
            "failed": sum(t["status"] == "FAIL" for t in tests), "skipped": 0, "tests": list(tests)}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="runask-")
        self.addCleanup(shutil.rmtree, self.root, True)
        # No configuration file here: nothing is private unless a test says so.
        self.addCleanup(setattr, ra.loop, "private_config", ra.loop.private_config)
        ra.loop.private_config = lambda root="", strict=False: {}

    def job(self, name, result=None, review=None):
        d = ra.intake.job_dir(name, self.root)
        os.makedirs(d, exist_ok=True)
        if result is not None:
            write(self.root, f".workbench/{name}/mvnrun-result.json", json.dumps(result))
        if review is not None:
            write(self.root, f".workbench/{name}/review.json", json.dumps(review))
        return d

    def suite(self, job, *classes):
        write(self.root, f"Suites/jira/{job}.xml", "<suite><test><classes>"
              + "".join(f'<class name="{c}"/>' for c in classes) + "</classes></test></suite>")


class WhichWayApplies(Base):
    def test_no_run_a_passing_run_and_a_run_without_results_have_nothing_to_fix(self):
        self.assertEqual(ra.classify(self.root, "job-1", {})["failed"], 0)
        self.assertIn("no tests have been run", ra.classify(self.root, "job-1", {})["why"])
        self.assertEqual(ra.classify(self.root, "job-1", {"what": "compile", "ok": False})["failed"], 0)
        passed = ra.classify(self.root, "job-1", run(test(JIRA, "a", "PASS")))
        self.assertEqual((passed["kind"], passed["failed"]), ("none", 0))
        self.assertIn("no failed test", passed["why"])
        broken = ra.classify(self.root, "job-1", {"what": "test", "verdict": "it did not compile", "at": ""})
        self.assertEqual(broken["failed"], 0)
        self.assertIn("it did not compile", broken["why"])
        skipped = ra.classify(self.root, "job-1", run(test(JIRA, "a", "SKIP", "config key x is not set")))
        self.assertEqual(skipped["failed"], 0, "a skip is not a failure AI Agent is asked to fix")

    def test_the_jobs_own_suite_goes_to_that_jobs_ask_cursor(self):
        self.job("job-1")
        self.suite("job-1", JIRA)
        got = ra.classify(self.root, "runs", run(test(JIRA, "staysTest", message="expected [200] but found [500]")))
        self.assertEqual((got["kind"], got["job"], got["failed"]), ("job", "job-1", 1))
        self.assertIn("Suites/jira/job-1.xml", got["why"])
        self.assertIn("Ask Me", got["why"])
        self.assertEqual(got["blocked"], "")

    def test_the_change_waiting_for_review_makes_a_class_the_jobs_whatever_was_run(self):
        self.job("job-2", review={"state": "pending-review", "files": {
            "created": ["src/test/java/com/hi/api/tests/jira/NewTest.java"], "modified": []}})
        got = ra.classify(self.root, "job-2", run(test("com.hi.api.tests.jira.NewTest", "a"),
                                                  suite="Suites/jira/group-smoke.xml"))
        self.assertEqual((got["kind"], got["job"]), ("job", "job-2"))
        self.assertIn("the change waiting for its review", got["why"])
        # Once that change is discarded, the class is no longer the job's.
        self.job("job-2", review={"state": "discarded", "files": {
            "created": ["src/test/java/com/hi/api/tests/jira/NewTest.java"]}})
        got = ra.classify(self.root, "job-2", run(test("com.hi.api.tests.jira.NewTest", "a"),
                                                  suite="Suites/jira/group-smoke.xml"))
        self.assertEqual(got["kind"], "none")

    def test_another_jobs_tests_are_said_not_sent(self):
        self.job("job-1")
        self.suite("job-1", JIRA)
        got = ra.classify(self.root, "job-1", run(test("com.hi.api.tests.jira.SomeoneElsesTest", "a")))
        self.assertEqual(got["kind"], "none")
        self.assertIn("not job job-1's tests", got["why"])
        self.assertEqual(got["failed"], 1, "the box still shows, with why it cannot send")

    def test_a_changed_that_cannot_take_a_request_is_said_before_anything_is_written(self):
        self.suite("job-1", JIRA)
        for review, words in (({"state": "pushed", "branch": "agent/job-1"}, "already approved and pushed"),
                              ({"state": "pending-review", "mock": True}, "stand-in"),
                              ({"state": "generating"}, "has not finished"),
                              ({"state": "push-failed"}, "push failed")):
            self.job("job-1", review=review)
            got = ra.classify(self.root, "job-1", run(test(JIRA, "a")))
            self.assertEqual(got["kind"], "job")
            self.assertIn(words, got["blocked"])

    def test_one_converted_suite_goes_to_a_fix_brief(self):
        got = ra.classify(self.root, "runs", run(test(IMPORTED, "getSpend403Test"), suite="Suites/Amex_spend.xml"))
        self.assertEqual((got["kind"], got["suite"]), ("converted", "amex"))
        self.assertIn("Fix with AI Agent on the Failures tab", got["why"])
        self.assertIn("new job of its own", got["why"])

    def test_several_suites_or_a_mix_are_refused_with_what_to_do(self):
        two = ra.classify(self.root, "runs", run(test(IMPORTED, "a"),
                                                 test("com.hi.api.tests.imported.goal.shop.ShopTest", "b")))
        self.assertEqual(two["kind"], "none")
        self.assertIn("2 converted suites (amex, goal)", two["why"])
        mix = ra.classify(self.root, "runs", run(test(IMPORTED, "a"), test(JIRA, "b")))
        self.assertEqual(mix["kind"], "none")
        self.assertIn("together", mix["why"])

    def test_a_classic_convert_is_a_converted_suite_too(self):
        # --classic writes under tests.classic.<suite>: its failures go to a fix brief as well.
        self.assertEqual(ra.converted_suite("com.hi.api.tests.classic.amex.x.ATest"), "amex")
        self.assertEqual(ra.converted_suite("com.hi.api.tests.imported.amex.x.ATest"), "amex")

    def test_the_names_that_are_read_from_a_run_are_checked(self):
        self.assertEqual(ra.converted_suite("com.hi.api.tests.imported.Amex-x.A"), "")
        self.assertEqual(ra.converted_suite("com.hi.api.tests.jira.A"), "")
        self.assertEqual(ra.suite_job("Suites/jira/group-smoke.xml"), "")
        self.assertEqual(ra.suite_job("Suites/jira/../../x.xml"), "")
        self.assertEqual(ra.suite_job("Suites/jira/job-1.xml"), "job-1")
        self.assertEqual(ra.case_id("{jira_xray_id=ABC-1, name=x}"), "ABC-1")
        self.assertEqual(ra.case_id("{name=x}"), "")


class TheRequest(Base):
    def test_it_names_each_test_its_case_why_and_the_runs_own_lines(self):
        self.suite("job-1", JIRA)
        d = self.job("job-1", result=run(
            test(JIRA, "staysTest", message="expected [200] but found [500]",
                 params="{jira_xray_id=ABC-1, name=stay}"),
            test(JIRA, "setUp", message="no token", config=True)))
        write(self.root, ".workbench/job-1/mvn-test.log",
              "[INFO] Running\n[ERROR] Failures:\n[ERROR]   H4bMemberStaysTest.staysTest:42 status 500\n"
              "[ERROR]   OtherTest.x:1 nope\n")
        write(self.root, "target/failure-digest.txt",
              "FAILURE DIGEST -- Demo\n\n" + ra.DIGEST_SECTION + "\n"
              "H4bMemberStaysTest.staysTest\tABC-1\tAssertionError | status 500 from Create Stay\n")
        got = ra.preview(self.root, "job-1")
        text = got["text"]
        self.assertTrue(text.startswith("Fix the tests of job job-1 that failed"))
        self.assertIn("- H4bMemberStaysTest.staysTest [case ABC-1]: expected [200] but found [500]", text)
        self.assertIn("- H4bMemberStaysTest.setUp (setup): no token", text)
        self.assertIn("H4bMemberStaysTest.staysTest:42 status 500", text)
        self.assertIn("status 500 from Create Stay", text, "the digest written by this run")
        self.assertNotIn("OtherTest", text)
        self.assertIn("do not change the expected value", text)
        # A digest older than the run is another run's: not quoted.
        os.utime(os.path.join(self.root, "target", "failure-digest.txt"), (1_600_000_000, 1_600_000_000))
        self.assertNotIn("Create Stay", ra.preview(self.root, "job-1")["text"])
        self.assertTrue(os.path.isdir(d))

    def test_a_refused_body_field_points_at_the_converted_bodies(self):
        # Seen live: employeeCountRange 0 copied from a reference example;
        # the server named the field, and the request did not say where its value is.
        self.suite("job-1", JIRA)
        refused = ('The following asserts failed: TC-001/SETUP: POST create program account status -- the '
                   'server said: {"context":"PROGRAMACCOUNTS","code":998,"notifications":[{"code":"31",'
                   '"fields":["businessProfile.employeeCountRange"],"message":"Invalid JSON Parameter Value"}]}')
        self.job("job-1", result=run(test(JIRA, "a", message=refused)))
        text = ra.preview(self.root, "job-1")["text"]
        self.assertIn("refused the field(s) businessProfile.employeeCountRange", text)
        self.assertIn("src/main/resources/templates/", text)
        self.assertIn("not from the reference examples", text)
        self.job("job-1", result=run(test(JIRA, "a", message="expected [200] but found [500]")))
        self.assertNotIn("refused the field", ra.preview(self.root, "job-1")["text"])

    def test_nothing_private_is_in_it(self):
        self.suite("job-1", JIRA)
        self.job("job-1", result=run(test(
            JIRA, "a", message="POST https://api.internal-corp.example.test/stays failed for key s3cretvalue42")))
        ra.loop.private_config = lambda root="", strict=False: {"s3cretvalue42": "a credential"}
        text = ra.preview(self.root, "job-1")["text"]
        self.assertNotIn("s3cretvalue42", text)
        self.assertNotIn("internal-corp", text)
        self.assertIn("<redacted>", text)
        self.assertIn("https://host.invalid/stays", text)

    def test_an_unreadable_configuration_file_refuses_rather_than_guesses(self):
        def broken(root="", strict=False):
            raise ValueError("program_configuration.json")
        ra.loop.private_config = broken
        self.suite("job-1", JIRA)
        self.job("job-1", result=run(test(JIRA, "a")))
        with self.assertRaises(ValueError) as got:
            ra.preview(self.root, "job-1")
        self.assertIn("nothing is sent to AI Agent", str(got.exception))

    def test_a_converted_run_lists_at_most_what_one_fix_job_takes(self):
        self.job("runs", result=run(*[test(IMPORTED, f"t{n}Test") for n in range(12)], suite="Suites/Amex.xml"))
        text = ra.preview(self.root, "runs")["text"]
        self.assertTrue(text.startswith("Make the 12 failing case(s) of the converted suite amex pass"))
        self.assertIn("- ... and 2 more (one fix job takes at most 10", text)
        self.assertIn("surefire's message and stack", text)

    def test_the_fix_briefs_failures_are_this_runs_and_this_suites(self):
        got = ra.fixbrief_failures(run(test(IMPORTED, "a", message="403  here", params="{xray_key=ABC-2}"),
                                       test("com.hi.api.tests.imported.goal.X", "b"),
                                       test(IMPORTED, "c", "PASS")), "amex")
        self.assertEqual(got, [{"test": IMPORTED + ".a", "row": "ABC-2", "signature": "403 here"}])

    def test_a_fix_job_is_always_a_new_one(self):
        from datetime import datetime
        when = datetime(2026, 10, 20, 10, 0, 0)
        first = ra.fix_job_name(self.root, "amex", when)
        self.assertEqual(first, "fix-amex-20261020-100000")
        os.makedirs(ra.intake.job_dir(first, self.root))
        self.assertEqual(ra.fix_job_name(self.root, "amex", when), first + "-2")
        self.assertLessEqual(len(ra.fix_job_name(self.root, "x" * 80, when)), 64)


JIRA_FILE = "src/test/java/com/hi/api/tests/jira/H4bMemberStaysTest.java"


def compiled(*errors, what="compile"):
    """A run whose compile failed: Compile itself, or a run's compile first."""
    part = {"compile": {"ok": False, "exit_code": 1, "how": "offline", "errors": list(errors), "seconds": 3}}
    if what == "compile":
        return dict(part, kind="mvnrun", what="compile", at="2026-10-26T10:00:00Z", ok=False,
                    verdict="the compile failed")
    return dict(part, kind="mvnrun", what="test", at="2026-10-26T10:00:00Z", env="stg", project="b2b",
                suite="Suites/jira/job-1.xml", test="", group="", ok=False,
                verdict="the compile failed: no test was run")


class CompileErrors(Base):
    """A compile that failed: no test ran, but the errors name their files."""

    def test_the_jobs_own_file_goes_to_its_ask_me_with_the_errors(self):
        self.suite("job-1", JIRA)
        self.job("job-1", result=compiled(f"{JIRA_FILE}:42: cannot find symbol",
                                          f"{JIRA_FILE}:50: ';' expected"))
        got = ra.preview(self.root, "job-1")
        self.assertEqual((got["kind"], got["job"], got["failed"], got["compile"]), ("job", "job-1", 2, True))
        self.assertIn("its suite Suites/jira/job-1.xml", got["why"])
        self.assertTrue(got["text"].startswith("Fix the compile errors in job job-1's files: the last Compile"))
        self.assertIn(f"- {JIRA_FILE}:42: cannot find symbol", got["text"])
        self.assertIn("Make the project compile", got["text"])

    def test_a_runs_compile_first_and_a_run_that_did_not_compile_count_too(self):
        self.job("job-1")
        self.suite("job-1", JIRA)
        got = ra.classify(self.root, "job-1", compiled(f"{JIRA_FILE}:1: x", what="test"))
        self.assertEqual((got["kind"], got["compile"]), ("job", True))
        self.job("job-1", result={"what": "test", "at": "", "suite": "Suites/jira/job-1.xml", "ok": False,
                                  "verdict": "it did not compile", "errors": [f"{JIRA_FILE}:1: x"]})
        text = ra.preview(self.root, "job-1")["text"]
        self.assertIn("the last run of Suites/jira/job-1.xml", text)

    def test_a_helper_in_the_waiting_change_is_the_jobs(self):
        helper = "src/main/java/com/hi/api/rest/utilities/StayHelper.java"
        self.job("job-2", review={"state": "pending-review", "files": {"created": [helper], "modified": []}})
        got = ra.classify(self.root, "job-2", compiled(f"{helper}:9: x"))
        self.assertEqual((got["kind"], got["job"]), ("job", "job-2"))
        self.assertIn("the change waiting for its review", got["why"])

    def test_shared_generated_files_are_said_not_sent(self):
        self.job("job-1")
        self.suite("job-1", JIRA)
        gen = ra.classify(self.root, "job-1", compiled(
            "src/main/java/com/hi/api/support/scenario/Insights.java:12: cannot find symbol"))
        self.assertEqual((gen["kind"], gen["failed"]), ("none", 1), "support/scenario/ is every suite's")
        self.assertIn("generated support file", gen["why"])
        self.assertIn("Convert the suite again", gen["why"])

    def test_a_converted_suite_that_does_not_compile_goes_to_convert_fix(self):
        self.job("job-1")
        self.suite("job-1", JIRA)
        self.job("job-1", result=compiled(
            "src/test/java/com/hi/api/tests/imported/amex/spend/SpendTest.java:3: x",
            "src/main/java/com/hi/api/support/amex/AmexSteps.java:9: y",
            "src/main/java/com/hi/api/support/scenario/Insights.java:12: z"))
        got = ra.preview(self.root, "job-1")
        self.assertEqual((got["kind"], got["suites"], got["fix_jobs"], got["blocked"]),
                         ("convert-fix", ["amex"], ["job-1-fix-amex"], ""))
        self.assertEqual(got["shared"], ["src/main/java/com/hi/api/support/scenario/Insights.java"])
        self.assertIn("a fix job of its own (job-1-fix-amex)", got["why"])
        self.assertIn("a person fixes those", got["why"])
        self.assertNotIn("text", got, "convertfix writes the brief")
        self.job("job-1-fix-amex", review={"state": "pending-review"})
        self.assertIn("already has a fix waiting (job-1-fix-amex)", ra.preview(self.root, "job-1")["blocked"])

    def test_the_suite_places_are_convertfixs(self):
        cf = ra._load("test_runask_convertfix", os.path.join(ra.HERE, "convertfix.py"))
        self.assertEqual([r.pattern for r in ra.CONVERTED_RX], [r.pattern for r in cf._SUITE_RX])
        for rel in ("src/main/java/com/hi/api/support/scenario/X.java",
                    "src/main/java/com/hi/api/rest/clients/AmexClient.java", "src/main/java/X.java"):
            self.assertEqual(ra.converted_owner(rel), cf.suite_of(rel), rel)

    def test_someone_elses_files_or_no_file_are_said_not_sent(self):
        self.job("job-1")
        self.suite("job-1", JIRA)
        other = ra.classify(self.root, "job-1", compiled(
            "src/test/java/com/hi/api/tests/jira/OtherTest.java:3: x", f"{JIRA_FILE}:1: y"))
        self.assertEqual(other["kind"], "none")
        self.assertIn("not job job-1's", other["why"])
        self.assertIn("OtherTest.java", other["why"])
        none = ra.classify(self.root, "job-1", compiled("[ERROR] something odd"))
        self.assertEqual(none["kind"], "none")
        self.assertIn("name no source file", none["why"])

    def test_a_project_under_a_src_folder_still_finds_its_files(self):
        # Maven's real output, read the way mvnrun reads it: this project sits
        # under .../src/main/b2b-api-ai/, so the line starts at the outer src/.
        out = ("[ERROR] /C:/work/Framework/src/main/b2b-api-ai/" + JIRA_FILE + ":[42,9] cannot find symbol\n"
               "[ERROR] /C:/work/Framework/src/main/b2b-api-ai/src/main/java/com/hi/api/support/scenario/"
               "Insights.java:[3,1] x\n")
        errors = ra.loop.compile_errors(out)
        self.assertEqual([ra.error_file(e) for e in errors],
                         [JIRA_FILE, "src/main/java/com/hi/api/support/scenario/Insights.java"])
        self.job("job-1")
        self.suite("job-1", JIRA)
        got = ra.classify(self.root, "job-1", compiled(errors[0]))
        self.assertEqual((got["kind"], got["job"]), ("job", "job-1"))
        self.assertIn("generated support file", ra.classify(self.root, "job-1", compiled(errors[1]))["why"])

    BACKBOOK = "src/test/java/com/hi/api/tests/imported/amexbackbook/book/BookTest.java"
    HELPER = "src/main/java/com/hi/api/rest/utilities/StayHelper.java"

    def test_a_converted_suite_the_jobs_change_broke_goes_to_the_job(self):
        # Automate changed a shared helper; its own check found the converted
        # suite failing after it (verify-failed): the change is the fix.
        self.job("job-3", review={"state": "verify-failed",
                                  "files": {"created": [], "modified": [self.HELPER]},
                                  "verify": {"ok": False, "steps": [{"name": "compile", "rc": 1, "errors": [
                                      f"src/main/b2b-api-ai/{self.BACKBOOK}:12: cannot find symbol"]}]}})
        self.job("job-3", result=compiled(f"{self.BACKBOOK}:12: cannot find symbol"))
        got = ra.preview(self.root, "job-3")
        self.assertEqual((got["kind"], got["job"], got["generated"]), ("job", "job-3", [self.BACKBOOK]))
        self.assertIn("suite amexbackbook", got["why"])
        self.assertIn("the change is what broke them", got["why"])
        self.assertIn("Do not edit those", got["text"])

    def test_the_jobs_own_errors_next_to_a_converted_suites_go_to_the_job(self):
        new = "src/test/java/com/hi/api/tests/jira/NewTest.java"
        self.job("job-3", review={"state": "pending-review", "files": {"created": [new], "modified": []},
                                  "verify": {"ok": True, "steps": []}})
        got = ra.classify(self.root, "job-3", compiled(f"{new}:3: x", f"{self.BACKBOOK}:12: y"))
        self.assertEqual(got["kind"], "job")
        self.assertIn("and in job job-3's own files", got["why"])

    def test_a_converted_suite_broken_before_the_change_says_so(self):
        # The change passed its check: these files failed before it too.
        self.job("job-3", review={"state": "pending-review",
                                  "files": {"created": ["src/test/java/com/hi/api/tests/jira/NewTest.java"]},
                                  "verify": {"ok": True, "steps": []}})
        got = ra.classify(self.root, "job-3", compiled(f"{self.BACKBOOK}:12: y"))
        self.assertEqual((got["kind"], got["suites"]), ("convert-fix", ["amexbackbook"]))
        self.assertIn("failed before job job-3's change too", got["why"])
        self.assertIn("job job-3's new tests included", got["why"])
        self.assertIn("Migration tab (Ask Me, scope converter)", got["why"])

    def test_the_count_includes_the_errors_left_off_the_list(self):
        self.assertEqual(ra.error_count([f"{JIRA_FILE}:1: x", "... and 7 more error(s)"]), 8)

    def test_a_compile_that_passed_is_nothing_to_ask_about(self):
        ok = {"what": "compile", "ok": True, "compile": {"ok": True, "errors": []}}
        self.assertEqual(ra.classify(self.root, "job-1", ok)["failed"], 0)


class Sending(Base):
    """jobs.run_ask: the two existing paths, and the refusals before them."""

    def setUp(self):
        Base.setUp(self)                   # shared with RunAgainAfterTheFix
        for owner in (jobs, jobs.intake):
            self.addCleanup(setattr, owner, "ROOT", owner.ROOT)
            owner.ROOT = self.root
        self.addCleanup(jobs._TOOLS.pop, "runask", None)
        jobs._TOOLS["runask"] = ra
        self.started = []
        self.addCleanup(setattr, jobs, "start", jobs.start)
        jobs.start = lambda job, runnable, options: (self.started.append((job, runnable, options)),
                                                     {"started": True})[1]
        self.addCleanup(setattr, jobs, "running_now", jobs.running_now)
        jobs.running_now = lambda: []

    def read(self, job, name):
        with io.open(os.path.join(jobs.job_dir(job), name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_jobs_own_tests_go_to_its_ask_cursor(self):
        self.suite("job-1", JIRA)
        self.job("job-1", result=run(test(JIRA, "a", message="boom")))
        ra.loop.private_config = lambda root="", strict=False: {"s3cretvalue42": "a credential"}
        r = jobs.run_ask("job-1", "Fix staysTest; the key s3cretvalue42 is right.")
        self.assertEqual((r["kind"], r["job"], r["runnable"]), ("job", "job-1", "agent-followup"))
        self.assertEqual(self.started, [("job-1", "agent-followup", {"--job": "job-1"})])
        asked = self.read("job-1", "agent-followup.txt")
        self.assertIn("Fix staysTest", asked)
        self.assertNotIn("s3cretvalue42", asked, "what the person typed is redacted too")

    def test_the_request_goes_to_the_job_whose_suite_was_run(self):
        self.job("job-7")
        self.suite("job-7", JIRA)
        self.job("runs", result=run(test(JIRA, "a"), suite="Suites/jira/job-7.xml"))
        r = jobs.run_ask("runs", "Fix it.")
        self.assertEqual(r["job"], "job-7")
        self.assertEqual(self.read("job-7", "agent-followup.txt"), "Fix it.")
        self.assertFalse(os.path.exists(os.path.join(jobs.job_dir("runs"), "agent-followup.txt")))

    def test_a_converted_suites_failures_become_a_fix_brief_in_a_new_job(self):
        write(self.root, "src/test/java/com/hi/api/tests/imported/amex/spend/SpendTest.java", "class SpendTest {}\n")
        write(self.root, "src/main/java/com/hi/api/support/amex/AmexSteps.java", "class AmexSteps {}\n")
        self.job("runs", result=run(test(IMPORTED, "getSpend403Test", message="expected [200] but found [403]",
                                         params="{jira_xray_id=ABC-3}"), suite="Suites/Amex_spend.xml"))
        r = jobs.run_ask("runs", "Use the employee's token. Bearer abcdefghij0123456789abcdefghij")
        self.assertEqual((r["kind"], r["suite"], r["runnable"], r["cases"]), ("converted", "amex", "agent-generate", 1))
        self.assertTrue(r["job"].startswith("fix-amex-"))
        self.assertEqual(self.started, [(r["job"], "agent-generate",
                                         {"--job": r["job"], "--scope": "converted", "--suite": "amex"})])
        brief = self.read(r["job"], "brief.md")
        self.assertIn("# Fix brief: suite amex -- 1 failing case(s)", brief)
        self.assertIn("from: .workbench/runs/mvnrun-result.json", brief)
        self.assertIn("src/test/java/com/hi/api/tests/imported/amex/spend/SpendTest.java", brief)
        self.assertIn("## What the person asks\n\nUse the employee's token.", brief)
        self.assertNotIn("abcdefghij0123456789", brief)
        plan = self.read(r["job"], "plan.md")
        self.assertTrue(plan.startswith("# Plan: fix the converted Java of suite amex"))
        self.assertIn("The brief ends with what the person asks", plan)
        self.assertEqual(json.loads(self.read(r["job"], "project.json")), {"project": "b2b"})

    def test_what_is_refused_writes_nothing(self):
        self.suite("job-1", JIRA)
        with self.assertRaises(ValueError) as got:
            jobs.run_ask("job-1", "Fix it.")
        self.assertIn("no tests have been run", str(got.exception))
        self.job("job-1", result=run(test(JIRA, "a", "PASS")))
        with self.assertRaises(ValueError) as got:
            jobs.run_ask("job-1", "Fix it.")
        self.assertIn("no failed test", str(got.exception))
        self.job("job-1", result=run(test(JIRA, "a")))
        with self.assertRaises(ValueError) as got:
            jobs.run_ask("job-1", "   ")
        self.assertIn("write what AI Agent should fix", str(got.exception))
        jobs.running_now = lambda: [("job-9", "mvn-test")]
        with self.assertRaises(ValueError) as got:
            jobs.run_ask("job-1", "Fix it.")
        self.assertIn("mvn-test is running (job job-9)", str(got.exception))
        jobs.running_now = lambda: []
        self.job("job-1", review={"state": "pushed"})
        with self.assertRaises(ValueError) as got:
            jobs.run_ask("job-1", "Fix it.")
        self.assertIn("already approved", str(got.exception))
        self.assertEqual(self.started, [])
        self.assertFalse(os.path.exists(os.path.join(jobs.job_dir("job-1"), "agent-followup.txt")))

    def test_compile_errors_in_the_jobs_files_go_to_its_ask_me(self):
        self.suite("job-1", JIRA)
        self.job("job-1", result=compiled(f"{JIRA_FILE}:42: cannot find symbol"))
        r = jobs.run_ask("job-1", "Fix the compile.")
        self.assertEqual((r["kind"], r["job"], r["runnable"]), ("job", "job-1", "agent-followup"))
        self.assertEqual(self.read("job-1", "agent-followup.txt"), "Fix the compile.")
        self.assertEqual(json.loads(self.read("job-1", "agent-mustfix.json")), {"files": [JIRA_FILE]},
                         "the fix is checked against the files that failed, not excused by the baseline")
        self.job("job-1", result=run(test(JIRA, "a")))
        jobs.run_ask("job-1", "Fix the test.")
        self.assertFalse(os.path.exists(os.path.join(jobs.job_dir("job-1"), "agent-mustfix.json")),
                         "a failed test is not a compile fix")
        self.job("job-1", result=compiled("src/main/java/com/hi/api/support/scenario/Insights.java:1: x"))
        with self.assertRaises(ValueError) as got:
            jobs.run_ask("job-1", "Fix it.")
        self.assertIn("Convert the suite again", str(got.exception))

    AMEX = "src/test/java/com/hi/api/tests/imported/amex/spend/SpendTest.java"

    def test_a_converted_suite_that_does_not_compile_starts_convert_fix(self):
        self.job("job-1", result=compiled(f"{self.AMEX}:3: x"))
        r = jobs.run_ask("job-1", "")
        self.assertEqual((r["kind"], r["runnable"], r["fix_jobs"]), ("convert-fix", "convert-fix", ["job-1-fix-amex"]))
        self.assertEqual(self.started, [("job-1", "convert-fix", {"--job": "job-1"})])

    def test_a_failed_compile_asked_for_it_is_sent_by_itself(self):
        self.suite("job-1", JIRA)
        self.job("job-1", result=dict(compiled(f"{JIRA_FILE}:42: cannot find symbol"), auto_fix=True))
        got = jobs.auto_fix("job-1")
        self.assertEqual((got["sent"], got["kind"], got["job"], got["runnable"]),
                         (True, "job", "job-1", "agent-followup"))
        self.assertIn(f"{JIRA_FILE}:42: cannot find symbol", self.read("job-1", "agent-followup.txt"))
        self.assertEqual(json.loads(self.read("job-1", "agent-mustfix.json")), {"files": [JIRA_FILE]})
        self.assertEqual(jobs.run_ask_preview("job-1")["auto"]["job"], "job-1", "the page is told")

    def test_nothing_is_sent_by_itself_unless_asked_or_allowed(self):
        self.suite("job-1", JIRA)
        self.job("job-1", result=compiled(f"{JIRA_FILE}:42: x"))
        self.assertEqual(jobs.auto_fix("job-1"), {}, "not asked for")
        self.job("job-1", result=dict(run(test(JIRA, "a")), auto_fix=True))
        self.assertEqual(jobs.auto_fix("job-1"), {}, "a failed test is the person's to send")
        self.job("job-1", result=dict(compiled("src/main/java/com/hi/api/support/scenario/I.java:1: x"),
                                      auto_fix=True))
        self.assertEqual(jobs.auto_fix("job-1"), {}, "what goes nowhere stays in the box")
        self.assertEqual(self.started, [])
        jobs.running_now = lambda: [("job-9", "convert")]
        self.job("job-1", result=dict(compiled(f"{JIRA_FILE}:42: x"), auto_fix=True))
        got = jobs.auto_fix("job-1")
        self.assertEqual(got["sent"], False)
        self.assertIn("convert is running", got["error"])
        self.assertIn("convert is running", jobs.run_ask_preview("job-1")["auto"]["error"])
        self.job("job-1", result=dict(compiled(f"{JIRA_FILE}:42: x"), at="2026-10-27T09:00:00Z"))
        self.assertNotIn("auto", jobs.run_ask_preview("job-1"), "an earlier run's record is not this run's")

    def test_the_compile_commands_take_auto_fix(self):
        self.assertIn("--auto-fix", jobs.build_argv("mvn-compile", {"--job": "j", "--auto-fix": True}))
        self.assertIn("--auto-fix", jobs.build_argv("mvn-test", {"--job": "j", "--suite": "S.xml",
                                                                 "--auto-fix": True}))

    def test_a_converted_fix_that_does_not_start_leaves_no_job(self):
        self.job("runs", result=run(test(IMPORTED, "a"), suite="Suites/Amex.xml"))

        def refuse(job, runnable, options):
            raise RuntimeError("convert is running for job 'x'")
        jobs.start = refuse
        with self.assertRaises(RuntimeError):
            jobs.run_ask("runs", "Fix it.")
        left = [n for n in os.listdir(os.path.join(self.root, ".workbench")) if n.startswith("fix-")]
        self.assertEqual(left, [])


class RunAgainAfterTheFix(Base):
    """jobs.rerun_after_fix: seen live -- Fix Failures made a change, its
    check passed, and nothing ran the failed tests again; the person had to."""

    setUp = Sending.setUp
    read = Sending.read

    def sent(self, job="job-1"):
        self.suite(job, JIRA)
        self.job(job, result=run(test(JIRA, "a"), test(JIRA, "b", "PASS"), test(JIRA, "setUp", config=True)))
        jobs.run_ask(job, "Fix it.")
        self.started.clear()

    def record(self, job="job-1"):
        return json.loads(self.read(job, jobs.RERUN_RECORD))

    def test_a_checked_fix_runs_only_the_failed_tests_again(self):
        self.sent()
        self.assertEqual(self.record()["state"], "waiting")
        self.assertIn("rerun", jobs.run_ask_preview("job-1"), "the page says the tests will run again")
        self.job("job-1", review={"state": "pending-review"})
        got = jobs.rerun_after_fix("job-1", "agent-followup", 0)
        self.assertEqual(got["state"], "started")
        self.assertEqual(self.started, [("job-1", "mvn-test", {
            "--env": "stg", "--project": "b2b", "--suite": "Suites/jira/job-1.xml",
            "--compile-first": True, "--failed-only": True})])
        named = json.loads(self.read("job-1", "mvnrun-rerun.json"))
        self.assertEqual(sorted((t["method"], t["config"]) for t in named["tests"]),
                         [("a", False), ("setUp", True)], "the failed ones, a failed setup included")
        self.assertNotIn("--auto-fix", self.started[0][2], "what still fails never loops back by itself")
        self.assertEqual(jobs.rerun_after_fix("job-1", "agent-followup", 0), {}, "once")

    def test_a_fix_that_failed_its_check_is_not_run_and_says_why(self):
        self.sent()
        self.job("job-1", review={"state": "verify-failed"})
        got = jobs.rerun_after_fix("job-1", "agent-followup", 1)
        self.assertEqual((got["state"], self.started), ("skipped", []))
        self.assertIn("did not pass its check", got["why"])
        self.assertIn("did not pass its check", jobs.run_ask_preview("job-1")["rerun"]["why"])

    def test_a_run_made_by_hand_meanwhile_is_not_run_over(self):
        self.sent()
        self.job("job-1", result=run(test(JIRA, "a"), at="2026-10-28T08:00:00Z"),
                 review={"state": "pending-review"})
        got = jobs.rerun_after_fix("job-1", "agent-followup", 0)
        self.assertEqual((got["state"], self.started), ("skipped", []))

    def test_only_a_fix_fix_failures_started_runs_anything(self):
        self.job("job-1", review={"state": "pending-review"})
        self.assertEqual(jobs.rerun_after_fix("job-1", "agent-followup", 0), {})
        self.sent()
        self.assertEqual(jobs.rerun_after_fix("job-1", "agent-generate", 0), {}, "another command's end")
        self.assertEqual(self.started, [])

    def test_a_converted_suites_fix_runs_in_its_own_job_and_the_run_comes_back(self):
        write(self.root, "src/test/java/com/hi/api/tests/imported/amex/spend/SpendTest.java", "class SpendTest {}\n")
        write(self.root, "src/main/java/com/hi/api/support/amex/AmexSteps.java", "class AmexSteps {}\n")
        self.job("runs", result=dict(run(test(IMPORTED, "getSpend403Test"), suite="Suites/Amex_spend.xml"),
                                     threads=3))
        r = jobs.run_ask("runs", "Fix it.")
        self.started.clear()
        self.job(r["job"], review={"state": "pending-review"})
        jobs.rerun_after_fix(r["job"], "agent-generate", 0)
        self.assertEqual(self.started, [("runs", "mvn-test", {
            "--env": "stg", "--project": "b2b", "--suite": "Suites/Amex_spend.xml", "--threads": "3",
            "--compile-first": True, "--failed-only": True})])

    def test_a_compile_fix_compiles_again(self):
        self.suite("job-1", JIRA)
        self.job("job-1", result=compiled(f"{JIRA_FILE}:42: cannot find symbol"))
        jobs.run_ask("job-1", "Fix the compile.")
        self.started.clear()
        self.job("job-1", review={"state": "pending-review"})
        jobs.rerun_after_fix("job-1", "agent-followup", 0)
        self.assertEqual(self.started, [("job-1", "mvn-compile", {})])

    def test_a_refused_restart_is_said_not_raised(self):
        self.sent()
        self.job("job-1", review={"state": "pending-review"})

        def refuse(job, runnable, options):
            raise RuntimeError("convert is running for job 'x'")
        jobs.start = refuse
        got = jobs.rerun_after_fix("job-1", "agent-followup", 0)
        self.assertEqual(got["state"], "skipped")
        self.assertIn("convert is running", got["why"])

    def test_a_fix_that_never_started_leaves_nothing_waiting(self):
        self.suite("job-1", JIRA)
        self.job("job-1", result=run(test(JIRA, "a")))

        def refuse(job, runnable, options):
            raise RuntimeError("busy")
        jobs.start = refuse
        with self.assertRaises(RuntimeError):
            jobs.run_ask("job-1", "Fix it.")
        self.assertFalse(os.path.exists(os.path.join(jobs.job_dir("job-1"), jobs.RERUN_RECORD)))
        self.assertFalse(os.path.exists(os.path.join(jobs.job_dir("job-1"), jobs.RERUN_FROM)))

    def test_the_rerun_flag_is_a_command_option(self):
        self.assertIn("--failed-only", jobs.build_argv("mvn-test", {"--job": "j", "--suite": "S.xml",
                                                                    "--failed-only": True}))


if __name__ == "__main__":
    unittest.main(verbosity=1)
