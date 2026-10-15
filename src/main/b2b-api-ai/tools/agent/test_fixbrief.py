"""A failing converted case becomes a job for Cursor.

    python tools/agent/test_fixbrief.py
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


fb = _load("fixbrief_under_test", os.path.join(HERE, "fixbrief.py"))

TEST = "com.hi.api.tests.imported.amex.spend.SpendTest.getSpend403Test"
SIG = "HTTP 403 on GET /spend -- expected 200"


def write(root, rel, text):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fixbrief-")
        self.addCleanup(shutil.rmtree, self.root, True)
        for owner in (fb, fb.intake):
            self.addCleanup(setattr, owner, "ROOT", owner.ROOT)
            owner.ROOT = self.root
        self.job = fb.intake.job_dir("fix1", self.root)
        snap = {"suite": "Guards", "at": "20261014T100000Z", "label": "after the token fix",
                "tests_run": 40, "failures": [
                    {"test": TEST, "row": "r7", "signature": SIG},
                    {"test": "com.hi.api.tests.imported.amex.spend.SpendTest.getSpend200Test", "row": "-",
                     "signature": "NullPointerException at SpendSteps.java:40"},
                    {"test": "com.hi.api.tests.imported.goal.shop.ShopTest.shopTest", "row": "-",
                     "signature": "HTTP 403 on GET /spend -- expected 200"}]}
        write(self.root, ".failure-history/20261014T100000Z-guards.json", json.dumps(snap))
        write(self.root, "src/test/java/com/hi/api/tests/imported/amex/spend/SpendTest.java", "class SpendTest {}\n")
        write(self.root, "src/test/java/com/hi/api/tests/imported/goal/shop/ShopTest.java", "class ShopTest {}\n")
        write(self.root, "src/main/java/com/hi/api/support/amex/AmexHooks1.java", "class AmexHooks1 {}\n")
        write(self.root, "src/main/java/com/hi/api/support/amex/AmexSteps.java", "class AmexSteps {}\n")
        write(self.root, "target/surefire-reports/TEST-TestSuite.xml",
              '<testsuite><testcase name="getSpend403Test" classname="com.hi.api.tests.imported.amex.spend.SpendTest">'
              '<failure message="expected [200] but found [403] for Bearer abcdefghij0123456789abcdefghij" type="java.lang.AssertionError">'
              "<![CDATA[java.lang.AssertionError: expected [200] but found [403]\n\tat org.testng.Assert.fail(Assert.java:111)\n"
              "\tat com.hi.api.tests.imported.amex.spend.SpendTest.getSpend403Test(SpendTest.java:44)]]></failure></testcase>"
              '<testcase name="other" classname="x.Y"/></testsuite>')
        write(self.root, "logs/com_hi_api_tests_imported_amex_spend_SpendTest__getSpend403Test__row7a.log",
              "GET https://api.example.test/spend\nAuthorization: Bearer abcdefghij0123456789abcdefghij\n"
              "client_secret=sup3rsecretvalue\n<- 403 {\"code\":\"FORBIDDEN\"}\n")
        write(self.root, "_audit/amex/case_to_method_mapping.csv",
              "soapui_suite,soapui_case,xray_key,java_class_fqn,java_method,csv_path,cluster_size,cluster_row_index,expected_status\n"
              "amex,B2B-1_get_spend_403_employee,B2B-9,com.hi.api.tests.imported.amex.spend.SpendTest,getSpend403Test,"
              "src/test/resources/csv/amex/spend/SpendTest/getSpend403Test.csv,1,0,403\n")
        write(self.root, "_audit/amex/runtime_skips.csv",
              "prefix,case,java_method,reason,source_step\namex,B2B-1_get_spend_403_employee,getSpend403Test,"
              "Untranslated JDBC step,query users\n")
        write(self.root, "_audit/amex/placeholders.csv", "case,placeholder,kind\nB2B-1_get_spend_403_employee,c_id,config\n")
        write(self.root, "_audit/amex/preflight.md", "# preflight\n- B2B-1_get_spend_403_employee: token-hoist applied\n- other: x\n")
        write(self.root, "src/main/resources/program_configuration.json",
              json.dumps({"api_config": {"client_secret": "sup3rsecretvalue"}}))
        write(self.root, ".failure-history/notes.json",
              json.dumps({fb.fh.signature_id(SIG): {"text": "the employee token lacks the spend scope", "at": "2026-10-10",
                                                    "signature": SIG}}))

    def read(self, name):
        with io.open(os.path.join(self.job, name), encoding="utf-8") as fh:
            return fh.read()


class TheBrief(Base):
    def test_a_signature_becomes_a_brief_with_everything_the_run_left_behind(self):
        # The signature is shared with a goal case: without a suite the job
        # is refused; named, the job takes amex's case and names goal's.
        with self.assertRaises(fb.Refused) as got:
            fb.build("fix1", self.root, signature=fb.fh.signature_id(SIG))
        self.assertIn("span 2 suites (amex, goal)", str(got.exception))
        g = fb.build("fix1", self.root, signature=fb.fh.signature_id(SIG), suite="amex")
        self.assertTrue(g["ok"])
        self.assertEqual((g["scope"], g["suite"]), ("converted", "amex"))
        self.assertEqual(len(g["cases"]), 1)
        self.assertEqual(g["other_suites"], ["com.hi.api.tests.imported.goal.shop.ShopTest.shopTest (goal)"])
        brief = self.read("brief.md")
        self.assertIn("also fails in other suites, not this job's: com.hi.api.tests.imported.goal.shop.ShopTest.shopTest (goal)", brief)
        for must in ("# Fix brief: suite amex -- 1 failing case(s)", "## Case 1: " + TEST + "  [row r7]",
                     "ReadyAPI case: `B2B-1_get_spend_403_employee`, expected status 403, Xray B2B-9",
                     "test class: `src/test/java/com/hi/api/tests/imported/amex/spend/SpendTest.java`",
                     "data row: `src/test/resources/csv/amex/spend/SpendTest/getSpend403Test.csv` (row r7)",
                     "### What surefire recorded", "java.lang.AssertionError: expected [200] but found [403]",
                     "SpendTest.getSpend403Test(SpendTest.java:44)",
                     "### The last request and response (`logs/com_hi_api_tests_imported_amex_spend_SpendTest__getSpend403Test__row7a.log`, tail)",
                     '<- 403 {"code":"FORBIDDEN"}', "skipped at run time: Untranslated JDBC step (step: query users)",
                     "placeholders: c_id (config)", "preflight: - B2B-1_get_spend_403_employee: token-hoist applied",
                     "## What this signature turned out to be before", "the employee token lacks the spend scope",
                     "AmexHooks1.java, AmexSteps.java", "signature id: [" + fb.fh.signature_id(SIG) + "]"):
            self.assertIn(must, brief, must)
        self.assertNotIn("sup3rsecretvalue", brief, "a configured secret is redacted")
        self.assertNotIn("abcdefghij0123456789", brief, "a bearer token is redacted")
        self.assertNotIn("api.example.test", brief, "a host in a quoted URL is redacted")
        self.assertIn("https://<host>/spend", brief)
        plan = self.read("plan.md")
        self.assertIn("# Plan: fix the converted Java of suite amex", plan)
        self.assertIn("FIX -- make these 1 case(s) pass: " + TEST + " [row r7]", plan)
        self.assertIn("that is a job for the scope 'The converter itself'", plan)
        result = json.loads(self.read("fix-brief.json"))
        self.assertEqual((result["kind"], result["suite"], result["cases"][0]["method"]), ("fix-brief", "amex", "getSpend403Test"))

    def test_a_test_name_chooses_too_and_the_suite_is_told_from_its_package(self):
        g = fb.build("fix1", self.root, test="getSpend200Test")
        self.assertEqual([c["test"] for c in g["cases"]], ["com.hi.api.tests.imported.amex.spend.SpendTest.getSpend200Test"])
        self.assertEqual(g["suite"], "amex")
        self.assertEqual(g["cases"][0]["surefire"], {}, "no surefire entry for it: nothing invented")
        self.assertEqual(g["cases"][0]["log"], {})

    def test_the_emitter_scope_writes_the_emitter_plan(self):
        g = fb.build("fix1", self.root, signature=fb.fh.signature_id(SIG), scope="converter", suite="amex")
        plan = self.read("plan.md")
        self.assertIn("FIX THE EMITTER", plan)
        self.assertIn("never a generated copy", plan)
        self.assertEqual(g["scope"], "converter")


class TheDigestsOwnNames(Base):
    """FailureDigestListener writes `SimpleClass.method`, not a qualified
    name: the class is found by its simple name under tests/imported/,
    the suite from where it is, surefire by the name's tail, the log by
    `Class__method`."""

    def test_a_simple_class_name_is_enough(self):
        snap = {"suite": "Guards", "at": "20261014T110000Z", "label": "", "tests_run": 1,
                "failures": [{"test": "SpendTest.getSpend403Test", "row": "r7", "signature": SIG}]}
        write(self.root, ".failure-history/20261014T110000Z-guards.json", json.dumps(snap))
        g = fb.build("fix1", self.root, test="SpendTest.getSpend403Test")
        c = g["cases"][0]
        self.assertEqual(c["class_file"], "src/test/java/com/hi/api/tests/imported/amex/spend/SpendTest.java")
        self.assertEqual(g["suite"], "amex")
        self.assertEqual(c["surefire"]["classname"], "com.hi.api.tests.imported.amex.spend.SpendTest")
        self.assertIn("expected [200] but found [403]", c["surefire"]["message"])
        self.assertEqual(c["log"]["file"], "logs/com_hi_api_tests_imported_amex_spend_SpendTest__getSpend403Test__row7a.log")
        self.assertEqual(c["audit"]["case"], "B2B-1_get_spend_403_employee")
        self.assertEqual(fb._split_test("SpendTest.getSpend403Test"), ("SpendTest", "getSpend403Test"))
        self.assertEqual(fb._split_test("bare"), ("", "bare"))

    def test_the_same_simple_name_in_two_suites_is_told_apart_or_refused(self):
        write(self.root, "src/test/java/com/hi/api/tests/imported/goal/spend/SpendTest.java", "class SpendTest {}\n")
        snap = {"suite": "Guards", "at": "20261014T110000Z", "label": "", "tests_run": 1,
                "failures": [{"test": "SpendTest.getSpend403Test", "row": "r7", "signature": SIG}]}
        write(self.root, ".failure-history/20261014T110000Z-guards.json", json.dumps(snap))
        # surefire ran amex's: that decides
        g = fb.build("fix1", self.root, test="SpendTest.getSpend403Test")
        self.assertEqual(g["suite"], "amex")
        self.assertEqual(g["cases"][0]["class_file"], "src/test/java/com/hi/api/tests/imported/amex/spend/SpendTest.java")
        # no surefire: the audit of amex maps the method with amex's class
        os.remove(os.path.join(self.root, "target", "surefire-reports", "TEST-TestSuite.xml"))
        shutil.rmtree(self.job)
        g = fb.build("fix1", self.root, test="SpendTest.getSpend403Test")
        self.assertEqual(g["suite"], "amex")
        self.assertEqual(g["cases"][0]["surefire"], {})
        # neither: refused, naming both -- never a guess
        shutil.rmtree(os.path.join(self.root, "_audit"))
        shutil.rmtree(self.job)
        with self.assertRaises(fb.Refused) as got:
            fb.build("fix1", self.root, test="SpendTest.getSpend403Test")
        self.assertIn("exists in suites amex, goal", str(got.exception))
        self.assertIn("name the suite", str(got.exception))
        # named: that suite's class, even with nothing else to go on
        g = fb.build("fix1", self.root, test="SpendTest.getSpend403Test", suite="goal")
        self.assertEqual(g["cases"][0]["class_file"], "src/test/java/com/hi/api/tests/imported/goal/spend/SpendTest.java")
        self.assertEqual(g["cases"][0]["log"], {}, "a simple name matches only its own class's logs")

    def test_a_log_of_another_row_is_said_to_be_possibly_so(self):
        write(self.root, "logs/com_hi_api_tests_imported_amex_spend_SpendTest__getSpend403Test__row7b.log", "later row\n")
        write(self.root, "logs/com_hi_api_tests_imported_amex_spend_OtherSpendTest__getSpend403Test__rowzz.log", "not ours\n")
        g = fb.build("fix1", self.root, test="getSpend403Test", suite="amex")
        log = g["cases"][0]["log"]
        self.assertEqual((log["of"], log["maybe_other_row"]), (2, True))
        self.assertNotIn("OtherSpendTest", log["file"])
        self.assertIn("it may be another row's", self.read("brief.md"))


class WhatIsRefused(Base):
    def test_two_suites_a_bad_scope_an_unknown_signature_and_a_story_job(self):
        with self.assertRaises(fb.Refused) as got:
            fb.build("fix1", self.root)                  # every failure: amex and goal
        self.assertIn("span 2 suites", str(got.exception))
        with self.assertRaises(fb.Refused) as got:
            fb.build("fix1", self.root, test="shopTest", suite="amex")
        self.assertIn("none of the chosen failures is in suite amex", str(got.exception))
        with self.assertRaises(fb.Refused):
            fb.build("fix1", self.root, signature="0123456789")
        with self.assertRaises(fb.Refused):
            fb.build("fix1", self.root, test="getSpend403Test", scope="nothing")
        os.makedirs(self.job, exist_ok=True)
        write(self.root, ".workbench/fix1/intake.json", json.dumps({"story": {"key": "ABC-1"}}))
        with self.assertRaises(fb.Refused) as got:
            fb.build("fix1", self.root, test="getSpend403Test")
        self.assertIn("a fix is its own job", str(got.exception))
        os.remove(os.path.join(self.job, "intake.json"))
        write(self.root, ".workbench/fix1/design.json", "{}")
        with self.assertRaises(fb.Refused) as got:
            fb.build("fix1", self.root, test="getSpend403Test")
        self.assertIn("design.json", str(got.exception))
        os.remove(os.path.join(self.job, "design.json"))
        write(self.root, ".workbench/fix1/plan.md", "# Plan: 3 request(s)\n")
        with self.assertRaises(fb.Refused):
            fb.build("fix1", self.root, test="getSpend403Test")
        os.remove(os.path.join(self.job, "plan.md"))
        fb.build("fix1", self.root, test="getSpend403Test")      # its own plan: running again is fine
        fb.build("fix1", self.root, test="getSpend403Test")

    def test_a_brief_too_long_for_one_prompt_cuts_whole_cases_and_says_which(self):
        self.addCleanup(setattr, fb, "BRIEF_CAP", fb.BRIEF_CAP)
        fb.BRIEF_CAP = 1500
        g = fb.build("fix1", self.root, suite="amex")           # two amex cases
        self.assertEqual(len(g["cases"]), 1, "the last case goes, whole")
        self.assertEqual(g["cut"], ["com.hi.api.tests.imported.amex.spend.SpendTest.getSpend200Test"])
        brief = self.read("brief.md")
        self.assertIn("1 case(s) were cut from this brief", brief)
        self.assertIn("getSpend200Test", brief)
        self.assertIn("## Case 1:", brief)
        self.assertNotIn("## Case 2:", brief)

    def test_the_signatures_own_run_is_found_even_when_a_newer_run_is_another_suites(self):
        newer = {"suite": "Other", "at": "20261014T120000Z", "label": "", "tests_run": 1,
                 "failures": [{"test": "com.hi.api.tests.imported.goal.shop.ShopTest.shopTest", "row": "-",
                               "signature": "something else"}]}
        write(self.root, ".failure-history/20261014T120000Z-other.json", json.dumps(newer))
        g = fb.build("fix1", self.root, signature=fb.fh.signature_id(SIG), suite="amex")
        self.assertEqual(g["run"]["at"], "20261014T100000Z")

    def test_no_record_falls_back_to_the_digest_and_then_refuses(self):
        shutil.rmtree(os.path.join(self.root, ".failure-history"))
        with self.assertRaises(fb.Refused) as got:
            fb.build("fix1", self.root, test="getSpend403Test")
        self.assertIn("Record this run", str(got.exception))
        write(self.root, "target/failure-digest.txt",
              "FAILURE DIGEST -- Guards\nunique failing (test, row) pairs: 1   distinct signatures: 1\n\n"
              + fb.fh.SECTION + "\n" + TEST + "\tr7\t" + SIG + "\n")
        g = fb.build("fix1", self.root, test="getSpend403Test")
        self.assertEqual(g["run"]["source"], fb.fh.DIGEST)
        self.assertEqual(g["suite"], "amex")

    def test_the_command_writes_an_error_result_when_refused(self):
        self.assertEqual(fb.main(["--job", "fix1", "--signature", "0123456789"]), 1)
        self.assertEqual(json.loads(self.read("fix-brief.json"))["kind"], "error")
        self.assertEqual(fb.main(["--job", "fix1", "--test", "getSpend403Test"]), 0)
        self.assertEqual(json.loads(self.read("fix-brief.json"))["kind"], "fix-brief")


if __name__ == "__main__":
    unittest.main(verbosity=1)
