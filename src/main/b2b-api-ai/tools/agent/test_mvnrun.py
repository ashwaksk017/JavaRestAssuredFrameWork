"""Compile and run from the page: only what is on disk is run, and a broken
JAVA_HOME is worked around, not trusted.

    python tools/agent/test_mvnrun.py

Maven is a function here; no build is started.
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


mr = _load("mvnrun_under_test", os.path.join(HERE, "mvnrun.py"))

RESULTS = """<?xml version="1.0" encoding="UTF-8"?>
<testng-results skipped="0" failed="1" total="2" passed="1">
  <suite name="s"><test name="t"><class name="com.hi.api.tests.jira.FooTest">
    <test-method status="PASS" name="ok" duration-ms="1200"/>
    <test-method status="FAIL" name="bad" duration-ms="300"><exception class="AssertionError"><message>expected 200 but was 404</message></exception></test-method>
  </class></test></suite>
</testng-results>
"""


def write(root, rel, text=""):
    path = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="b2b-mr-")
        self.job_dir = os.path.join(self.root, ".workbench", "runs")
        write(self.root, "Suites/jira/group-smoke.xml", "<suite/>")
        write(self.root, "Suites/jira/job-1.xml", "<suite/>")
        write(self.root, "Suites/Amex/AmexSuite.xml", "<suite/>")
        write(self.root, "src/test/resources/testng-jira.xml", "<suite/>")
        write(self.root, "src/test/java/com/hi/api/tests/jira/FooTest.java", "class FooTest {}")
        write(self.root, "src/main/resources/program_configuration.json",
              json.dumps({"stg": {"a": 1}, "qa": {"a": 1}, "note": "x"}))
        self.calls = []
        self.allure_rc = 0

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def runner(self, results=RESULTS, rc=1, compile_rc=0, text=""):
        def run(args):
            self.calls.append(args)
            if args[0] == "test-compile":
                return compile_rc, "" if not compile_rc else \
                    "[ERROR] /x/src/test/java/FooTest.java:[3,5] cannot find symbol\n[ERROR] BUILD FAILURE"
            if args[0] == "allure:report":
                return self.allure_rc, "[ERROR] allure commandline could not be downloaded" if self.allure_rc else ""
            if results:
                write(self.root, mr.RESULTS_XML.replace(os.sep, "/"), results)
            return rc, text
        return run

    def result(self):
        with io.open(os.path.join(self.job_dir, mr.RESULT), encoding="utf-8") as fh:
            return json.load(fh)

    def mvn_test_call(self):
        return next(c for c in self.calls if c[0] == "test")

    def quiet(self, fn, *a, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fn(*a, **kw)
        return rc, buf.getvalue()


class Targets(Base):
    def test_suites_groups_and_envs_are_listed_from_disk(self):
        paths = [s["path"] for s in mr.list_suites(self.root)]
        self.assertEqual(paths[0], "Suites/jira/group-smoke.xml")
        self.assertIn("src/test/resources/testng-jira.xml", paths)
        self.assertIn("Suites/Amex/AmexSuite.xml", paths)
        self.assertEqual(mr.list_groups(self.root), ["jira", "smoke"])
        self.assertEqual(sorted(mr.list_envs(self.root)["envs"])[:2], ["qa", "stg"])

    def test_what_is_not_on_disk_or_not_a_name_is_refused(self):
        for bad in ("../pom.xml", "Suites/none.xml", "pom.xml", "Suites/x.xml;calc"):
            with self.assertRaises(ValueError, msg=bad):
                mr.check_suite(self.root, bad)
        self.assertEqual(mr.check_test(self.root, "FooTest#ok*"), "FooTest#ok*")
        for bad in ("BarTest", "FooTest; rm", "-Dx=y"):
            with self.assertRaises(ValueError, msg=bad):
                mr.check_test(self.root, bad)


class Running(Base):
    def test_a_suite_run_reads_the_results_and_says_failed(self):
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, suite="Suites/jira/group-smoke.xml",
                              env="stg", runner=self.runner())
        self.assertEqual(rc, 1, said)
        r = self.result()
        self.assertEqual((r["passed"], r["failed"]), (1, 1))
        self.assertIn("-DsuiteXmlFile=Suites/jira/group-smoke.xml", self.mvn_test_call())
        self.assertIn("-Denv=stg", self.mvn_test_call())
        self.assertEqual(next(t for t in r["tests"] if t["status"] == "FAIL")["message"], "expected 200 but was 404")

    def test_a_group_runs_its_suite_file(self):
        self.quiet(mr.cmd_test, self.job_dir, self.root, group="jira", env="qa", runner=self.runner())
        self.assertIn("-DsuiteXmlFile=src/test/resources/testng-jira.xml", self.mvn_test_call())
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, group="nightly", env="qa", runner=self.runner())
        self.assertEqual(rc, 1)
        self.assertIn("no suite file", said)

    def test_a_class_runs_with_dtest(self):
        self.quiet(mr.cmd_test, self.job_dir, self.root, test="FooTest", env="stg", runner=self.runner())
        self.assertIn("-Dtest=FooTest", self.mvn_test_call())

    def test_a_failed_compile_runs_no_test(self):
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, test="FooTest", env="stg",
                              compile_first=True, runner=self.runner(compile_rc=1))
        self.assertEqual(rc, 1)
        self.assertEqual([c[0] for c in self.calls], ["test-compile"])
        r = self.result()
        self.assertFalse(r["compile"]["ok"])
        self.assertTrue(r["compile"]["errors"])

    def test_no_results_is_a_failure_with_the_tail(self):
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, test="FooTest", env="stg",
                              runner=self.runner(results="", text="something broke\nBUILD FAILURE"))
        self.assertEqual(rc, 1)
        self.assertIn("BUILD FAILURE", " ".join(self.result()["tail"]))

    def test_exactly_one_thing_is_run(self):
        rc, _ = self.quiet(mr.cmd_test, self.job_dir, self.root, suite="Suites/jira/group-smoke.xml",
                           test="FooTest", runner=self.runner())
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])

    def test_a_run_is_a_clean_build_then_the_tests_then_the_allure_report(self):
        for rel in ("target/test-classes/Old.class", "target/allure-results/old-result.json",
                    "target/convert-input/Suite.xml", "target/failure-digest.txt"):
            write(self.root, rel, "x")
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, test="FooTest", env="stg",
                              compile_first=True, runner=self.runner(rc=0))
        self.assertEqual([c[0] for c in self.calls], ["test-compile", "test", "allure:report"])
        for gone in ("target/test-classes", "target/allure-results"):
            self.assertFalse(os.path.exists(os.path.join(self.root, gone)), gone)
        for kept in ("target/convert-input/Suite.xml", "target/failure-digest.txt"):
            self.assertTrue(os.path.isfile(os.path.join(self.root, kept)), kept + " is the workbench's")
        self.assertTrue(self.result()["allure"]["ok"])
        self.assertIn("clean build", said)

    def test_a_report_that_cannot_be_built_leaves_the_verdict_alone(self):
        self.allure_rc = 1
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, suite="Suites/jira/group-smoke.xml",
                              env="stg", runner=self.runner())
        self.assertEqual(rc, 1, "the run's own verdict (one test failed)")
        r = self.result()
        self.assertEqual((r["passed"], r["failed"]), (1, 1))
        self.assertFalse(r["allure"]["ok"])
        self.assertIn("could not be downloaded", r["allure"]["why"])
        self.assertIn("the test result stands", said)

    def test_the_report_server_reaches_only_the_reports_files(self):
        bs = mr.buildsteps
        write(self.root, "target/allure-report/index.html", "<html>report</html>")
        write(self.root, "target/allure-report/app.js", "x()")
        write(self.root, "target/failure-digest.txt", "secret-ish")
        self.assertEqual(bs.report_file(self.root, "/")[1], "text/html; charset=utf-8")
        self.assertEqual(bs.report_file(self.root, "/app.js")[1], "text/javascript; charset=utf-8",
                         "with nosniff, a script served as text/plain does not run")
        for bad in ("/../failure-digest.txt", "/..\\failure-digest.txt", "/nope.html", "/a\x00b"):
            self.assertIsNone(bs.report_file(self.root, bad), bad)
        self.assertTrue(bs.report_ready(self.root))

    def test_compile_alone(self):
        rc, said = self.quiet(mr.cmd_compile, self.job_dir, self.root, runner=self.runner())
        self.assertEqual(rc, 0, said)
        self.assertTrue(self.result()["compile"]["ok"])


class Java(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b2b-jdk-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def jdk(self, name, version):
        home = os.path.join(self.tmp, name)
        exe = ".exe" if os.name == "nt" else ""
        for tool in ("java", "javac"):
            write(home, f"bin/{tool}{exe}", "")
        write(home, "release", f'JAVA_VERSION="{version}"\n')
        return home

    def test_a_good_java_home_is_kept(self):
        home = self.jdk("jdk-17", "17.0.9")
        env, note = mr.java_env({"JAVA_HOME": home, "PATH": ""}, candidates=[])
        self.assertEqual((env["JAVA_HOME"], note), (home, ""))

    def test_a_missing_java_home_is_replaced_by_the_oldest_jdk_17_or_later(self):
        old = self.jdk("jdk1.8", "1.8.0_391")
        j25 = self.jdk("jdk-25", "25")
        j21 = self.jdk("jdk-21", "21.0.2")
        env, note = mr.java_env({"JAVA_HOME": os.path.join(self.tmp, "jdk-17"), "PATH": "p"},
                                candidates=[old, j25, j21])
        self.assertEqual(env["JAVA_HOME"], j21)
        self.assertTrue(env["PATH"].startswith(os.path.join(j21, "bin")))
        self.assertIn("using Java 21", note)

    def test_no_usable_jdk_is_said(self):
        env, note = mr.java_env({"JAVA_HOME": "", "PATH": ""}, candidates=[self.jdk("jdk1.8", "1.8.0")])
        self.assertIn("no JDK 17+", note)


if __name__ == "__main__":
    unittest.main(verbosity=1)
