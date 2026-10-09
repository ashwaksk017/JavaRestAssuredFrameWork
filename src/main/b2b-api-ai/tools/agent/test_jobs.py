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
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "jobs_under_test", os.path.join(HERE, "jobs.py"))
jobs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jobs)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
