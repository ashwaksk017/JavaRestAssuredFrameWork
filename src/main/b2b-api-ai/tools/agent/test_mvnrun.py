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


class ChosenProject(Base):
    """The project chosen on the Run tests tab decides the configuration
    file, the default environment and -Dconfig.project; a converted suite
    of the other project is warned of, not refused."""

    GOAL_SUITE = "Suites/Group360CreateSTAGE/Group360CreateSTAGE_Regression.xml"

    def setUp(self):
        super().setUp()
        write(self.root, self.GOAL_SUITE, "<suite/>")
        write(self.root, "src/main/resources/program_configuration.goal.json", json.dumps(
            {"goalstg": {"services": {"hospitality_internal_all_v2": "https://goal.example.test",
                                      "hospitality_partner_v2": "https://partner.example.test"}}}))

    def test_goal_chosen_is_goals_file_and_config_project(self):
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, suite=self.GOAL_SUITE, env="goalstg",
                              project="GOAL", runner=self.runner(rc=0))
        self.assertIn("-Dconfig.project=goal", self.mvn_test_call())
        self.assertEqual(self.result()["project"], "goal")
        self.assertNotIn("project_warning", self.result())
        self.assertNotIn("WARN", said)

    def test_the_chosen_project_is_preflighted_not_the_commands(self):
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, test="FooTest", env="stg",
                              project="goal", runner=self.runner())
        self.assertEqual(rc, 1)
        self.assertIn("no `stg` block", said)
        self.assertEqual(self.calls, [], "refused before Maven")

    def test_a_suite_of_the_other_project_is_warned_of_and_still_run(self):
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, suite=self.GOAL_SUITE, env="stg",
                              project="b2b", runner=self.runner(rc=0))
        self.assertIn("WARN", said)
        self.assertIn("GOAL suite", self.result()["project_warning"])
        self.assertIn("-Dconfig.project=b2b", self.mvn_test_call())

    def test_a_project_that_is_not_one_is_refused(self):
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, test="FooTest", env="stg",
                              project="gaol", runner=self.runner())
        self.assertEqual(rc, 1)
        self.assertIn("is not a project", said)
        self.assertEqual(self.calls, [])
        self.assertEqual(mr.check_project(""), "")
        self.assertEqual(mr.check_project(" B2B "), "b2b")

    def test_none_chosen_is_the_commands_own(self):
        self.quiet(mr.cmd_test, self.job_dir, self.root, test="FooTest", env="stg", runner=self.runner())
        self.assertIn("-Dconfig.project=b2b", self.mvn_test_call())

    def test_a_suites_own_project(self):
        self.assertTrue(mr.is_converted(self.GOAL_SUITE))
        self.assertFalse(mr.is_converted("Suites/jira/group-smoke.xml"))
        self.assertEqual(mr.suite_project(self.GOAL_SUITE), "goal")
        self.assertEqual(mr.suite_project("Suites/Amex/AmexSuite.xml"), "b2b")


class ParallelThreads(Base):
    """The threads chosen on the page run a copy of the suite; the suite
    file itself is never changed."""

    def go(self, **kw):
        return self.quiet(mr.cmd_test, self.job_dir, self.root, env="stg", runner=self.runner(rc=0), **kw)

    def copy_of(self):
        arg = next(a for a in self.mvn_test_call() if a.startswith("-DsuiteXmlFile="))
        rel = arg.split("=", 1)[1]
        with io.open(os.path.join(self.root, rel.replace("/", os.sep)), encoding="utf-8") as fh:
            return rel, fh.read()

    def test_a_suite_runs_as_a_copy_with_the_thread_count_and_its_own_parallel_mode(self):
        original = '<suite name="S" parallel="methods" thread-count="3" verbose="1">\n<test name="t"/></suite>\n'
        write(self.root, "Suites/Amex/AmexSuite.xml", original)
        rc, said = self.go(suite="Suites/Amex/AmexSuite.xml", threads=6)
        rel, text = self.copy_of()
        self.assertEqual(rel, "target/run-suites/AmexSuite.threads-6.xml")
        self.assertIn('<suite name="S" parallel="methods" thread-count="6" verbose="1">', text)
        self.assertIn('<test name="t"/>', text)
        with io.open(os.path.join(self.root, "Suites", "Amex", "AmexSuite.xml"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), original, "the suite file is not changed")
        self.assertIn("6 parallel thread(s)", said)
        self.assertEqual(self.result()["threads"], 6)

    def test_a_suite_with_no_parallel_mode_runs_classes_and_one_thread_runs_nothing_in_parallel(self):
        write(self.root, "Suites/jira/group-smoke.xml", '<suite name="jira-smoke" verbose="1"></suite>')
        self.go(group="smoke", threads=4)
        self.assertIn('parallel="classes" thread-count="4"', self.copy_of()[1])
        self.calls.clear()
        self.go(group="smoke", threads=1)
        self.assertIn('parallel="none" thread-count="1"', self.copy_of()[1])

    def test_as_the_suite_says_hands_maven_the_suite_itself(self):
        self.go(suite="Suites/jira/job-1.xml")
        self.assertIn("-DsuiteXmlFile=Suites/jira/job-1.xml", self.mvn_test_call())

    def test_what_is_refused_before_anything_runs(self):
        write(self.root, "src/test/resources/testng-all.xml",
              '<suite name="All"><suite-files><suite-file path="testng-jira.xml"/></suite-files></suite>')
        for kw, why in (({"suite": "Suites/jira/job-1.xml", "threads": 7}, "1 to 6"),
                        ({"test": "FooTest", "threads": 2}, "one class or method runs as it is"),
                        ({"suite": "src/test/resources/testng-all.xml", "threads": 2},
                         "only lists other suite files")):
            self.calls.clear()
            rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, env="stg", compile_first=True,
                                  runner=self.runner(rc=0), **kw)
            self.assertEqual(rc, 1, kw)
            self.assertIn(why, said)
            self.assertEqual(self.calls, [], "refused before any compile or run")


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

    def test_without_a_clean_build_the_report_still_holds_this_run_only(self):
        for rel in ("target/test-classes/Old.class", "target/allure-results/old-result.json",
                    "target/allure-report/index.html"):
            write(self.root, rel, "x")
        rc, said = self.quiet(mr.cmd_test, self.job_dir, self.root, test="FooTest", env="stg",
                              compile_first=False, runner=self.runner(rc=0))
        self.assertEqual([c[0] for c in self.calls], ["test", "allure:report"])
        for gone in ("target/allure-results", "target/allure-report"):
            self.assertFalse(os.path.exists(os.path.join(self.root, gone)), gone)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "target/test-classes/Old.class")),
                        "no clean build: the compiled classes stay")
        self.assertIn("the report holds this run only", said)

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

    def test_compile_alone_is_the_clean_build_a_run_does(self):
        """Compile said "no errors", Run then failed: old classes stood in
        for a generated file that no longer compiled."""
        write(self.root, "target/classes/Old.class", "x")
        rc, said = self.quiet(mr.cmd_compile, self.job_dir, self.root, runner=self.runner())
        self.assertFalse(os.path.exists(os.path.join(self.root, "target/classes/Old.class")))
        self.assertIn("clean build", said)


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

    def test_a_java_home_saved_in_windows_is_used_when_the_process_has_none(self):
        # Started before JAVA_HOME was set: the process has none, Windows has it.
        home = self.jdk("jdk-17", "17.0.9")
        env, note = mr.java_env({"PATH": "p"}, candidates=[], saved=lambda: home)
        self.assertEqual((env["JAVA_HOME"], note), (home, ""))
        self.assertTrue(env["PATH"].startswith(os.path.join(home, "bin")))

    def test_the_process_own_java_home_wins_over_the_saved_one(self):
        mine, saved = self.jdk("jdk-21", "21.0.2"), self.jdk("jdk-17", "17.0.9")
        env, _note = mr.java_env({"JAVA_HOME": mine, "PATH": ""}, candidates=[], saved=lambda: saved)
        self.assertEqual(env["JAVA_HOME"], mine)

    def test_a_broken_saved_java_home_is_named_not_called_unset(self):
        gone = os.path.join(self.tmp, "nowhere")
        _env, note = mr.java_env({"PATH": ""}, candidates=[], saved=lambda: gone)
        self.assertIn(gone, note)
        self.assertNotIn("not set", note)

    def test_a_passed_environment_never_reads_the_registry(self):
        _env, note = mr.java_env({"PATH": ""}, candidates=[])
        self.assertIn("JAVA_HOME is not set", note)

    def test_no_usable_jdk_is_said(self):
        env, note = mr.java_env({"JAVA_HOME": "", "PATH": ""}, candidates=[self.jdk("jdk1.8", "1.8.0")])
        self.assertIn("no JDK 17+", note)


class NeverConverted(unittest.TestCase):
    """A copy that only ever made story tests has no com.hi.api.support:
    every compile failed with the pom's "No converted output found". The
    converter's --bootstrap is run first, once, and only when they are missing."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        conv = os.path.join(self.root, "tools", "ra_converter")
        os.makedirs(conv)
        open(os.path.join(conv, "ra_converter.py"), "w").close()
        self.calls, self.said = [], []

    def run_(self, writes=True, rc=0):
        def run(argv):
            self.calls.append(argv)
            if writes:
                d = os.path.join(self.root, *mr.loop.SUPPORT_DIR.split("/"))
                os.makedirs(d, exist_ok=True)
                for f in mr.loop.SUPPORT_NEEDED:
                    open(os.path.join(d, f), "w").close()

            class R:
                returncode, stdout = rc, b"[ra_converter] --bootstrap: wrote 9 file(s)"
            return R()
        return run

    def test_missing_framework_classes_are_bootstrapped(self):
        got = mr.loop.ensure_framework(self.root, self.said.append, self.run_())
        self.assertEqual(got, "bootstrapped")
        self.assertEqual(len(self.calls), 1)
        argv = self.calls[0]
        self.assertIn("--bootstrap", argv)
        self.assertEqual(argv[argv.index("--package-root") + 1], "com.hi.api")
        self.assertNotIn("--input", argv, "no ReadyAPI XML is needed")
        self.assertTrue(any("--bootstrap" in s for s in self.said), self.said)

    def test_a_converted_copy_is_left_alone(self):
        self.run_()([])                                    # the classes exist
        self.calls.clear()
        self.assertEqual(mr.loop.ensure_framework(self.root, self.said.append, self.run_()), "")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.said, [])

    def test_a_failed_bootstrap_is_said(self):
        got = mr.loop.ensure_framework(self.root, self.said.append, self.run_(writes=False, rc=2))
        self.assertEqual(got, "failed")
        self.assertTrue(any(s.startswith("FAIL") and "exit 2" in s for s in self.said), self.said)

    def test_a_wrapper_calling_runners_scenario_steps_lost_is_removed(self):
        """Seen: support/scenario/Insights.java from an earlier convert,
        calling runVerifyAccountMember() that ScenarioSteps no longer has."""
        self.run_()([])
        d = os.path.join(self.root, *mr.loop.SUPPORT_DIR.split("/"), "scenario")
        os.makedirs(d)
        files = {
            "ScenarioSteps.java": "public abstract class ScenarioSteps<S> {\n"
                                  "    public void runVerifyKept() throws Exception {\n    }\n}\n",
            "Insights.java": "class Insights {\n  static void a() throws Exception {\n"
                             "    ScenarioSteps.current().runVerifyAccountMember();\n  }\n}\n",
            "PartnerRelationships.java": "class P {\n  static void a() throws Exception {\n"
                                         "    ScenarioSteps.current().runVerifyKept();\n  }\n}\n"}
        for name, text in files.items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
        self.assertEqual(mr.loop.ensure_framework(self.root, self.said.append, self.run_()), "")
        self.assertEqual(sorted(os.listdir(d)), ["PartnerRelationships.java", "ScenarioSteps.java"])
        self.assertTrue(any("removed" in s and "runVerifyAccountMember" in s for s in self.said), self.said)

    def test_no_converter_in_the_copy_nothing_is_tried(self):
        shutil.rmtree(os.path.join(self.root, "tools"))
        self.assertEqual(mr.loop.ensure_framework(self.root, self.said.append, self.run_()), "")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
