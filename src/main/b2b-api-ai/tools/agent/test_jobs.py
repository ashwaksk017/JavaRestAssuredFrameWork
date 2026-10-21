"""The job runner starts only what is declared, and tells the truth after.

    python tools/agent/test_jobs.py

This module exists because the UI is a web page that starts commands.
Most of what is tested here is refusal: a form field reaches `start()`
as text, and the page is reachable by anything in the browser.
"""
from __future__ import annotations

import os as _os
_os.environ["WORKBENCH_MOCK"] = "0"      # these tests are about the real paths

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "jobs_under_test", os.path.join(HERE, "jobs.py"))
jobs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jobs)


class AChosenFileBecomesAPath(unittest.TestCase):
    """The Convert tab sends a file; the converter is given where it was put."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jobs_upload_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        saved = jobs.ROOT
        jobs.ROOT = self.root
        self.addCleanup(setattr, jobs, "ROOT", saved)

    def put(self, name, data=b"<?xml version='1.0'?><con:soapui-project/>", length=None):
        return jobs.save_upload(name, io.BytesIO(data), len(data) if length is None else length)

    def test_it_is_kept_under_target_with_its_own_name_and_forward_slashes(self):
        got = self.put("Account Member Regression.xml")
        self.assertEqual(got, {"name": "Account Member Regression.xml",
                               "path": "target/convert-input/Account Member Regression.xml",
                               "bytes": 42, "workbooks": 0})
        self.assertNotIn("\\", got["path"])
        there = os.path.join(self.root, "target", "convert-input", "Account Member Regression.xml")
        self.assertTrue(os.path.isfile(there))
        argv = jobs.build_argv("convert", {"--input": got["path"]})
        self.assertEqual(argv[argv.index("--input") + 1], os.path.realpath(there))
        self.assertEqual(jobs.list_uploads(), [got])

    def test_only_the_name_of_the_file_is_taken_from_what_was_sent(self):
        self.assertEqual(self.put("C:\\Users\\me\\Desktop\\Suite.xml")["path"],
                         "target/convert-input/Suite.xml")
        self.assertEqual(self.put("/home/me/Suite (2).xml")["name"], "Suite (2).xml")
        for bad in ("", "..xml", ".hidden.xml", "a.txt", "a.xml.exe", "a/../..", "nul.xml",
                    "COM1.xml", "a\x00.xml", "a;rm.xml", "a$b.xml", "x" * 200 + ".xml",
                    "program_configuration.json"):
            with self.assertRaises(ValueError, msg=bad):
                self.put(bad)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "target", "convert-input"))),
                         ["Suite (2).xml", "Suite.xml"], "nothing was written anywhere else")
        self.assertEqual(os.listdir(self.root), ["target"])

    def test_what_is_not_xml_or_did_not_all_arrive_is_not_kept(self):
        for name, data, length in (("zip.xml", b"PK\x03\x04 not xml", None),
                                   ("empty.xml", b"", None),
                                   ("short.xml", b"<a>", 500),
                                   ("huge.xml", b"<a/>", jobs.MAX_UPLOAD + 1)):
            with self.assertRaises(ValueError, msg=name):
                self.put(name, data, length)
        self.assertEqual(jobs.list_uploads(), [])
        d = os.path.join(self.root, "target", "convert-input")
        self.assertEqual(os.listdir(d) if os.path.isdir(d) else [], [], "no part file is left")
        self.assertEqual(self.put("bom.xml", b"\xef\xbb\xbf  \n<a/>")["bytes"], 10)

    def test_choosing_again_forgets_only_what_was_chosen(self):
        self.put("A.xml")
        self.put("B.xml")
        d = os.path.join(self.root, "target", "convert-input")
        with io.open(os.path.join(d, "notes.txt"), "w") as fh:
            fh.write("mine")
        os.makedirs(os.path.join(d, "sub.xml"))
        self.assertEqual(jobs.clear_uploads(), 2)
        self.assertEqual(sorted(os.listdir(d)), ["notes.txt", "sub.xml"])
        self.assertEqual(jobs.clear_uploads(), 0)


class ArgvIsNeverFreeText(unittest.TestCase):
    """A UI that posted a command string would be a remote shell."""

    def test_the_design_step_is_given_no_file_path_at_all(self):
        """A path confined to the repository still reaches the private
        configuration and the Cursor key file. Whatever the step is
        pointed at is sent to Cursor and copied into a log this API
        serves, so the page may not point it at anything."""
        argv = jobs.build_argv("agent-design", {"--job": "j", "--speed": "fast",
                                                "--mode": "plan", "--fresh": True})
        self.assertEqual(argv[-7:], ["--job", "j", "--speed", "fast", "--mode", "plan", "--fresh"])
        for option in ("--swagger-file", "--requirements-file", "--notes-file"):
            with self.assertRaises(ValueError):
                jobs.build_argv("agent-design", {option: os.path.join(
                    "src", "main", "resources", "program_configuration.json")})
        self.assertFalse([k for k, v in jobs.RUNNABLES["agent-design"]["options"].items()
                          if v == "path"])
        self.assertNotIn("agent-design", jobs.EXCLUSIVE,
                         "it writes no tracked or generated file")

    def test_the_intake_is_given_no_file_path_either(self):
        """What it reads is quoted into the brief Cursor is given."""
        for option in ("--text-file", "--pages-file"):
            with self.assertRaises(ValueError):
                jobs.build_argv("intake", {"--job": "j", option: os.path.join(
                    "src", "main", "resources", "program_configuration.json")})
        self.assertFalse([k for k, v in jobs.RUNNABLES["intake"]["options"].items()
                          if v == "path"])
        self.assertEqual(jobs.build_argv("intake", {"--job": "j", "--pasted": True})[-3:],
                         ["--job", "j", "--pasted"])

    def test_the_jira_and_failures_tabs_can_send_no_host_token_or_path(self):
        """What the page can send is a project key, a version, a type, a
        limit, a query, a label, a signature id and a note."""
        tabs = [r for r in jobs.RUNNABLES if r.startswith(("jira-", "failures-", "defects-"))]
        self.assertEqual(sorted(tabs), ["defects-apply", "defects-load", "defects-suggest",
                                        "failures-list", "failures-note", "failures-record",
                                        "failures-show", "jira-paste", "jira-release",
                                        "jira-tests", "jira-verify", "jira-versions"])
        for name in tabs:
            options = jobs.RUNNABLES[name]["options"]
            self.assertFalse([k for k, v in options.items() if v == "path"], name)
            self.assertIn("--job", options, name)
            self.assertNotIn(name, jobs.EXCLUSIVE, name)
            for bad in ("--base", "--url", "--host", "--token", "--pat", "--digest",
                        "--config", "--name", "--field", "--write-back"):
                with self.assertRaises(ValueError, msg=(name, bad)):
                    jobs.build_argv(name, {bad: "x"})

    def test_what_is_typed_reaches_the_command_as_one_argument_never_a_shell_line(self):
        text = 'project = ABC AND summary ~ "a b" ; rm -rf / && echo $(whoami) `id`'
        argv = jobs.build_argv("jira-paste", {"--job": "j", "--text": text})
        self.assertEqual(argv[-2:], ["--text", text])
        argv = jobs.build_argv("failures-note", {"--job": "j", "--id": "0123456789",
                                                 "--text": "it's \"quoted\" & <b>bold</b>"})
        self.assertEqual(argv[-1], "it's \"quoted\" & <b>bold</b>")

    def test_a_value_that_begins_with_a_dash_is_still_a_value(self):
        """`--text -flaky` reads to argparse as an option called -flaky and
        a --text with nothing after it."""
        argv = jobs.build_argv("failures-note", {"--id": "0123456789", "--text": "-flaky on CI"})
        self.assertEqual(argv[-1], "--text=-flaky on CI")
        argv = jobs.build_argv("jira-paste", {"--text": "--max"})
        self.assertEqual(argv[-1], "--text=--max")
        self.assertNotIn("--max", argv)
        argv = jobs.build_argv("jira-release", {"--project": "ABC", "--version": "6.02"})
        self.assertEqual(argv[-4:], ["--project", "ABC", "--version", "6.02"],
                         "an ordinary value is passed as before")

    def test_the_last_commands_result_is_gone_before_the_next_command_starts(self):
        root = tempfile.mkdtemp(prefix="jobstest_res_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        orig = jobs.job_dir
        jobs.job_dir = lambda job: os.path.join(root, job)
        self.addCleanup(setattr, jobs, "job_dir", orig)
        os.makedirs(os.path.join(root, "j"))
        for name in ("jira-result.json", "failures-result.json", "plan.md"):
            with io.open(os.path.join(root, "j", name), "w") as fh:
                fh.write("{}")
        jobs.clear_result("j", "jira-tests")
        self.assertEqual(sorted(os.listdir(os.path.join(root, "j"))),
                         ["failures-result.json", "plan.md"])
        jobs.clear_result("j", "failures-note")
        self.assertEqual(os.listdir(os.path.join(root, "j")), ["plan.md"])
        jobs.clear_result("j", "convert")              # nothing of its own to clear
        jobs.clear_result("never-made", "jira-verify")  # and no error when there is none
        self.assertEqual(os.listdir(os.path.join(root, "j")), ["plan.md"])

    def test_the_two_result_files_can_be_read_back(self):
        for name in ("jira-result.json", "failures-result.json"):
            self.assertEqual(jobs.read_artifact("no-such-job-here", name), "")

    def test_design_files_can_be_read_back_and_nothing_else_new(self):
        for name in ("design.md", "design.json", "test-cases.csv", "xray.csv"):
            self.assertEqual(jobs.read_artifact("no-such-job-here", name), "")
        for name in ("design-swagger.txt", "cursor.log", "../design.md"):
            with self.assertRaises(ValueError):
                jobs.read_artifact("no-such-job-here", name)

    def test_a_declared_option_is_accepted(self):
        argv = jobs.build_argv("convert", {"--classic": True,
                                           "--suite-name": "goal"})
        self.assertIn("--classic", argv)
        self.assertIn("goal", argv)

    def test_a_convert_is_written_into_this_project_unless_told_otherwise(self):
        """The page no longer shows --output. The converter's own default is
        a directory called `output`; this project is converted into itself."""
        argv = jobs.build_argv("convert", {"--suite-name": "goal"})
        self.assertEqual(argv[argv.index("--output") + 1], os.path.realpath(jobs.ROOT))
        argv = jobs.build_argv("convert", {"--output": "target/elsewhere"})
        self.assertEqual(argv[argv.index("--output") + 1],
                         os.path.realpath(os.path.join(jobs.ROOT, "target", "elsewhere")))
        self.assertEqual(argv.count("--output"), 1)

    def test_several_inputs_are_each_kept_inside_the_project(self):
        argv = jobs.build_argv("convert", {
            "--input": "target/convert-input/A.xml, target/convert-input/B b.xml"})
        got = argv[argv.index("--input") + 1].split(",")
        base = os.path.realpath(os.path.join(jobs.ROOT, "target", "convert-input"))
        self.assertEqual(got, [os.path.join(base, "A.xml"), os.path.join(base, "B b.xml")])
        with self.assertRaises(ValueError):
            jobs.build_argv("convert", {"--input": "target/convert-input/A.xml,../../x.xml"})

    def test_the_gate_can_be_run_and_takes_only_an_area_from_the_page(self):
        self.assertEqual(jobs.RUNNABLES["verify-all"]["options"], {"--area": "text"})
        self.assertEqual(jobs.build_argv("verify-all", {"--area": "converter"})[-2:],
                         ["--area", "converter"])
        self.assertEqual(jobs.build_argv("verify-all", {})[-1],
                         os.path.join("tools", "verify_all.py"))
        with self.assertRaises(ValueError):
            jobs.build_argv("verify-all", {"--baseline": True})
        self.assertIn("verify-all", jobs.EXCLUSIVE, "it judges the tree a convert rewrites")

    def test_an_undeclared_option_is_refused(self):
        with self.assertRaises(ValueError) as got:
            jobs.build_argv("convert", {"--rm": "-rf"})
        self.assertIn("does not accept", str(got.exception))

    def test_an_unknown_runnable_is_refused(self):
        with self.assertRaises(ValueError):
            jobs.build_argv("sh", {})

    def test_a_false_flag_contributes_nothing(self):
        self.assertNotIn("--clean", jobs.build_argv("convert",
                                                    {"--clean": False}))

    def test_a_repeatable_option_takes_a_list(self):
        argv = jobs.build_argv("intake", {"--link": ["a", "b"]})
        self.assertEqual(argv.count("--link"), 2)


class PathsAreConfined(unittest.TestCase):
    """`--output ../../..` would otherwise write wherever this runs."""

    def test_traversal_is_refused(self):
        for bad in ("../../../tmp/pwn", "..", "../"):
            with self.assertRaises(ValueError, msg=bad):
                jobs.confine(bad)

    def test_an_absolute_path_outside_the_repo_is_refused(self):
        with self.assertRaises(ValueError):
            jobs.confine(tempfile.gettempdir())

    def test_a_path_inside_the_repo_is_allowed(self):
        self.assertTrue(jobs.confine("tools"))

    def test_the_repo_root_itself_is_allowed(self):
        self.assertTrue(jobs.confine("."))

    def test_an_empty_path_is_refused(self):
        with self.assertRaises(ValueError):
            jobs.confine("   ")


class LogReading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._jd = jobs.job_dir
        self.addCleanup(setattr, jobs, "job_dir", self._jd)
        jobs.job_dir = lambda job: self.tmp

    def _write(self, data: bytes):
        with io.open(jobs.log_path("j", "convert"), "wb") as fh:
            fh.write(data)

    def test_an_offset_reads_only_what_is_new(self):
        self._write(b"one\n")
        first = jobs.read_log("j", "convert", 0)
        self._write(b"one\ntwo\n")
        second = jobs.read_log("j", "convert", first["offset"])
        self.assertEqual(second["text"], "two\n")

    def test_a_truncated_log_restarts_the_read(self):
        """A re-run truncates the log. A client holding the previous
        run's offset asks past the end and is handed nothing -- it
        watches an empty pane and concludes nothing is happening."""
        self._write(b"a long first run\n" * 20)
        stale = jobs.read_log("j", "convert", 0)["offset"]
        self._write(b"second run\n")
        again = jobs.read_log("j", "convert", stale)
        self.assertTrue(again["restarted"])
        self.assertEqual(again["text"], "second run\n")

    def test_a_missing_log_is_empty_not_an_error(self):
        # a real runnable that has not run for this job
        r = jobs.read_log("j", "audit-token-chain", 0)
        self.assertEqual(r["text"], "")
        self.assertFalse(r["restarted"])

    def test_the_cursor_log_is_readable_like_a_run_log(self):
        """cursor.log is not a runnable's output, but tab 3 follows it."""
        r = jobs.read_log("j", "cursor", 0)
        self.assertEqual(r["text"], "")

    def test_a_name_that_is_not_declared_cannot_pick_a_file(self):
        """The name becomes part of a file name and comes from a query
        string: `../other-job/convert` must not read another job's log."""
        for bad in ("nothing-ran", "../j2/convert", "..\\j2\\convert", ""):
            with self.assertRaises(ValueError):
                jobs.read_log("j", bad, 0)
            with self.assertRaises(ValueError):
                jobs.read_status("j", bad)


class TheJobIsTheJob(unittest.TestCase):
    """One request must not be logged as one job and run against another."""

    def test_an_option_cannot_be_repeated(self):
        with self.assertRaises(ValueError):
            jobs.build_argv("agent-approve", {"--job": ["scratch", "victim"]})
        argv = jobs.build_argv("intake", {"--link": ["a", "b"]})
        self.assertEqual(argv.count("--link"), 2, "--link is the one repeatable option")

    def test_confirm_must_name_the_job_in_the_request(self):
        with self.assertRaises(ValueError):
            jobs.start("scratch", "agent-approve", {"--job": "victim", "--confirm": "victim"})


class OneWriterAtATime(unittest.TestCase):
    """A convert during an agent run would look like the agent rewriting
    every suite; the page must not be able to start both."""

    class Live:
        def poll(self):
            return None

    def tearDown(self):
        jobs._RUNNING.clear()

    def test_an_agent_run_blocks_a_convert_and_the_other_way_round(self):
        jobs._RUNNING[("job1", "agent-generate")] = self.Live()
        with self.assertRaises(RuntimeError) as got:
            jobs.start("job2", "convert", {})
        self.assertIn("only one of convert", str(got.exception))
        jobs._RUNNING.clear()
        jobs._RUNNING[("job1", "convert")] = self.Live()
        with self.assertRaises(RuntimeError):
            jobs.start("job2", "agent-approve", {"--job": "job2", "--confirm": "job2"})

    def test_reading_and_planning_are_not_blocked(self):
        self.assertNotIn("intake", jobs.EXCLUSIVE)
        self.assertNotIn("locate", jobs.EXCLUSIVE)
        self.assertNotIn("agent-setup", jobs.EXCLUSIVE)


class StatusTellsTheTruth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._jd = jobs.job_dir
        self.addCleanup(setattr, jobs, "job_dir", self._jd)
        jobs.job_dir = lambda job: self.tmp
        jobs.RUNNABLES["_broken"] = {
            "argv": ["definitely-not-a-program-anywhere"],
            "label": "cannot start", "options": {}}
        self.addCleanup(jobs.RUNNABLES.pop, "_broken", None)

    def test_a_spawn_that_fails_is_not_left_running(self):
        """The status file said `running` for a process that never
        existed, and the page polled it forever."""
        with self.assertRaises(Exception):
            jobs.start("j", "_broken", {})
        st = jobs.read_status("j", "_broken")
        self.assertEqual(st["state"], "failed")
        self.assertIn("error", st)

    def test_an_unknown_runnable_fails_before_any_directory_is_made(self):
        with self.assertRaises(ValueError):
            jobs.start("j", "nope", {})


class Artifacts(unittest.TestCase):
    def test_names_are_checked_against_a_list_not_joined(self):
        with self.assertRaises(ValueError):
            jobs.read_artifact("demo", "../../../../etc/passwd")

    def test_a_known_name_is_allowed(self):
        jobs.read_artifact("demo", "brief.md")   # absent file -> ""



class AMigrationRequest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jobs_mig_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for owner in (jobs, jobs.intake):
            self.addCleanup(setattr, owner, "ROOT", owner.ROOT)
            owner.ROOT = self.root
        os.makedirs(os.path.join(self.root, "src", "main", "java", "com", "hi", "api", "support", "amex"))
        self.started = []
        self.addCleanup(setattr, jobs, "start", jobs.start)
        jobs.start = lambda job, runnable, options: (self.started.append((runnable, options)), {"started": True})[1]
        self.job = jobs.job_dir("m1")

    def read(self, name):
        with io.open(os.path.join(self.job, name), encoding="utf-8") as fh:
            return fh.read()

    def test_a_first_request_becomes_the_brief_and_plan_and_starts_a_run(self):
        r = jobs.migration_ask("m1", "converted", "amex", "Use the employee's token in getSpend403.")
        self.assertEqual(r["runnable"], "agent-generate")
        self.assertEqual(self.started, [("agent-generate", {"--job": "m1", "--scope": "converted", "--suite": "amex"})])
        self.assertIn("Use the employee's token in getSpend403.", self.read("brief.md"))
        self.assertIn("support/amex/", self.read("plan.md"))
        self.assertTrue(self.read("plan.md").startswith("# Plan: migration change (converted)"))
        self.assertEqual(jobs.converted_suites(), ["amex"])

    def test_a_request_while_a_change_waits_is_added_to_it(self):
        os.makedirs(self.job)
        with io.open(os.path.join(self.job, "review.json"), "w", encoding="utf-8") as fh:
            fh.write('{"state": "pending-review", "scope": "converter"}')
        with self.assertRaises(ValueError) as got:
            jobs.migration_ask("m1", "converted", "amex", "Something else.")
        self.assertIn("the change waiting for this job is for converter", str(got.exception))
        self.assertFalse(os.path.exists(os.path.join(self.job, "agent-followup.txt")), "nothing written")
        r = jobs.migration_ask("m1", "converter", "", "Also handle the JDBC step.")
        self.assertEqual(r["runnable"], "agent-followup")
        self.assertEqual(self.read("agent-followup.txt"), "Also handle the JDBC step.")

    def test_nothing_is_written_when_it_is_refused(self):
        os.makedirs(self.job)
        with io.open(os.path.join(self.job, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("# Plan: fix the converted Java of suite amex\n\nthe brief\n")
        with self.assertRaises(ValueError) as got:
            jobs.migration_ask("m1", "converted", "amex", "Something of mine.")
        self.assertIn("leave the box empty", str(got.exception))
        self.assertTrue(self.read("plan.md").startswith("# Plan: fix"), "the fix brief is kept")
        with io.open(os.path.join(self.job, "review.json"), "w", encoding="utf-8") as fh:
            fh.write('{"state": "generating"}')
        with self.assertRaises(ValueError) as got:
            jobs.migration_ask("m1", "converter", "", "x")
        self.assertIn("is running for this job", str(got.exception))
        self.assertEqual(self.started, [])

    def test_the_empty_box_run_is_checked_against_the_plan(self):
        os.makedirs(self.job)
        with io.open(os.path.join(self.job, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("# Plan: fix the converted Java of suite amex\n")
        self.assertEqual(jobs.migration_refusal("m1", "converted", "amex"), "")
        self.assertIn("not converter", jobs.migration_refusal("m1", "converter", ""))
        self.assertIn("not converted suite goal", jobs.migration_refusal("m1", "converted", "goal"))
        with io.open(os.path.join(self.job, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("# Plan: B2B story\n")
        self.assertIn("not a migration request", jobs.migration_refusal("m1", "converter", ""))

    def test_what_is_refused(self):
        for scope, suite, text, why in (("new-test", "", "x", "choose what Cursor may change"),
                                        ("converted", "", "x", "name the converted suite"),
                                        ("converted", "goal", "x", "not converted on this machine"),
                                        ("converter", "", "  ", "write what Cursor should change")):
            with self.assertRaises(ValueError) as got:
                jobs.migration_ask("m1", scope, suite, text)
            self.assertIn(why, str(got.exception))
        os.makedirs(self.job, exist_ok=True)
        with io.open(os.path.join(self.job, "design.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with self.assertRaises(ValueError) as got:
            jobs.migration_ask("m1", "converter", "", "x")
        self.assertIn("its own job", str(got.exception))
        self.assertEqual(self.started, [])


class ChangingTheWorkbench(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jobs_wb_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for owner in (jobs, jobs.intake):
            self.addCleanup(setattr, owner, "ROOT", owner.ROOT)
            owner.ROOT = self.root
        self.addCleanup(jobs._RUNNING.clear)
        self.started = []
        self.addCleanup(setattr, jobs, "start", jobs.start)
        real_start = jobs.start
        jobs.start = lambda job, runnable, options: (self.started.append((job, runnable, options)),
                                                     {"started": True})[1]
        self.real_start = real_start

    def test_a_request_starts_a_change_in_a_workbench_job_of_its_own(self):
        r = jobs.workbench_ask("Show the count beside the button.")
        self.assertTrue(r["job"].startswith("workbench-"))
        self.assertEqual(self.started, [(r["job"], "agent-generate", {"--job": r["job"], "--scope": "workbench"})])
        with io.open(os.path.join(jobs.job_dir(r["job"]), "plan.md"), encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("# Plan: workbench change"))

    def test_a_request_while_its_change_waits_is_added_to_it(self):
        job = "workbench-20261015-120000"
        os.makedirs(jobs.job_dir(job))
        with io.open(os.path.join(jobs.job_dir(job), "review.json"), "w", encoding="utf-8") as fh:
            fh.write('{"state": "pending-review"}')
        r = jobs.workbench_ask("Also wrap the label.")
        self.assertEqual((r["job"], r["runnable"]), (job, "agent-followup"))

    def test_an_unfinished_workbench_run_is_refused_only_when_changing_the_workbench(self):
        stuck = "workbench-20261016-151909"
        os.makedirs(jobs.job_dir(stuck))
        with io.open(os.path.join(jobs.job_dir(stuck), "review.json"), "w", encoding="utf-8") as fh:
            fh.write('{"state": "generating", "scope": "workbench"}')
        # No snapshot and no git repo here: the status cannot be read, so
        # the run stays unfinished. A commit that clears it is the next test.
        with self.assertRaises(ValueError) as got:
            jobs.workbench_ask("Fix the button.")
        self.assertIn("a change to the workbench did not finish", str(got.exception))
        self.assertIn(stuck, str(got.exception))
        cur = jobs.workbench_job()
        self.assertNotEqual(cur.get("state"), "generating",
                            "a stuck run does not bind the workbench session")
        self.assertEqual(cur.get("unfinished"), stuck)

    def _git(self, *args):
        r = subprocess.run(["git", *args], cwd=self.root, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace"))
        return r.stdout.decode("utf-8", "replace")

    def test_a_commit_from_the_command_line_clears_a_generating_workbench_run(self):
        # The run's review still says generating. git status is what Ask
        # Cursor reads: uncommitted edits keep the refusal; a commit of
        # those edits clears it, and Discard is not left on offer.
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")
        readme = os.path.join(self.root, "README.md")
        with io.open(readme, "w", encoding="utf-8") as fh:
            fh.write("before\n")
        self._git("add", "README.md")
        self._git("commit", "-q", "-m", "base")
        stuck = "workbench-20261019-111620"
        os.makedirs(jobs.job_dir(stuck))
        with io.open(os.path.join(jobs.job_dir(stuck), "review.json"), "w", encoding="utf-8") as fh:
            fh.write('{"state": "generating", "scope": "workbench", "base_commit": "x"}')
        with io.open(os.path.join(jobs.job_dir(stuck), "pre-state.json"), "w", encoding="utf-8") as fh:
            fh.write('{"before": {}}')
        with io.open(os.path.join(jobs.job_dir(stuck), "agent-generate.status.json"), "w",
                     encoding="utf-8") as fh:
            fh.write('{"state": "running", "runnable": "agent-generate"}')
        with io.open(readme, "w", encoding="utf-8") as fh:
            fh.write("the run wrote this\n")
        with self.assertRaises(ValueError) as got:
            jobs.workbench_ask("While it is still uncommitted.")
        self.assertIn(stuck, str(got.exception))
        with io.open(os.path.join(jobs.job_dir(stuck), "review.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["state"], "generating")
        self._git("add", "README.md")
        self._git("commit", "-q", "-m", "committed from the command line")
        r = jobs.workbench_ask("Show a clear-logs button.")
        self.assertTrue(r["job"].startswith("workbench-"))
        self.assertNotEqual(r["job"], stuck)
        with io.open(os.path.join(jobs.job_dir(stuck), "review.json"), encoding="utf-8") as fh:
            review = json.load(fh)
        self.assertEqual(review["state"], "committed-outside")
        self.assertFalse(review.get("needs_discard"))
        self.assertIn("git status", review["reason"])
        with io.open(os.path.join(jobs.job_dir(stuck), "agent-generate.status.json"),
                     encoding="utf-8") as fh:
            status = json.load(fh)
        self.assertEqual(status["state"], "failed", "a dead run must not stay running")
        self.assertEqual(jobs.workbench_job().get("unfinished"), "")

    def test_uncommitted_edits_still_block_and_a_live_run_is_not_settled(self):
        stuck = "workbench-20261019-111620"
        os.makedirs(jobs.job_dir(stuck))
        with io.open(os.path.join(jobs.job_dir(stuck), "review.json"), "w", encoding="utf-8") as fh:
            fh.write('{"state": "generating", "scope": "workbench"}')
        self.addCleanup(setattr, jobs, "_git_left", jobs._git_left)
        jobs._git_left = lambda _job: ["tools/agent/ui/app.js"]
        with self.assertRaises(ValueError) as got:
            jobs.workbench_ask("Again.")
        self.assertIn(stuck, str(got.exception))
        with io.open(os.path.join(jobs.job_dir(stuck), "review.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["state"], "generating")

        class Alive:
            def poll(self):
                return None
        jobs._RUNNING[(stuck, "agent-generate")] = Alive()
        jobs._git_left = lambda _job: []
        self.assertEqual(jobs.workbench_unfinished(), stuck)
        with io.open(os.path.join(jobs.job_dir(stuck), "review.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["state"], "generating",
                             "a run that is still going is not called committed")

    def test_nothing_else_may_run_and_nothing_starts_while_it_runs(self):
        class Alive:
            def poll(self):
                return None
        jobs._RUNNING[("job-1", "convert")] = Alive()
        with self.assertRaises(ValueError) as got:
            jobs.workbench_ask("x")
        self.assertIn("only while nothing else runs", str(got.exception))
        jobs._RUNNING.clear()
        jobs.start = self.real_start
        jobs._RUNNING[("workbench-20261015-130000", "agent-generate")] = Alive()
        with self.assertRaises(RuntimeError) as got:
            jobs.start("job-2", "intake", {"--job": "job-2"})
        self.assertIn("changing the workbench itself", str(got.exception))

    def test_the_workbench_scope_only_in_a_workbench_job_and_only_it_there(self):
        jobs.start = self.real_start
        with self.assertRaises(ValueError):
            jobs.start("job-3", "agent-generate", {"--job": "job-3", "--scope": "workbench"})
        with self.assertRaises(ValueError):
            jobs.start("workbench-20261015-140000", "agent-generate",
                       {"--job": "workbench-20261015-140000", "--scope": "new-test"})


class WorkbookSteps(unittest.TestCase):
    def test_only_a_datasource_that_reads_a_file_counts(self):
        d = tempfile.mkdtemp(prefix="jobs_wb_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "s.xml")
        xml = ('<con:testStep type="restrequest" name="a"><con:file>x.xlsx</con:file></con:testStep>'
               '<con:testStep type="datasource" name="b"><con:config><con:dataSource type="Grid">'
               '</con:dataSource></con:config></con:testStep>'
               '<con:testStep type="datasource" name="c"><con:config><configuration>'
               '<file>C:/data/members.xlsx</file><worksheet>S1</worksheet></configuration>'
               '</con:config></con:testStep>')
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write(xml)
        self.assertEqual(jobs.workbook_steps(p), 1, "a grid DataSource and another step's file do not count")
        self.assertEqual(jobs.workbook_steps(os.path.join(d, "none.xml")), 0)


class ClearingADesign(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jobs_clear_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for owner in (jobs, jobs.intake):
            self.addCleanup(setattr, owner, "ROOT", owner.ROOT)
            owner.ROOT = self.root
        self.job = jobs.job_dir("j1")
        os.makedirs(os.path.join(self.job, "design-cache"))

    def put(self, name, text="x"):
        with io.open(os.path.join(self.job, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_the_design_and_its_kept_replies_go_and_the_review_stays(self):
        import json
        self.put("design.json", json.dumps({"test_cases": [{"id": "TC-001"}]}))
        for n in ("design.md", "test-cases.csv", "agent-design.log", "case-review.json", "xray-tests.json",
                  "design-swagger.txt", "brief.md"):
            self.put(n)
        self.put("design-cache/abc.json")
        got = jobs.clear_design("j1")
        self.assertTrue(got["cleared"])
        self.assertIn("design-cache/", got["moved"])
        for n in ("design.json", "design.md", "test-cases.csv", "agent-design.log", "design-cache"):
            self.assertFalse(os.path.exists(os.path.join(self.job, n)), n)
            self.assertTrue(os.path.exists(os.path.join(self.job, got["archive"], n)), n)
        for n in ("case-review.json", "xray-tests.json", "design-swagger.txt", "brief.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.job, n)), f"{n} stays")
        with io.open(os.path.join(self.job, "design.previous.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["test_cases"][0]["id"], "TC-001", "the next design is matched to it")
        self.assertEqual(jobs.clear_design("j1"), {"cleared": False, "moved": []})


class RefiningAndSources(unittest.TestCase):
    """Refine one case, some, or the whole list; and the page is told what
    the cases were designed from."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jobs_refine_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for owner in (jobs, jobs.intake):
            self.addCleanup(setattr, owner, "ROOT", owner.ROOT)
            owner.ROOT = self.root
        self.job = jobs.job_dir("j1")
        os.makedirs(self.job)
        with io.open(os.path.join(self.job, "design.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_cases": [{"id": f"TC-00{n}", "title": f"t{n}", "endpoint": "GET /a",
                                       "steps": [{"action": "a", "expected": "200"}]} for n in (1, 2, 3)]}, fh)

    def test_chosen_cases_are_marked_and_the_request_says_only_those(self):
        listing = jobs.refine_listing("j1", "split it", ["TC-002"])
        self.assertIn("TC-002 [REFINE]", listing)
        self.assertNotIn("TC-001 [REFINE]", listing)
        self.assertIn("ONLY the cases marked [REFINE] (TC-002)", jobs.refine_scope("split it", ["TC-002"]))
        self.assertEqual(jobs.refine_scope("split it", []), "split it")
        self.assertNotIn("[REFINE]", jobs.refine_listing("j1", "all of them"))

    def test_a_case_that_is_not_there_is_refused(self):
        with self.assertRaises(ValueError):
            jobs.refine_listing("j1", "x", ["TC-009"])

    def test_the_request_cap_is_the_callers(self):
        with self.assertRaises(ValueError):
            jobs.refine_listing("j1", "x" * 4001)
        self.assertIn("TC-001", jobs.refine_listing("j1", "x" * 8000, cap=12000))

    def test_sources_name_the_stories_and_pages_without_their_text(self):
        with io.open(os.path.join(self.job, "intake.json"), "w", encoding="utf-8") as fh:
            json.dump({"at": "2026-10-20T10:00:00Z", "story": {"key": "ABC-1", "summary": "s", "parents": ["ABC-0"],
                                                              "acceptance_criteria": {"present": True}, "chars": 900},
                       "story_text": "SECRET STORY TEXT",
                       "around": [{"key": "ABC-2", "how": "is blocked by", "ok": True, "text": "x" * 50},
                                  {"key": "ABC-3", "how": "relates to", "ok": False, "reason": "403"}],
                       "pages": [{"link": "https://wiki/x", "text": "y" * 10, "cut": 0}],
                       "links": [{"link": "nope", "ok": False, "reason": "not a link"}]}, fh)
        s = jobs.sources("j1")
        self.assertEqual(s["story"]["key"], "ABC-1")
        self.assertTrue(s["story"]["acceptance_criteria"])
        self.assertEqual([(a["key"], a["ok"], a["chars"]) for a in s["around"]],
                         [("ABC-2", True, 50), ("ABC-3", False, 0)])
        self.assertEqual(s["pages"][0]["chars"], 10)
        self.assertEqual(s["failed"][0]["reason"], "not a link")
        self.assertEqual(s["design"]["cases"], 3)
        self.assertNotIn("SECRET STORY TEXT", json.dumps(s))

    def test_new_results_are_readable_and_cleared_before_a_run(self):
        for name in ("devcode.json", "devcode.md", "suite-suggest.json", "mvnrun-result.json"):
            with io.open(os.path.join(self.job, name), "w", encoding="utf-8") as fh:
                fh.write("{}")
            self.assertEqual(jobs.read_artifact("j1", name), "{}")
        jobs.clear_result("j1", "devcode-check")
        self.assertFalse(os.path.exists(os.path.join(self.job, "devcode.json")))
        self.assertFalse(os.path.exists(os.path.join(self.job, "devcode.md")))
        jobs.clear_result("j1", "mvn-test")
        self.assertFalse(os.path.exists(os.path.join(self.job, "mvnrun-result.json")))
        self.assertIn("mvn-test", jobs.EXCLUSIVE)


class PuttingAnOutcomeAway(unittest.TestCase):
    """The agent loop's last outcome can be dismissed from the page when it
    left nothing in the working tree."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="jobs_dismiss_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # job_dir resolves through intake's root: both are pointed away.
        for owner in (jobs, jobs.intake):
            self.addCleanup(setattr, owner, "ROOT", owner.ROOT)
            owner.ROOT = self.root
        self.job = jobs.job_dir("j1")
        self.assertTrue(self.job.startswith(self.root), "the test stays in its scratch folder")
        os.makedirs(self.job)

    def review(self, **data):
        import json
        with io.open(os.path.join(self.job, "review.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def read(self):
        import json
        with io.open(os.path.join(self.job, "review.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_rejection_that_was_undone_is_put_away_and_kept(self):
        import json
        self.review(state="rejected", why="timeout", reason="did not finish", undone=True)
        for name in ("cursor.log", "csv-xray.log", "agent-generate.log", "agent-generate.status.json",
                     "agent-prompt-1.txt", "agent-answer-1.md", "proposed.diff", "code-review.json",
                     "code-review.md", "intake.json", "design.json", "brief.md"):
            with io.open(os.path.join(self.job, name), "w", encoding="utf-8") as fh:
                fh.write("x")
        got = jobs.dismiss_review("j1")
        self.assertEqual((got["dismissed"], got["was"]), (True, "rejected"))
        self.assertEqual(got["moved"], ["agent-answer-1.md", "agent-generate.log", "agent-generate.status.json",
                                        "agent-prompt-1.txt", "code-review.json", "code-review.md", "csv-xray.log",
                                        "cursor.log", "proposed.diff"])
        archive = os.path.join(self.job, got["archive"])
        self.assertTrue(os.path.isfile(os.path.join(archive, "cursor.log")))
        self.assertTrue(os.path.isfile(os.path.join(archive, "review.json")), "the outcome itself, too")
        for name in ("cursor.log", "agent-generate.log", "proposed.diff"):
            self.assertFalse(os.path.exists(os.path.join(self.job, name)), name)
        for name in ("intake.json", "design.json", "brief.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.job, name)), f"{name} is the job's, not the run's")
        self.assertEqual(self.read()["state"], "none")
        with io.open(os.path.join(self.job, "review-dismissed.json"), encoding="utf-8") as fh:
            kept = json.load(fh)
        self.assertEqual((kept["state"], kept["why"]), ("rejected", "timeout"))
        self.assertTrue(kept["dismissed_at"])
        self.assertEqual(jobs.dismiss_review("j1"), {"dismissed": False, "state": "none"})

    def test_an_outcome_with_files_in_the_tree_is_refused(self):
        for state, extra in (("pending-review", {}), ("verify-failed", {}), ("generating", {}),
                             ("rejected", {"needs_discard": True})):
            self.review(state=state, **extra)
            with self.assertRaises(ValueError, msg=state) as got:
                jobs.dismiss_review("j1")
            self.assertIn("Use Discard", str(got.exception))
            self.assertEqual(self.read()["state"], state, "nothing changed")
        for state in ("discarded", "pushed", "approved-local"):
            self.review(state=state)
            self.assertTrue(jobs.dismiss_review("j1")["dismissed"], state)

    def test_no_review_at_all_is_nothing_to_dismiss(self):
        self.assertEqual(jobs.dismiss_review("j1"), {"dismissed": False, "state": "none"})
        with self.assertRaises(ValueError):
            jobs.dismiss_review("../x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
