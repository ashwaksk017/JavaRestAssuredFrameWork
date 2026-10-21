"""The diagnostics record says what happened, and says no secret.

    python tools/test_diag.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("diag_under_test", os.path.join(HERE, "diag.py"))
diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diag)

TOKEN = "tok_DIAG_secret_value_12345"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="b2b-diag-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.job = os.path.join(self.root, ".workbench", "j1")
        os.makedirs(self.job)
        os.makedirs(os.path.join(self.root, "src", "main", "resources"))
        self.config({"jira_config": {"pat": TOKEN, "base_urls": ["https://jira.example.com"]},
                     "db": {"password": 'p@ss"word-1'}, "project_key": "ABC"})
        self._env = os.environ.get("WORKBENCH_LOG")
        os.environ.pop("WORKBENCH_LOG", None)
        self.addCleanup(self.restore)
        diag._STATE.update(path="", module="", seen={}, secrets=None, secrets_note="")

    def restore(self):
        os.environ.pop("WORKBENCH_LOG", None)
        if self._env is not None:
            os.environ["WORKBENCH_LOG"] = self._env
        diag._STATE.update(path="", module="", seen={}, secrets=None, secrets_note="")

    def config(self, data, raw=None):
        with io.open(os.path.join(self.root, "src", "main", "resources",
                                  "program_configuration.json"), "w", encoding="utf-8") as fh:
            fh.write(raw if raw is not None else json.dumps(data))
        diag._STATE.update(secrets=None, secrets_note="")

    def log(self, name="diagnostics.log"):
        path = os.path.join(self.job, name) if name != "server.log" else diag.server_log(self.root)
        try:
            with io.open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def run_cmd(self, main, argv=("--job", "j1", "--speed", "fast"), module="demo"):
        return diag.run(module, main, list(argv), self.root)


class TheFullRunLog(Base):
    def run_log(self):
        try:
            with io.open(os.path.join(self.root, "target", "b2b-full-run.log"), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def test_a_failed_command_is_entered_with_what_it_said(self):
        def fails():
            print("==> Compiling")
            print("verify FAIL compile")
            print(f"The JAVA_HOME environment variable is not defined correctly {TOKEN}")
            return 1
        self.run_cmd(fails, module="loop")
        text = self.run_log()
        self.assertIn("WORKBENCH FAIL [loop] --job j1 --speed fast -- exit 1", text)
        self.assertIn("verify FAIL compile", text)
        self.assertIn("JAVA_HOME environment variable", text)
        self.assertNotIn(TOKEN, text)

    def test_a_failure_said_by_a_command_that_ended_well_is_entered_too(self):
        def warns():
            print("WARN part 2 of 3 produced nothing")
            print("FAIL the Xray link could not be made")
            print("ok  done")
            return 0
        self.run_cmd(warns, module="design")
        text = self.run_log()
        self.assertIn("[design] --job j1 --speed fast -- exit 0", text)
        self.assertIn("FAIL the Xray link could not be made", text)
        self.assertNotIn("WORKBENCH FAIL [design] ok  done", text, "what went well is a STEP, never a FAIL")

    def test_a_command_that_worked_enters_nothing_and_a_crash_is_entered(self):
        self.run_cmd(lambda: 0)
        self.assertEqual(self.run_log(), "")

        def crashes():
            raise ValueError("boom")
        with self.assertRaises(ValueError):
            self.run_cmd(crashes, module="jobs")
        self.assertIn("WORKBENCH FAIL [jobs] --job j1 --speed fast -- CRASH", self.run_log())
        self.assertIn("ValueError: boom", self.run_log())

    def test_a_command_without_a_job_is_entered_and_the_server_s_errors_are(self):
        diag.run("mvnrun", lambda: 3, ["test", "--suite", "x.xml"], self.root)
        diag.request("POST", "/api/run", 500, 0.2, self.root)
        try:
            raise KeyError("missing")
        except KeyError:
            diag.server_trace("handling /api/run", self.root)
        text = self.run_log()
        self.assertIn("[mvnrun] test --suite x.xml -- exit 3", text)
        self.assertIn("[server] POST /api/run -> 500", text)
        self.assertIn("[server] ERROR handling /api/run", text)

    def test_making_tests_says_what_it_did_in_the_run_log_and_the_kept_activity_log(self):
        """Asked for: the run log said only what FAILED while tests were made."""
        def designs():
            print(" ..  asking Cursor (9412 characters)")
            print(" ok  35 test case(s) designed. Read design.md")
            print(f"     TC-001  ABC-901  a case {TOKEN}")
            return 0
        self.run_cmd(designs, module="design")
        text = self.run_log()
        self.assertIn("WORKBENCH STEP [design] START --job j1 --speed fast", text)
        self.assertIn("WORKBENCH STEP [design] END --job j1 --speed fast -- exit 0", text)
        self.assertIn("ok  35 test case(s) designed", text)
        self.assertIn("TC-001  ABC-901", text)
        self.assertNotIn("asking Cursor", text, "progress is not a step's outcome")
        self.assertNotIn(TOKEN, text)
        with io.open(diag.activity_log(self.root), encoding="utf-8") as fh:
            kept = fh.read()
        self.assertIn("[design] job j1 END", kept)
        self.assertIn("ok  35 test case(s) designed", kept)

    def test_reading_and_other_commands_are_not_activities(self):
        diag.run("xraytests", lambda: 0, ["status", "--job", "j1"], self.root)
        diag.run("history", lambda: 0, ["--job", "j1"], self.root)
        self.assertEqual(self.run_log(), "")

    def test_a_failure_says_the_steps_it_took_and_what_it_handled(self):
        def fails():
            diag.step("Reading the story")
            diag.step("Asking Cursor")
            try:
                raise TimeoutError("slow")
            except TimeoutError:
                diag.trace("asking Cursor")
            print("FAIL Cursor did not answer")
            return 1
        self.run_cmd(fails, module="loop")
        text = self.run_log()
        self.assertIn("steps it took: Reading the story > Asking Cursor", text)
        self.assertIn("errors it handled first: TimeoutError while asking Cursor at test_diag.py:", text)

    def test_the_page_checks_jobs_stay_out_of_the_run_log(self):
        os.makedirs(os.path.join(self.root, ".workbench", "check-ui-throwaway"))
        diag.run("loop", lambda: 1, ["--job", "check-ui-throwaway"], self.root)
        self.assertEqual(self.run_log(), "")

    def test_it_can_be_sent_elsewhere_or_switched_off(self):
        other = os.path.join(self.root, "elsewhere.log")
        os.environ["WORKBENCH_RUN_LOG"] = other
        self.addCleanup(os.environ.pop, "WORKBENCH_RUN_LOG", None)
        self.run_cmd(lambda: 1)
        with io.open(other, encoding="utf-8") as fh:
            self.assertIn("WORKBENCH FAIL [demo]", fh.read())
        os.environ["WORKBENCH_RUN_LOG"] = "off"
        os.remove(other)
        self.run_cmd(lambda: 1)
        self.assertFalse(os.path.exists(other))
        self.assertEqual(self.run_log(), "")


class ACommand(Base):
    def test_its_start_and_its_end_are_on_record(self):
        self.assertEqual(self.run_cmd(lambda: 0), 0)
        self.assertEqual(self.run_cmd(lambda: 2), 2)
        text = self.log()
        self.assertIn("[demo] START --job j1 --speed fast  (pid ", text)
        self.assertIn("[demo] END exit 0 after ", text)
        self.assertIn("[demo] END exit 2 after ", text)
        self.assertRegex(text.splitlines()[0], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d  \[demo\] START")

    def test_what_it_said_as_it_failed_is_kept_and_what_it_said_when_it_worked_is_not(self):
        def refuses():
            print("REFUSED the key must be given twice")
            print("  (nothing was written)")
            return 2
        self.run_cmd(refuses)
        text = self.log()
        self.assertIn("[demo] the end of what it said:", text)
        self.assertIn("    REFUSED the key must be given twice", text)

        def works():
            print("a long and happy report")
            return 0
        self.run_cmd(works)
        self.assertNotIn("happy report", self.log())
        self.assertIs(sys.stdout, sys.__stdout__ if sys.stdout is sys.__stdout__ else sys.stdout)

    def test_the_error_it_only_named_is_here_in_full(self):
        def main():
            try:
                {}["the key that is missing"]
            except Exception:                            # noqa: BLE001
                diag.trace("reading the reply")
            return 1
        self.run_cmd(main)
        text = self.log()
        self.assertIn("[demo] HANDLED KeyError while reading the reply", text)
        self.assertIn("Traceback (most recent call last)", text)
        self.assertIn("the key that is missing", text)
        self.assertIn("test_diag.py", text)

    def test_a_character_the_console_cannot_encode_does_not_end_the_command(self):
        class Narrow(io.TextIOBase):
            encoding = "cp1252"

            def write(self, text):
                text.encode("cp1252")
                return len(text)

            def flush(self):
                return None

        sys.stdout = Narrow()
        self.addCleanup(setattr, sys, "stdout", sys.__stdout__)

        def main():
            print("B2B-99771 \u2192 PASS")
            return 0

        self.assertEqual(self.run_cmd(main), 0)
        text = self.log()
        self.assertIn("[demo] END exit 0", text)
        self.assertNotIn("CRASH", text)
        self.assertNotIn("UnicodeEncodeError", text)

    def test_what_escapes_it_is_recorded_and_still_escapes(self):
        def boom():
            raise ZeroDivisionError("nine by nought")
        with self.assertRaises(ZeroDivisionError):
            self.run_cmd(boom)
        self.assertIn("[demo] CRASH after ", self.log())
        self.assertIn("nine by nought", self.log())

        def leaves():
            sys.exit(3)
        with self.assertRaises(SystemExit):
            self.run_cmd(leaves)
        self.assertIn("END exit 3", self.log())

    def test_the_same_error_two_hundred_times_is_said_once(self):
        def main():
            for _ in range(200):
                try:
                    raise TimeoutError("slow")
                except Exception:                        # noqa: BLE001
                    diag.trace("reading steps")
            return 0
        self.run_cmd(main)
        text = self.log()
        self.assertEqual(text.count("Traceback (most recent call last)"), 5)
        self.assertIn("the same error at the same place, 6 times so far", text)
        self.assertIn("50 times so far", text)
        self.assertLess(len(text.splitlines()), 60)

    def test_a_mistyped_job_does_not_become_a_job_by_being_logged(self):
        self.assertEqual(self.run_cmd(lambda: 2, argv=("--job", "nosuchjob")), 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".workbench", "nosuchjob")))
        for bad in ("../x", "", "a/b", "C:\\x"):
            self.assertEqual(diag.job_log(bad, self.root), "")
        self.assertEqual(self.run_cmd(lambda: 0, argv=("--job=j1",)), 0)
        self.assertIn("START --job=j1", self.log())

    def test_outside_a_command_nothing_is_written(self):
        try:
            raise ValueError("x")
        except Exception:                                # noqa: BLE001
            diag.trace("library use")
        diag.note("hello")
        self.assertEqual(self.log(), "")
        self.assertEqual(os.listdir(self.job), [])


class NoSecrets(Base):
    def test_the_private_configurations_values_are_not_written(self):
        def main():
            diag.note(f"asking with Authorization: Bearer {TOKEN}")
            diag.note('db said: wrong password "p@ss\\"word-1" for ABC')
            try:
                raise RuntimeError(f"401 for token {TOKEN} at https://jira.example.com/rest")
            except Exception:                            # noqa: BLE001
                diag.trace()
            return 0
        self.run_cmd(main, argv=("--job", "j1", "--text", f"note {TOKEN}"))
        text = self.log()
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("p@ss", text)
        self.assertIn("<redacted>", text)
        self.assertIn("https://jira.example.com/rest", text, "an address is not a secret")
        self.assertIn("for ABC", text, "a short ordinary value is not taken for one")

    def test_what_looks_like_a_token_is_taken_out_even_when_it_is_not_in_the_configuration(self):
        for line, gone in (("Authorization: Bearer abcDEF123456xyz", "abcDEF123456xyz"),
                           ('headers={"authorization": "Basic dXNlcjpwYXNzd29yZA=="}', "dXNlcjpwYXNz"),
                           ("api_key=sk_live_0123456789", "sk_live_0123456789"),
                           ("password: hunter2hunter2", "hunter2hunter2")):
            got = diag.redact(line, self.root)
            self.assertNotIn(gone, got, line)
            self.assertIn("<redacted>", got)
        self.assertEqual(diag.redact("the password field is empty", self.root),
                         "the password field is empty")

    def test_a_configuration_that_cannot_be_read_is_said_on_the_first_line(self):
        self.config(None, raw="{ broken")
        self.run_cmd(lambda: 0)
        self.assertIn("NOTE program_configuration.json could not be read", self.log().splitlines()[0])
        self.assertNotIn("abcDEF123456xyz", diag.redact("Bearer abcDEF123456xyz", self.root))

    def test_a_very_long_line_is_cut(self):
        self.run_cmd(lambda: diag.note("x" * 20_000) or 0)
        self.assertTrue(all(len(line) < diag.MAX_LINE + 200 for line in self.log().splitlines()))
        self.assertIn("more characters)", self.log())


class TheSwitchAndTheFile(Base):
    def test_off_writes_nothing_and_debug_writes_the_polling_too(self):
        os.environ["WORKBENCH_LOG"] = "off"
        self.run_cmd(lambda: 0)
        diag.request("POST", "/api/start", 500, 0.1, self.root)
        self.assertEqual((self.log(), self.log("server.log")), ("", ""))
        os.environ["WORKBENCH_LOG"] = "debug"
        diag.request("GET", "/api/status?job=j1&runnable=x", 200, 0.01, self.root)
        self.assertIn("/api/status", self.log("server.log"))

    def test_it_rolls_over_and_keeps_one_earlier_file(self):
        saved = diag.MAX_BYTES
        diag.MAX_BYTES = 2000
        self.addCleanup(setattr, diag, "MAX_BYTES", saved)
        for _ in range(60):
            self.run_cmd(lambda: 0)
        self.assertTrue(os.path.isfile(os.path.join(self.job, "diagnostics.log.1")))
        self.assertLess(os.path.getsize(os.path.join(self.job, "diagnostics.log")), 4000)
        self.assertEqual(sorted(os.listdir(self.job)), ["diagnostics.log", "diagnostics.log.1"])

    def test_a_log_that_cannot_be_written_fails_nothing(self):
        os.remove(diag.job_log("j1", self.root)) if os.path.exists(diag.job_log("j1", self.root)) else None
        os.makedirs(diag.job_log("j1", self.root))          # a directory where the file goes
        self.assertEqual(self.run_cmd(lambda: 7), 7)
        diag.job_note("j1", "x", self.root)
        diag.request("POST", "/x", 500, 0.1, os.path.join(self.root, "no", "such"))
        diag.server_trace("x", "\x00bad")

    def test_the_end_of_a_log_for_the_page(self):
        path = diag.job_log("j1", self.root)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(f"line {i}\n" for i in range(1000)))
        self.assertEqual(diag.tail(path, 3), "line 997\nline 998\nline 999")
        self.assertTrue(diag.tail(path, 5000, 2000).splitlines()[0].startswith("line "))
        self.assertEqual(diag.tail(os.path.join(self.root, "none.log")), "")


class TheServer(Base):
    def test_what_fails_and_what_changes_something_is_written_and_polling_is_not(self):
        for method, path, status in (("GET", "/api/status?job=j1&runnable=x", 200),
                                     ("GET", "/api/log?job=j1", 200), ("GET", "/app.js", 200),
                                     ("GET", "/api/artifact?job=j1&name=x", 200)):
            diag.request(method, path, status, 0.01, self.root)
        self.assertEqual(self.log("server.log"), "")
        diag.request("POST", "/api/start", 200, 0.02, self.root)
        diag.request("GET", "/api/status?job=../x", 400, 0.01, self.root)
        diag.request("GET", "/nothing-here", 404, 0.01, self.root)
        diag.request("POST", f"/api/x?token={TOKEN}", 200, 0.01, self.root)
        text = self.log("server.log")
        self.assertIn("POST /api/start -> 200 in 20 ms", text)
        self.assertIn("GET /api/status?job=../x -> 400", text)
        self.assertIn("/nothing-here -> 404", text)
        self.assertNotIn(TOKEN, text)

    def test_an_error_in_the_server_is_written_with_its_traceback(self):
        try:
            raise OSError(f"disk went away while holding {TOKEN}")
        except OSError:
            diag.server_trace("POST /api/spec-upload", self.root)
        text = self.log("server.log")
        self.assertIn("ERROR POST /api/spec-upload", text)
        self.assertIn("OSError: disk went away", text)
        self.assertNotIn(TOKEN, text)

    def test_the_runner_adds_what_a_failed_command_said(self):
        diag.job_note("j1", ["[jobs] publish-attach exited 2. The end of what it said:",
                             "    REFUSED the key must be given twice"], self.root)
        self.assertIn("REFUSED the key must be given twice", self.log())
        diag.job_note("nosuchjob", "x", self.root)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".workbench", "nosuchjob")))


class EveryCommandIsOnRecord(unittest.TestCase):
    """Each workbench command runs under diag.run, shares ONE diag for the
    process, and hands it the errors it handles."""

    FILES = ("agent/intake.py", "agent/locate.py", "agent/design.py", "agent/loop.py",
             "agent/codereview.py", "agent/defects.py", "agent/history.py", "agent/patches.py",
             "agent/specsource.py", "agent/manualtests.py", "agent/publish.py",
             "jira/search.py", "failure_history.py")

    def test_each_one_starts_under_it(self):
        for rel in self.FILES:
            with io.open(os.path.join(HERE, *rel.split("/")), encoding="utf-8") as fh:
                text = fh.read()
            self.assertRegex(text.rstrip().splitlines()[-1], r"diag\.run\(\"[a-z-]+\", main\)", rel)
            self.assertIn('"workbench_diag"', text, rel)
            self.assertIn("diag.trace()", text, rel)

    def test_a_broad_handler_does_not_swallow_an_error_unrecorded(self):
        import re
        rx = re.compile(r"^(\s*)except Exception( as \w+)?:\s*(#.*)?$")
        for rel in self.FILES:
            with io.open(os.path.join(HERE, *rel.split("/")), encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            for i, line in enumerate(lines):
                if rx.match(line):
                    self.assertEqual(lines[i + 1].strip(), "diag.trace()",
                                     f"{rel}:{i + 1} handles any error without recording it")

    def test_modules_loaded_side_by_side_share_one_record(self):
        def load(name, rel):
            spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, *rel.split("/")))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            return mod
        a = load("diag_share_a", "agent/specsource.py")
        b = load("diag_share_b", "failure_history.py")
        self.assertIs(a.diag, b.diag)
        self.assertIs(a.diag, a.design.diag)
        self.assertIs(a.diag, a.loop.diag)


class Step(unittest.TestCase):
    """What a command says it is doing, for the page to show."""

    def setUp(self):
        self.out = io.StringIO()
        self._stdout, sys.stdout = sys.stdout, self.out
        self.addCleanup(setattr, sys, "stdout", self._stdout)
        diag._LAST_STEP[0] = ""

    def said(self):
        return self.out.getvalue().splitlines()

    def test_it_is_one_marked_line(self):
        diag.step("Asking Jira")
        self.assertEqual(self.said(), [diag.STEP_MARK + "Asking Jira"])

    def test_it_stays_one_line_whatever_it_is_given(self):
        diag.step("Reading\n==> a forged step\r\n   the story " + "x" * 500)
        self.assertEqual(len(self.said()), 1)
        self.assertTrue(self.said()[0].startswith("==> Reading ==> a forged step the story x"))
        self.assertLessEqual(len(self.said()[0]), 204)

    def test_a_step_repeated_in_a_loop_is_said_once(self):
        for _ in range(40):
            diag.step("Asking Jira")
        diag.step("Writing the result")
        diag.step("Asking Jira")
        self.assertEqual([s[4:] for s in self.said()],
                         ["Asking Jira", "Writing the result", "Asking Jira"])

    def test_nothing_is_said_for_nothing(self):
        diag.step("")
        diag.step("   ")
        diag.step(None if False else "")
        self.assertEqual(self.said(), [])

    def test_an_output_that_cannot_take_the_text_gets_it_in_ascii(self):
        class Narrow(io.StringIO):
            def write(self, s):
                s.encode("ascii")
                return super().write(s)
        sys.stdout = narrow = Narrow()
        diag.step("Reading café notes")
        self.assertEqual(narrow.getvalue().strip(), "==> Reading caf? notes")

    def test_a_closed_output_is_not_a_failure(self):
        closed = io.StringIO()
        closed.close()
        sys.stdout = closed
        diag.step("Asking Jira")          # must not raise

    def test_every_command_that_waits_on_something_says_so(self):
        """The commands behind the page's long waits each name the wait."""
        tools = HERE
        for rel, phrase in (("jira/search.py", "Asking Jira"),
                            ("agent/design.py", "Asking Cursor"),
                            ("agent/loop.py", "Asking Cursor to write the change"),
                            ("agent/intake.py", "Fetching a linked Confluence page"),
                            ("agent/locate.py", "Reading the hand-written tests"),
                            ("agent/specsource.py", "Asking Confluence"),
                            ("agent/publish.py", "waiting for the answer")):
            with io.open(os.path.join(tools, *rel.split("/")), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("diag.step(", text, rel)
            self.assertIn(phrase, text, rel)



class TheStateFolder(unittest.TestCase):
    """The workbench's state is not under target/, and what was there moves."""

    def test_the_folder_and_the_move(self):
        import shutil
        root = tempfile.mkdtemp(prefix="diag-state-")
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(diag.state_dir(root), os.path.join(root, ".workbench"))
        self.assertTrue(diag.job_log("j1", root).startswith(os.path.join(root, ".workbench", "j1")))
        self.assertNotIn("target", diag.server_log(root))
        old = os.path.join(root, "target", "agent")
        os.makedirs(os.path.join(old, "job1"))
        with io.open(os.path.join(old, "job1", "brief.md"), "w", encoding="utf-8") as fh:
            fh.write("the brief\n")
        with io.open(os.path.join(old, "server.log"), "w", encoding="utf-8") as fh:
            fh.write("log\n")
        os.makedirs(os.path.join(root, ".workbench", "job1"))        # already there: kept, not replaced
        moved = diag.migrate_state(root)
        self.assertEqual(moved, ["server.log"])
        self.assertTrue(os.path.isfile(os.path.join(root, ".workbench", "server.log")))
        self.assertTrue(os.path.isdir(os.path.join(old, "job1")), "a job the new folder already has stays put")
        self.assertEqual(diag.migrate_state(os.path.join(root, "nowhere")), [], "no old folder: nothing to do")
        shutil.rmtree(os.path.join(root, ".workbench", "job1"))
        self.assertEqual(diag.migrate_state(root), ["job1"])
        self.assertFalse(os.path.exists(old), "the old folder is removed once empty")


if __name__ == "__main__":
    unittest.main(verbosity=1)
