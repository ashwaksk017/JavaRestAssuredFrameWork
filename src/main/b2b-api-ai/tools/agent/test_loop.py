"""The agent loop keeps the agent inside its paths and pushes only what a
person approved.

    python tools/agent/test_loop.py

Every test runs against a throwaway git repository with a FAKE agent: a
function that edits files the way Cursor might. No network, no key, no
cursor-sdk. What is tested is the part that must hold whatever the agent
does -- the checks made on the working tree after it returns.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "loop_under_test", os.path.join(HERE, "loop.py"))
loop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loop)

JIRA = "src/test/java/com/hi/api/tests/jira/"
GEN = "src/main/java/com/hi/api/support/"
IMPORTED = "src/test/java/com/hi/api/tests/imported/"
CSV = "src/test/resources/csv/"
OK = lambda: {"ok": True, "steps": [{"name": "compile", "rc": 0, "tail": ""}]}


def sh(root, *args):
    r = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    assert r.returncode == 0, r.stdout.decode()
    return r.stdout.decode().strip()


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


class Repo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="looptest_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        sh(self.root, "init", "-q", "-b", "main")
        sh(self.root, "config", "user.email", "t@example.com")
        sh(self.root, "config", "user.name", "t")
        sh(self.root, "config", "core.autocrlf", "false")
        write(self.root, ".gitignore", "target/\n" + GEN + "\n" + IMPORTED + "\n"
              + CSV + "*\n!" + CSV + "manual/\n")
        write(self.root, "README.md", "hello\n")
        write(self.root, JIRA + "ExistingTest.java", "class ExistingTest {}\n")
        write(self.root, "src/main/java/Framework.java", "class Framework {}\n")
        write(self.root, GEN + "goal/Hooks.java", "class Hooks {}\n")   # ignored
        write(self.root, GEN + "amex/Hooks.java", "class Hooks { int amex; }\n")
        write(self.root, IMPORTED + "amex/AmexTest.java", "class AmexTest {}\n")
        write(self.root, CSV + "amex/a.csv", "h\n1\n")
        write(self.root, "tools/ra_converter/ra_converter.py", "X = 1\n")
        sh(self.root, "add", "-A")
        sh(self.root, "commit", "-q", "-m", "base")
        self.job = os.path.join(self.root, "target", "agent", "job1")
        os.makedirs(self.job)
        write(self.root, "target/agent/job1/plan.md", "# plan\ncreate POST /x\n")
        write(self.root, "target/agent/job1/brief.md", "# brief\nPOST /x expect 201\n")
        self.policy = loop.load_policy(os.path.join(self.root, "no-policy.json"))

    def generate(self, agent, verifier=OK, scope="new-test", suite=""):
        return loop.cmd_generate(self.job, self.root, self.policy, scope, suite,
                                 agent=agent, verifier=verifier)

    def cursor_log(self):
        with io.open(os.path.join(self.job, "cursor.log"), encoding="utf-8") as fh:
            return fh.read()

    def state(self):
        return loop.read_review(self.job)

    def status(self):
        return sh(self.root, "status", "--porcelain", "--untracked-files=all")


class InsideThePaths(Repo):
    def test_a_new_test_is_held_for_review_and_nothing_is_committed(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            return {"result": "created NewTest"}
        head = sh(self.root, "rev-parse", "HEAD")
        self.assertEqual(self.generate(agent), 0)
        r = self.state()
        self.assertEqual(r["state"], "pending-review")
        self.assertEqual(r["files"]["created"], [JIRA + "NewTest.java"])
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), head)
        diff = io.open(os.path.join(self.job, "proposed.diff"), encoding="utf-8").read()
        self.assertIn("+class NewTest {}", diff)

    def test_an_edit_to_an_existing_test_is_a_modification(self):
        def agent(_prompt):
            write(self.root, JIRA + "ExistingTest.java", "class ExistingTest { int x; }\n")
            return {"result": "edited"}
        self.assertEqual(self.generate(agent), 0)
        self.assertEqual(self.state()["files"]["modified"], [JIRA + "ExistingTest.java"])
        self.assertEqual(self.state()["files"]["created"], [])

    def test_the_prompt_carries_the_plan_and_the_allowed_paths(self):
        seen = {}
        def agent(prompt):
            seen["p"] = prompt
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            return {"result": ""}
        self.generate(agent)
        self.assertIn("create POST /x", seen["p"])
        self.assertIn(JIRA + "**", seen["p"])
        self.assertIn("Do not delete any file", seen["p"])


class OutsideThePaths(Repo):
    def test_a_file_outside_fails_the_job_and_everything_is_put_back(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            write(self.root, "src/main/java/Framework.java", "class Framework { /* hacked */ }\n")
            write(self.root, "stray.txt", "x\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertEqual(self.state()["state"], "rejected")
        self.assertIn("outside the allowed paths", self.state()["reason"])
        self.assertEqual(self.status(), "", "the tree must be exactly as it was")

    def test_your_own_uncommitted_edit_survives_a_rejection(self):
        """A file that was ALREADY changed is restored to that version,
        not to HEAD."""
        write(self.root, "README.md", "my unsaved work\n")
        def agent(_prompt):
            write(self.root, "README.md", "the agent overwrote this\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertEqual(io.open(os.path.join(self.root, "README.md")).read(),
                         "my unsaved work\n")

    def test_a_delete_fails_the_job_and_the_file_comes_back(self):
        def agent(_prompt):
            os.remove(os.path.join(self.root, JIRA + "ExistingTest.java"))
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertIn("deleted", self.state()["reason"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, JIRA + "ExistingTest.java")))

    def test_a_change_in_the_generated_tree_is_noticed_though_git_cannot_see_it(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            write(self.root, GEN + "goal/Hooks.java", "class Hooks { int changed; }\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        reason = self.state()["reason"]
        self.assertIn("generated file(s) outside this scope", reason)
        self.assertIn("put back", reason)
        with io.open(os.path.join(self.root, GEN + "goal/Hooks.java")) as fh:
            self.assertEqual(fh.read(), "class Hooks {}\n",
                             "git cannot restore it; the copy taken before the run does")
        self.assertFalse(os.path.isfile(os.path.join(self.root, JIRA + "NewTest.java")),
                         "the rest of the attempt is still undone")

    def test_an_agent_that_commits_is_rejected(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            sh(self.root, "add", "-A")
            sh(self.root, "commit", "-q", "-m", "agent went rogue")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertIn("HEAD moved", self.state()["reason"])

    def test_an_agent_that_changes_nothing_is_not_a_success(self):
        self.assertEqual(self.generate(lambda p: {"result": "I could not"}), 1)
        self.assertEqual(self.state()["state"], "rejected")


class Preconditions(Repo):
    def test_no_plan_no_run(self):
        os.remove(os.path.join(self.job, "plan.md"))
        called = []
        self.assertEqual(self.generate(lambda p: called.append(1) or {"result": ""}), 1)
        self.assertEqual(called, [])

    def test_uncommitted_work_inside_the_write_paths_blocks_the_run(self):
        write(self.root, JIRA + "Mine.java", "class Mine {}\n")
        called = []
        self.assertEqual(self.generate(lambda p: called.append(1) or {"result": ""}), 1)
        self.assertEqual(called, [], "the agent must not be called")

    def test_a_pending_review_blocks_a_second_generate(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 0)
        self.assertEqual(self.generate(agent), 1)


class VerifyAndRepair(Repo):
    def test_a_failed_verify_gets_one_repair_attempt_with_the_output(self):
        prompts, results = [], [
            {"ok": False, "steps": [{"name": "compile", "rc": 1, "tail": "NewTest.java:[3,5] cannot find symbol"}]},
            OK()]
        def agent(prompt):
            prompts.append(prompt)
            write(self.root, JIRA + "NewTest.java", "class NewTest { /* v%d */ }\n" % len(prompts))
            return {"result": ""}
        self.assertEqual(self.generate(agent, lambda: results.pop(0)), 0)
        self.assertEqual(len(prompts), 2)
        self.assertIn("cannot find symbol", prompts[1])
        self.assertNotIn("cannot find symbol", prompts[0])
        self.assertEqual(self.state()["state"], "pending-review")

    def test_two_failures_stop_and_are_not_offered_for_review(self):
        bad = lambda: {"ok": False, "steps": [{"name": "compile", "rc": 1, "tail": "boom"}]}
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent, bad), 1)
        self.assertEqual(self.state()["state"], "verify-failed")
        self.assertEqual(loop.cmd_approve(self.job, self.root, self.policy, "job1", "job1"), 1)

    def test_a_missing_compiler_is_a_failure_not_a_pass(self):
        scope = {"compile": ["definitely-not-a-real-compiler"], "checks": []}
        got = loop.run_verify(self.root, scope)
        self.assertFalse(got["ok"])
        self.assertIn("not on PATH", got["steps"][0]["tail"])


class ARunThatTimedOut(Repo):
    """Nobody is waiting for the run any more. It may not have stopped."""

    def setUp(self):
        super().setUp()
        self.policy["timeout_settle_seconds"] = 0

    def stuck(self, cancelled, after=None):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            write(self.root, JIRA + "ExistingTest.java", "class ExistingTest { int x; }\n")
            if after:
                after()
            raise loop.CursorFailed("Cursor did not finish within 2700s", "timeout", cancelled)
        return agent

    def test_a_cancelled_run_is_put_back_and_that_is_the_end_of_it(self):
        self.assertEqual(self.generate(self.stuck(cancelled=True)), 1)
        self.assertEqual(self.status(), "")
        r = self.state()
        self.assertEqual(r["state"], "rejected")
        self.assertFalse(r.get("needs_discard"))
        self.assertIn("did not finish within", r["reason"])

    def test_a_run_that_could_not_be_cancelled_leaves_a_job_that_can_be_discarded(self):
        self.assertEqual(self.generate(self.stuck(cancelled=False)), 1)
        self.assertEqual(self.status(), "", "what it wrote so far is put back")
        r = self.state()
        self.assertTrue(r["needs_discard"])
        self.assertIn("could not be confirmed stopped", r["reason"])
        self.assertIn("Discard", r["reason"])
        # ...and the run, still alive, writes again after the job has ended.
        write(self.root, JIRA + "LateTest.java", "class LateTest {}\n")
        write(self.root, "README.md", "scribbled on later\n")
        self.assertEqual(loop.cmd_discard(self.job, self.root), 0)
        self.assertEqual(self.status(), "", "discard puts back what came after")

    def test_writes_that_arrive_while_settling_are_seen_even_after_a_confirmed_cancel(self):
        import threading
        # The write lands well inside the wait, whatever the machine's speed:
        # the checks before the first putting-back take about a second.
        self.policy["timeout_settle_seconds"] = 8
        late = lambda: threading.Timer(
            4.0, lambda: write(self.root, JIRA + "LateTest.java", "class LateTest {}\n")).start()
        self.assertEqual(self.generate(self.stuck(cancelled=True, after=late)), 1)
        r = self.state()
        self.assertTrue(r["needs_discard"])
        self.assertIn("wrote again afterwards", r["reason"])
        self.assertEqual(self.status(), "")

    def test_a_write_during_the_checks_counts_as_the_run_still_writing(self):
        real = loop.hidden_flags

        def slow_check(root):
            if not getattr(slow_check, "done", False):
                slow_check.done = True
                write(self.root, JIRA + "DuringChecks.java", "class DuringChecks {}\n")
            return real(root)
        calls = []

        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            loop.hidden_flags = slow_check          # from now on: inside sweep()
            calls.append(1)
            raise loop.CursorFailed("Cursor did not finish within 2700s", "timeout", True)
        try:
            self.assertEqual(self.generate(agent), 1)
        finally:
            loop.hidden_flags = real
        r = self.state()
        self.assertTrue(r["needs_discard"])
        self.assertIn("wrote again afterwards", r["reason"])
        self.assertEqual(self.status(), "")

    def test_a_failed_call_is_swept_for_what_undo_does_not_look_at(self):
        """A run that timed out can have done everything a finished one
        can. The credential file, a hook, a hidden file: all checked on
        the finished path, and none on this one until now."""
        cfg = "src/main/resources/program_configuration.json"
        write(self.root, cfg, '{"pat": "the-real-one"}\n')
        self.policy["protected_files"] = [cfg]

        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            write(self.root, cfg, '{"pat": "swapped"}\n')
            write(self.root, ".git/hooks/pre-push", "#!/bin/sh\ncurl evil\n")
            raise loop.CursorFailed("Cursor did not finish within 2700s", "timeout", True)
        self.assertEqual(self.generate(agent), 1)
        with io.open(os.path.join(self.root, cfg), encoding="utf-8") as fh:
            self.assertIn("the-real-one", fh.read())
        self.assertFalse(os.path.isfile(os.path.join(self.root, ".git", "hooks", "pre-push")))
        r = self.state()
        self.assertFalse(r.get("needs_discard"),
                         "it was cancelled and everything is back: nothing for Discard to do")
        self.assertIn("credential/config file(s) were changed", r["reason"])
        self.assertIn("files inside .git were changed", r["reason"])

    def test_a_commit_made_before_the_call_failed_is_reported_not_hidden(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            sh(self.root, "add", "-A")
            sh(self.root, "commit", "-q", "-m", "sneaky")
            raise loop.CursorFailed("the agent started but did not finish", "run")
        self.assertEqual(self.generate(agent), 1)
        r = self.state()
        self.assertTrue(r["needs_discard"])
        self.assertIn("HEAD moved while it ran", r["reason"])
        self.assertIn("Nothing was reverted", r["reason"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, JIRA + "NewTest.java")),
                        "not 'restored' to the agent's own commit and called put back")

    def test_the_users_own_uncommitted_delete_is_not_read_as_the_run_writing_again(self):
        os.remove(os.path.join(self.root, "README.md"))

        def agent(_prompt):
            write(self.root, "README.md", "recreated by the agent\n")
            raise loop.CursorFailed("Cursor did not finish within 2700s", "timeout", True)
        self.assertEqual(self.generate(agent), 1)
        r = self.state()
        self.assertNotIn("wrote again afterwards", r["reason"])
        self.assertFalse(r.get("needs_discard"))

    def test_a_settle_time_that_is_not_a_number_does_not_crash_the_job(self):
        self.policy["timeout_settle_seconds"] = "0s"
        self.assertEqual(self.generate(self.stuck(cancelled=True)), 1)
        self.assertEqual(self.state()["state"], "rejected")

    def test_a_rejected_run_lists_no_files_that_are_no_longer_there(self):
        calls = []

        def agent(_prompt):
            calls.append(1)
            if len(calls) == 1:
                write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
                return {"result": "wrote it"}
            raise loop.CursorFailed("Cursor did not finish within 2700s", "timeout", True)
        failing = lambda: {"ok": False, "steps": [
            {"name": "compile", "rc": 1, "tail": "error"}]}
        self.assertEqual(self.generate(agent, verifier=failing), 1)
        r = self.state()
        self.assertEqual(len(calls), 2, "the repair attempt is the one that timed out")
        self.assertNotIn("files", r)
        self.assertFalse(os.path.isfile(os.path.join(self.job, "proposed.diff")))

    def test_discard_is_once_so_it_cannot_take_work_done_afterwards(self):
        self.assertEqual(self.generate(self.stuck(cancelled=False)), 1)
        self.assertEqual(loop.cmd_discard(self.job, self.root), 0)
        self.assertFalse(self.state().get("needs_discard"), "the button goes off")
        # The reviewer carries on working.
        write(self.root, "README.md", "my work, after the discard\n")
        write(self.root, "src/main/java/Mine.java", "class Mine {}\n")
        self.assertEqual(loop.cmd_discard(self.job, self.root), 1, "nothing to discard")
        with io.open(os.path.join(self.root, "README.md"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "my work, after the discard\n")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "src/main/java/Mine.java")))

    def test_an_ignored_file_that_was_already_there_survives_the_clean_up(self):
        """git reports a folder as ONE ignored entry once its tracked files
        are gone. Taking that entry whole took the reviewer's local file."""
        write(self.root, ".gitignore", "target/\n*.env\n")
        write(self.root, "data/x.txt", "tracked\n")
        sh(self.root, "add", "-A")
        sh(self.root, "commit", "-q", "-m", "data")
        write(self.root, "data/local.env", "MINE=1\n")           # ignored, the reviewer's

        def agent(_prompt):
            os.remove(os.path.join(self.root, "data", "x.txt"))
            write(self.root, "data/planted.env", "AGENT=1\n")
            raise loop.CursorFailed("the agent started but did not finish", "run")
        self.assertEqual(self.generate(agent), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "data", "local.env")))
        self.assertFalse(os.path.isfile(os.path.join(self.root, "data", "planted.env")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "data", "x.txt")))

    def test_any_other_failure_is_handled_as_before(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            raise loop.CursorFailed("the agent did not start: 401", "startup")
        self.assertEqual(self.generate(agent), 1)
        self.assertFalse(self.state().get("needs_discard"))
        self.assertEqual(self.status(), "")


class Discard(Repo):
    def test_discard_removes_what_the_agent_wrote_and_nothing_else(self):
        write(self.root, "README.md", "my unsaved work\n")
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            write(self.root, JIRA + "ExistingTest.java", "class ExistingTest { int x; }\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 0)
        self.assertEqual(loop.cmd_discard(self.job, self.root), 0)
        self.assertEqual(self.status(), "M README.md")
        self.assertEqual(self.state()["state"], "discarded")


class Push(Repo):
    def setUp(self):
        super().setUp()
        self.remote = tempfile.mkdtemp(prefix="looptest_remote_")
        self.addCleanup(shutil.rmtree, self.remote, ignore_errors=True)
        sh(self.remote, "init", "-q", "--bare")
        sh(self.root, "remote", "add", "origin", self.remote)
        sh(self.root, "push", "-q", "origin", "main")

    def pending(self, body="class NewTest {}\n"):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", body)
            return {"result": ""}
        self.assertEqual(self.generate(agent), 0)

    def push(self, confirm="job1", config=""):
        return loop.cmd_approve(self.job, self.root, self.policy, "job1", confirm,
                                config_path=config or os.path.join(self.root, "none.json"))

    def test_an_approved_job_goes_to_its_own_branch_never_main(self):
        self.pending()
        main_before = sh(self.remote, "rev-parse", "main")
        self.assertEqual(self.push(), 0)
        self.assertEqual(self.state()["state"], "pushed")
        self.assertEqual(sh(self.remote, "rev-parse", "main"), main_before)
        files = sh(self.remote, "show", "--name-only", "--format=", "agent/job1")
        self.assertEqual(files.splitlines(), [JIRA + "NewTest.java"])
        self.assertEqual(sh(self.root, "rev-parse", "--abbrev-ref", "HEAD"), "main",
                         "the checkout returns to where it was")

    def test_unrelated_uncommitted_work_is_not_swept_into_the_commit(self):
        write(self.root, "README.md", "my unsaved work\n")
        self.pending()
        self.assertEqual(self.push(), 0)
        files = sh(self.remote, "show", "--name-only", "--format=", "agent/job1")
        self.assertNotIn("README.md", files)
        self.assertIn("README.md", self.status())

    def test_no_push_without_the_confirmation(self):
        self.pending()
        self.assertEqual(self.push(confirm=""), 1)
        self.assertEqual(self.state()["state"], "pending-review")
        self.assertNotIn("agent/job1", sh(self.remote, "branch", "--list"))

    def test_a_job_that_was_never_reviewed_cannot_be_pushed(self):
        self.assertEqual(self.push(), 1)

    def test_a_credential_in_the_diff_stops_the_push_before_any_commit(self):
        self.pending('class NewTest { String password = "hunter2-very-secret"; }\n')
        head = sh(self.root, "rev-parse", "HEAD")
        self.assertEqual(self.push(), 1)
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), head)
        self.assertNotIn("agent/job1", sh(self.root, "branch", "--list"))
        self.assertNotIn("agent/job1", sh(self.remote, "branch", "--list"))

    def test_a_host_from_the_private_config_stops_the_push(self):
        cfg = os.path.join(self.root, "target", "program_configuration.json")
        write(self.root, "target/program_configuration.json", json.dumps(
            {"baseUrl": "https://api.internal-stage.corp.test/v2",
             "jira_config": {"token": "tok_abcdefghijklmnop"}}))
        self.pending('class NewTest { String u = "https://api.internal-stage.corp.test/v2/x"; }\n')
        self.assertEqual(self.push(config=cfg), 1)
        self.assertNotIn("agent/job1", sh(self.remote, "branch", "--list"))

    def test_an_ordinary_test_is_not_flagged(self):
        """NEGATIVE CONTROL for the scan: placeholders and Config lookups
        are how a test is supposed to reach a secret."""
        clean = ('+String t = Config.get("jira_config.token", "");\n'
                 '+String u = "#base_url#/guests/{guestId}";\n'
                 '+String password = "${#Project#password}";\n')
        self.assertEqual(loop.scan_secrets(clean, {"tok_abcdefghijklmnop": "x"}), [])
        self.assertTrue(loop.scan_secrets('+String t = "tok_abcdefghijklmnop";\n',
                                          {"tok_abcdefghijklmnop": "token"}))
        self.assertEqual(loop.scan_secrets('-String t = "tok_abcdefghijklmnop";\n',
                                           {"tok_abcdefghijklmnop": "token"}), [],
                         "a removed line is not a leak")


class ConvertedScope(Repo):
    """Cursor edits what the converter produced for ONE suite. git cannot
    see those files, so the diff, the undo and the limits all come from a
    copy taken before the run."""

    def edit_amex(self, _prompt):
        write(self.root, GEN + "amex/Hooks.java", "class Hooks { int amex; int fixed; }\n")
        write(self.root, IMPORTED + "amex/AmexTest.java", "class AmexTest { /* fixed */ }\n")
        return {"result": "fixed the hook", "status": "finished", "id": "run-1"}

    def test_an_edit_inside_the_suite_is_held_for_review_with_a_real_diff(self):
        self.assertEqual(self.generate(self.edit_amex, scope="converted", suite="amex"), 0)
        r = self.state()
        self.assertEqual(r["state"], "pending-review")
        self.assertFalse(r["pushable"])
        self.assertEqual(sorted(r["files"]["modified"]),
                         [GEN + "amex/Hooks.java", IMPORTED + "amex/AmexTest.java"])
        with io.open(os.path.join(self.job, "proposed.diff"), encoding="utf-8") as fh:
            diff = fh.read()
        self.assertIn("-class Hooks { int amex; }", diff)
        self.assertIn("+class Hooks { int amex; int fixed; }", diff)

    def test_another_suites_files_are_out_of_scope(self):
        def agent(_prompt):
            write(self.root, GEN + "amex/Hooks.java", "class Hooks { int ok; }\n")
            write(self.root, GEN + "goal/Hooks.java", "class Hooks { int not_yours; }\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent, scope="converted", suite="amex"), 1)
        self.assertIn("may only change suite `amex`", self.state()["reason"])
        self.assertIn("suite(s) goal", self.state()["reason"])
        with io.open(os.path.join(self.root, GEN + "goal/Hooks.java")) as fh:
            self.assertEqual(fh.read(), "class Hooks {}\n",
                             "the OTHER suite's hook is put back")
        with io.open(os.path.join(self.root, GEN + "amex/Hooks.java")) as fh:
            self.assertEqual(fh.read(), "class Hooks { int amex; }\n",
                             "the in-scope edit of the failed attempt is undone")

    def test_a_class_every_suite_shares_is_out_of_scope_too(self):
        """ImportedScenario sits next to the suite folders, not in one. A
        change to it changes every suite, so a one-suite job may not."""
        write(self.root, GEN + "ImportedScenario.java", "class ImportedScenario {}\n")
        def agent(_prompt):
            write(self.root, GEN + "amex/Hooks.java", "class Hooks { int ok; }\n")
            write(self.root, GEN + "ImportedScenario.java", "class ImportedScenario { int x; }\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent, scope="converted", suite="amex"), 1)
        self.assertIn("files every suite shares", self.state()["reason"])
        with io.open(os.path.join(self.root, GEN + "ImportedScenario.java")) as fh:
            self.assertEqual(fh.read(), "class ImportedScenario {}\n")

    def test_cursor_is_told_which_files_are_the_suites_and_which_are_not(self):
        write(self.root, GEN + "ImportedScenario.java", "class ImportedScenario {}\n")
        seen = {}
        def agent(prompt):
            seen["p"] = prompt
            return self.edit_amex(prompt)
        self.assertEqual(self.generate(agent, scope="converted", suite="amex"), 0)
        p = seen["p"]
        self.assertIn("THE SUITE YOU ARE WORKING IN: amex", p)
        self.assertIn("  " + GEN + "amex/Hooks.java", p)            # its own
        shared = p[p.index("SHARED by every suite"):p.index("OTHER suites")]
        self.assertIn(GEN + "ImportedScenario.java", shared)
        self.assertNotIn("amex/Hooks.java", shared)
        self.assertIn("OTHER suites -- their folders are off limits: goal", p)
        own = p[p.index("This suite's generated Java"):p.index("SHARED by every suite")]
        self.assertNotIn("goal/Hooks.java", own, "another suite's file is not listed as its own")

    def test_tracked_code_is_out_of_scope_for_a_converted_job(self):
        def agent(_prompt):
            write(self.root, GEN + "amex/Hooks.java", "class Hooks { int ok; }\n")
            write(self.root, "tools/ra_converter/ra_converter.py", "X = 2\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent, scope="converted", suite="amex"), 1)
        self.assertEqual(self.status(), "")

    def test_deleting_a_generated_file_is_refused_and_it_comes_back(self):
        def agent(_prompt):
            os.remove(os.path.join(self.root, CSV + "amex/a.csv"))
            return {"result": ""}
        self.assertEqual(self.generate(agent, scope="converted", suite="amex"), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.root, CSV + "amex/a.csv")))

    def test_discard_puts_the_generated_files_back(self):
        self.assertEqual(self.generate(self.edit_amex, scope="converted", suite="amex"), 0)
        self.assertEqual(loop.cmd_discard(self.job, self.root), 0)
        with io.open(os.path.join(self.root, GEN + "amex/Hooks.java")) as fh:
            self.assertEqual(fh.read(), "class Hooks { int amex; }\n")

    def test_approve_keeps_them_locally_and_never_commits(self):
        self.assertEqual(self.generate(self.edit_amex, scope="converted", suite="amex"), 0)
        head = sh(self.root, "rev-parse", "HEAD")
        self.assertEqual(loop.cmd_approve(self.job, self.root, self.policy, "job1", "job1"), 0)
        self.assertEqual(self.state()["state"], "approved-local")
        self.assertEqual(self.state()["stored"], "amex/001-job1")
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), head)
        self.assertEqual(sh(self.root, "branch", "--list", "agent/*"), "")
        self.assertTrue(os.path.isfile(os.path.join(self.job, "converted.patch")))
        with io.open(os.path.join(self.root, GEN + "amex/Hooks.java")) as fh:
            self.assertIn("fixed", fh.read(), "the approved edit stays in place")

    def test_a_suite_is_required_and_must_exist(self):
        called = []
        agent = lambda p: called.append(1) or {"result": ""}
        self.assertEqual(self.generate(agent, scope="converted", suite=""), 1)
        self.assertEqual(self.generate(agent, scope="converted", suite="nosuchsuite"), 1)
        self.assertEqual(self.generate(agent, scope="converted", suite="../etc"), 1)
        self.assertEqual(called, [])


class SurvivesAReconvert(Repo):
    """An approved change to converted Java is put back after the
    converter rewrites the suite."""

    HOOK = GEN + "amex/Hooks.java"
    BEFORE = "class Hooks {\n    int a;\n    int b;\n    int c;\n    int d;\n    int e;\n}\n"

    def setUp(self):
        super().setUp()
        write(self.root, self.HOOK, self.BEFORE)
        def agent(_prompt):
            write(self.root, self.HOOK, self.BEFORE.replace("int e;", "int e; // approved fix"))
            write(self.root, GEN + "amex/Extra.java", "class Extra {}\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent, scope="converted", suite="amex"), 0)
        self.assertEqual(loop.cmd_approve(self.job, self.root, self.policy, "job1", "job1"), 0)

    def reconvert(self, text):
        """What a convert does: rewrite the suite's files from scratch."""
        write(self.root, self.HOOK, text)
        p = os.path.join(self.root, GEN + "amex/Extra.java")
        if os.path.isfile(p):
            os.remove(p)

    def hook(self):
        with io.open(os.path.join(self.root, self.HOOK)) as fh:
            return fh.read()

    def test_the_same_output_gets_the_change_back(self):
        self.reconvert(self.BEFORE)
        r = loop.patches.reapply(self.root, "amex")
        self.assertEqual(len(r["applied"]), 2, r)
        self.assertEqual(r["conflicts"], [])
        self.assertIn("// approved fix", self.hook())
        self.assertTrue(os.path.isfile(os.path.join(self.root, GEN + "amex/Extra.java")))

    def test_new_converter_output_elsewhere_in_the_file_is_kept_too(self):
        """The reason this is a merge and not a diff: the converter changed
        a different line, and both changes must survive."""
        self.reconvert(self.BEFORE.replace("int a;", "int a; // converter now emits this"))
        r = loop.patches.reapply(self.root, "amex")
        self.assertEqual(r["conflicts"], [], r)
        self.assertIn("// approved fix", self.hook())
        self.assertIn("// converter now emits this", self.hook())

    def test_an_overlap_leaves_the_converters_file_alone_and_says_so(self):
        regenerated = self.BEFORE.replace("int e;", "long e;   // converter changed the same line")
        self.reconvert(regenerated)
        r = loop.patches.reapply(self.root, "amex")
        self.assertEqual(len(r["conflicts"]), 1, r)
        self.assertIn(".agent-patches/amex/001-job1/after/", r["conflicts"][0])
        self.assertEqual(self.hook(), regenerated, "no conflict markers, no half-applied file")
        self.assertNotIn("<<<<<<<", self.hook())

    def test_running_it_twice_changes_nothing_the_second_time(self):
        self.reconvert(self.BEFORE)
        loop.patches.reapply(self.root, "amex")
        again = loop.patches.reapply(self.root, "amex")
        self.assertEqual(again["applied"], [])
        self.assertEqual(len(again["already"]), 2)

    def test_the_converter_entry_point_reports_and_never_raises(self):
        self.reconvert(self.BEFORE)
        self.assertEqual(loop.patches.reapply_after_convert(self.root, ["amex", "goal", "../x"]), 0)
        self.assertIn("// approved fix", self.hook())

    def test_a_stored_change_cannot_name_another_suites_file(self):
        """NEGATIVE CONTROL: the store is per suite. A manifest edited to
        point at another suite's hook is refused, not applied."""
        entry = os.path.join(self.root, ".agent-patches", "amex", "001-job1")
        with io.open(os.path.join(entry, "manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)
        foreign = GEN + "goal/Hooks.java"
        m["modified"].append(foreign)
        write(self.root, ".agent-patches/amex/001-job1/manifest.json", json.dumps(m))
        write(self.root, ".agent-patches/amex/001-job1/after/" + foreign, "class Hooks { int hijacked; }\n")
        write(self.root, ".agent-patches/amex/001-job1/base/" + foreign, "class Hooks {}\n")
        r = loop.patches.reapply(self.root, "amex")
        self.assertTrue(any(foreign in x for x in r["refused"]), r)
        with io.open(os.path.join(self.root, foreign)) as fh:
            self.assertEqual(fh.read(), "class Hooks {}\n")
        with self.assertRaises(ValueError):
            loop.patches.save(self.root, "amex", "j", [], [foreign], self.root)

    def test_the_next_job_is_told_what_was_already_approved(self):
        seen = {}
        def agent(prompt):
            seen["p"] = prompt
            write(self.root, GEN + "amex/Other.java", "class Other {}\n")
            return {"result": ""}
        job2 = os.path.join(self.root, "target", "agent", "job2")
        os.makedirs(job2)
        write(self.root, "target/agent/job2/plan.md", "# plan\n")
        self.assertEqual(loop.cmd_generate(job2, self.root, self.policy, "converted", "amex",
                                           agent=agent, verifier=OK), 0)
        self.assertIn("Changes already approved for this suite", seen["p"])
        self.assertIn("001-job1: " + self.HOOK, seen["p"])

    def test_forget_removes_one_entry(self):
        self.assertTrue(loop.patches.forget(self.root, "amex", "001-job1"))
        self.assertEqual(loop.patches.entries(self.root, "amex"), [])
        self.assertFalse(loop.patches.forget(self.root, "amex", "001-job1"))


class ConverterScope(Repo):
    def test_the_converter_is_writable_and_a_test_path_is_not(self):
        def good(_prompt):
            write(self.root, "tools/ra_converter/ra_converter.py", "X = 2\n")
            return {"result": ""}
        self.assertEqual(self.generate(good, scope="converter"), 0)
        self.assertEqual(self.state()["files"]["modified"],
                         ["tools/ra_converter/ra_converter.py"])
        self.assertTrue(self.state()["pushable"])

    def test_a_converter_job_may_not_edit_generated_output(self):
        """The rule the converter work has always had: fix the emitter,
        never the copy it generated."""
        def agent(_prompt):
            write(self.root, "tools/ra_converter/ra_converter.py", "X = 2\n")
            write(self.root, GEN + "amex/Hooks.java", "class Hooks { int patched; }\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent, scope="converter"), 1)
        self.assertEqual(self.status(), "")

    def test_the_gate_fails_the_job_only_on_a_NEW_failing_check(self):
        scope = {"compile": [], "checks": [], "gate": True}
        after = {"old-known": "python a.py", "freshly-broken": "python b.py"}
        orig = loop.gate_failures
        loop.gate_failures = lambda root, out, runner=None: dict(after)
        try:
            same = loop.run_verify(self.root, scope, gate_before=dict(after))
            worse = loop.run_verify(self.root, scope, gate_before={"old-known": ""})
        finally:
            loop.gate_failures = orig
        self.assertTrue(same["ok"], "what already failed is not the agent's doing")
        self.assertFalse(worse["ok"])
        self.assertIn("NEW failing check: freshly-broken", worse["steps"][-1]["tail"])
        self.assertNotIn("NEW failing check: old-known", worse["steps"][-1]["tail"])

    def test_an_unknown_scope_is_refused(self):
        self.assertEqual(self.generate(lambda p: {"result": ""}, scope="everything"), 1)


class TheCursorLog(Repo):
    def test_it_records_the_conversation_and_the_verdict(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            return {"result": "I created NewTest.java", "status": "finished",
                    "id": "run-42", "model": "composer-2.5"}
        self.assertEqual(self.generate(agent), 0)
        log = self.cursor_log()
        for expected in ("scope=new-test", "may write:", "create POST /x",
                         "I created NewTest.java", "run id=run-42",
                         "created   " + JIRA + "NewTest.java",
                         "verify ok", "PENDING REVIEW"):
            self.assertIn(expected, log)

    def test_a_rejection_and_its_undo_are_in_it(self):
        def agent(_prompt):
            write(self.root, "stray.txt", "x\n")
            return {"result": "oops"}
        self.assertEqual(self.generate(agent), 1)
        log = self.cursor_log()
        self.assertIn("REJECTED: files changed outside the allowed paths", log)
        self.assertIn("undo: removed stray.txt", log)

    def test_it_is_appended_to_not_replaced(self):
        self.generate(lambda p: {"result": "first"})
        self.generate(lambda p: {"result": "second"})
        log = self.cursor_log()
        self.assertIn("first", log)
        self.assertIn("second", log)

    def test_the_key_never_reaches_it(self):
        clog = loop.CursorLog(self.job, secret="key_SUPERSECRET123")
        clog.block("answer", "the agent echoed key_SUPERSECRET123 back")
        self.assertNotIn("SUPERSECRET", self.cursor_log())
        self.assertIn("<redacted>", self.cursor_log())


class OneJobAtATime(Repo):
    def test_a_second_job_is_refused_while_one_holds_the_tree(self):
        called = []
        with loop.RepoLock(self.root, "generate, job other"):
            rc = self.generate(lambda p: called.append(1) or {"result": ""})
        self.assertEqual(rc, 1)
        self.assertEqual(called, [], "the agent must not be called")

    def test_the_lock_goes_with_the_job(self):
        with loop.RepoLock(self.root, "x"):
            pass
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 0)


class AnUnfinishedRun(Repo):
    """Stopped from the page, or the process died: no file list was ever
    written, so discard works from what was saved before the agent ran."""

    def crash(self, scope="new-test", suite=""):
        def agent(_prompt):
            write(self.root, JIRA + "Half.java", "class Half {\n")
            write(self.root, GEN + "amex/Hooks.java", "class Hooks { int half; }\n")
            write(self.root, "README.md", "half an edit\n")
            raise KeyboardInterrupt()           # what a kill looks like
        with self.assertRaises(KeyboardInterrupt):
            self.generate(agent, scope=scope, suite=suite)

    def test_discard_puts_everything_back(self):
        self.crash()
        self.assertEqual(self.state()["state"], "generating")
        self.assertEqual(loop.cmd_discard(self.job, self.root), 0)
        self.assertEqual(self.status(), "")
        with io.open(os.path.join(self.root, GEN + "amex/Hooks.java")) as fh:
            self.assertEqual(fh.read(), "class Hooks { int amex; }\n")
        self.assertEqual(self.state()["state"], "discarded")

    def test_it_cannot_be_run_again_on_top_of_the_leftovers(self):
        self.crash()
        called = []
        self.assertEqual(self.generate(lambda p: called.append(1) or {"result": ""}), 1)
        self.assertEqual(called, [])

    def test_a_failed_verify_must_be_discarded_before_another_run(self):
        """Otherwise the next run copies the FAILED edit as 'how it was'."""
        bad = lambda: {"ok": False, "steps": [{"name": "compile", "rc": 1, "tail": "x"}]}
        def agent(_prompt):
            write(self.root, GEN + "amex/Hooks.java", "class Hooks { int broken; }\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent, bad, scope="converted", suite="amex"), 1)
        called = []
        again = self.generate(lambda p: called.append(1) or {"result": ""},
                              scope="converted", suite="amex")
        self.assertEqual((again, called), (1, []))
        self.assertEqual(loop.cmd_discard(self.job, self.root), 0)
        with io.open(os.path.join(self.root, GEN + "amex/Hooks.java")) as fh:
            self.assertEqual(fh.read(), "class Hooks { int amex; }\n")


class ATreeThatAlreadyFails(unittest.TestCase):
    """Verify must judge the agent's change, not the state of the tree."""

    OUT = (b"[ERROR] /C:/repo/src/test/java/com/hi/api/tests/manual/GoalShop.java:[251,23] cannot find symbol\n"
           b"[ERROR] /C:/repo/src/test/java/com/hi/api/tests/jira/NewTest.java:[3,5] ';' expected\n")

    def verify(self, before):
        scope = {"compile": [sys.executable], "checks": []}
        failing = lambda argv: types.SimpleNamespace(returncode=1, stdout=self.OUT)
        return loop.run_verify(".", scope, runner=failing, compile_before=before)

    def test_the_failing_files_are_read_from_the_output(self):
        self.assertEqual(loop.compile_error_files(self.OUT), {
            "src/test/java/com/hi/api/tests/manual/GoalShop.java",
            "src/test/java/com/hi/api/tests/jira/NewTest.java"})

    def test_what_failed_before_does_not_fail_the_job(self):
        both = loop.compile_error_files(self.OUT)
        got = self.verify(both)
        self.assertTrue(got["ok"], got)
        self.assertIn("did not compile BEFORE", got["steps"][0]["tail"])

    def test_a_new_failing_file_does(self):
        got = self.verify({"src/test/java/com/hi/api/tests/manual/GoalShop.java"})
        self.assertFalse(got["ok"])
        self.assertIn("NEW files with errors: src/test/java/com/hi/api/tests/jira/NewTest.java",
                      got["steps"][0]["tail"])

    def test_a_tree_that_compiled_before_gets_no_allowance(self):
        self.assertFalse(self.verify(None)["ok"])

    def test_an_unreadable_failure_is_a_failure(self):
        """NEGATIVE CONTROL: if no file can be read from the output, 'no
        new file' proves nothing."""
        scope = {"compile": [sys.executable], "checks": []}
        opaque = lambda argv: types.SimpleNamespace(returncode=1, stdout=b"BUILD FAILURE\n")
        self.assertFalse(loop.run_verify(".", scope, runner=opaque, compile_before={"x.java"})["ok"])


class CredentialFiles(Repo):
    CFG = "src/main/resources/program_configuration.json"

    def setUp(self):
        super().setUp()
        with io.open(os.path.join(self.root, ".gitignore"), "a") as fh:
            fh.write(self.CFG + "\n")
        sh(self.root, "commit", "-q", "-am", "ignore config")
        write(self.root, self.CFG, json.dumps({"jira_config": {"token": "tok_abcdefghijklmnop"}}))

    def test_a_change_to_the_private_config_is_seen_and_put_back(self):
        """git ignores it and it is not generated code: nothing else here
        would notice."""
        original = io.open(os.path.join(self.root, self.CFG)).read()
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            write(self.root, self.CFG, json.dumps({"jira_config": {"token": "replaced"}}))
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertIn("credential/config file(s) were changed", self.state()["reason"])
        self.assertEqual(io.open(os.path.join(self.root, self.CFG)).read(), original)
        self.assertFalse(os.path.exists(os.path.join(self.job, "pre", self.CFG)),
                         "the credentials are not copied into the job folder")

    def test_a_secret_the_agent_quotes_is_not_written_to_the_cursor_log(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")
            return {"result": "I read the token tok_abcdefghijklmnop from the config"}
        self.assertEqual(self.generate(agent), 0)
        self.assertNotIn("tok_abcdefghijklmnop", self.cursor_log())
        self.assertIn("I read the token <redacted>", self.cursor_log())

    def test_a_secret_far_into_a_new_file_still_stops_the_push(self):
        """The diff shown for review is capped; the scan is not."""
        remote = tempfile.mkdtemp(prefix="looptest_remote_")
        self.addCleanup(shutil.rmtree, remote, ignore_errors=True)
        sh(remote, "init", "-q", "--bare")
        sh(self.root, "remote", "add", "origin", remote)
        filler = "// padding line to push the next one past the review cap\n" * 9000
        def agent(_prompt):
            write(self.root, JIRA + "Big.java",
                  filler + 'class Big { String t = "tok_abcdefghijklmnop"; }\n')
            return {"result": ""}
        self.assertEqual(self.generate(agent), 0)
        self.assertGreater(len(filler), 400000)
        rc = loop.cmd_approve(self.job, self.root, self.policy, "job1", "job1",
                              config_path=os.path.join(self.root, self.CFG))
        self.assertEqual(rc, 1)
        self.assertEqual(sh(remote, "branch", "--list"), "")


class StoredChangesInOrder(Repo):
    HOOK = GEN + "amex/Hooks.java"
    V0 = "class Hooks {\n    int a;\n    int b;\n    int c;\n}\n"

    def approve(self, job, text):
        d = os.path.join(self.root, "target", "agent", job)
        os.makedirs(d, exist_ok=True)
        write(self.root, f"target/agent/{job}/plan.md", "# plan\n")
        def agent(_prompt):
            write(self.root, self.HOOK, text)
            return {"result": ""}
        self.assertEqual(loop.cmd_generate(d, self.root, self.policy, "converted", "amex",
                                           agent=agent, verifier=OK), 0)
        self.assertEqual(loop.cmd_approve(d, self.root, self.policy, job, job), 0)

    def setUp(self):
        super().setUp()
        write(self.root, self.HOOK, self.V0)
        self.approve("one", self.V0.replace("int c;", "int c; // first"))
        self.approve("two", self.V0.replace("int c;", "int c; // first, then second"))

    def ids(self):
        return [e["id"] for e in loop.patches.entries(self.root, "amex")]

    def test_two_changes_to_one_file_come_back_in_order(self):
        write(self.root, self.HOOK, self.V0)                 # a reconvert
        r = loop.patches.reapply(self.root, "amex")
        self.assertEqual(r["conflicts"], [], r)
        with io.open(os.path.join(self.root, self.HOOK)) as fh:
            self.assertIn("// first, then second", fh.read())

    def test_when_the_first_cannot_apply_the_second_is_held_back_too(self):
        clash = self.V0.replace("int c;", "long c; // the converter changed this line")
        write(self.root, self.HOOK, clash)
        r = loop.patches.reapply(self.root, "amex")
        self.assertEqual(len(r["conflicts"]), 2, r)
        self.assertIn("builds on it", r["conflicts"][1])
        with io.open(os.path.join(self.root, self.HOOK)) as fh:
            self.assertEqual(fh.read(), clash, "half a sequence is not applied")

    def test_numbers_are_not_reused_after_one_is_forgotten(self):
        self.assertEqual(self.ids(), ["001-one", "002-two"])
        loop.patches.forget(self.root, "amex", "001-one")
        self.approve("three", self.V0.replace("int a;", "int a; // third"))
        self.assertEqual(self.ids(), ["002-two", "003-three"])


class WhatGitWouldNotShow(Repo):
    """Ways a change can be made so that a plain `git status` in the
    project folder does not report it. Each must still fail the job."""

    def new_test(self):
        write(self.root, JIRA + "NewTest.java", "class NewTest {}\n")

    def test_a_staged_new_file_is_still_a_new_file_with_a_full_diff(self):
        """`git add` made a new file look tracked, and the diff against the
        index empty -- an empty review and an empty secret scan."""
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java", 'class NewTest { String p = "x"; }\n')
            sh(self.root, "add", JIRA + "NewTest.java")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 0)
        self.assertEqual(self.state()["files"]["created"], [JIRA + "NewTest.java"])
        with io.open(os.path.join(self.job, "proposed.diff"), encoding="utf-8") as fh:
            self.assertIn('+class NewTest { String p = "x"; }', fh.read())

    def test_a_staged_edit_shows_in_the_diff_and_discard_undoes_it(self):
        def agent(_prompt):
            write(self.root, JIRA + "ExistingTest.java", "class ExistingTest { int staged; }\n")
            sh(self.root, "add", JIRA + "ExistingTest.java")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 0)
        with io.open(os.path.join(self.job, "proposed.diff"), encoding="utf-8") as fh:
            self.assertIn("+class ExistingTest { int staged; }", fh.read())
        self.assertEqual(loop.cmd_discard(self.job, self.root), 0)
        self.assertEqual(self.status(), "")

    def test_a_file_marked_assume_unchanged_is_found(self):
        def agent(_prompt):
            self.new_test()
            sh(self.root, "update-index", "--assume-unchanged", "src/main/java/Framework.java")
            write(self.root, "src/main/java/Framework.java", "class Framework { /* hidden */ }\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertIn("assume-unchanged", self.state()["reason"])
        self.assertEqual(self.status(), "", "the mark is removed and the file restored")
        self.assertEqual(loop.hidden_flags(self.root), set())

    def test_a_folder_that_ignores_itself_is_found_and_removed(self):
        def agent(_prompt):
            self.new_test()
            write(self.root, "src/main/java/sneaky/.gitignore", "*\n")
            write(self.root, "src/main/java/sneaky/Evil.java", "class Evil {}\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertIn("git ignores", self.state()["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "src/main/java/sneaky")))

    def test_a_hook_or_a_config_change_inside_dot_git_is_put_back(self):
        hook = os.path.join(self.root, ".git", "hooks", "pre-commit")
        config = os.path.join(self.root, ".git", "config")
        before = io.open(config, "rb").read()
        def agent(_prompt):
            self.new_test()
            write(self.root, ".git/hooks/pre-commit", "#!/bin/sh\ngit add -f target/leak.txt\n")
            sh(self.root, "config", "remote.origin.url", "https://elsewhere.example.com/x.git")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertIn("inside .git", self.state()["reason"])
        self.assertFalse(os.path.exists(hook))
        self.assertEqual(io.open(config, "rb").read(), before)

    def test_a_same_size_write_with_the_old_timestamp_is_still_seen(self):
        """Size and mtime were the whole check; both can be kept."""
        target = os.path.join(self.root, GEN + "goal/Hooks.java")
        def agent(_prompt):
            self.new_test()
            st = os.stat(target)
            with io.open(target, "w", newline="") as fh:
                fh.write("class Hookz {}\n")            # same length as before
            os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertEqual(io.open(target).read(), "class Hooks {}\n")

    def test_a_rejection_that_could_not_undo_can_be_discarded(self):
        def agent(_prompt):
            self.new_test()
            write(self.root, "README.md", "changed\n")
            sh(self.root, "add", "-A")
            sh(self.root, "commit", "-q", "-m", "agent went rogue")
            write(self.root, "stray.txt", "left behind\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 1)
        self.assertTrue(self.state()["needs_discard"])
        self.assertEqual(loop.cmd_discard(self.job, self.root), 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "stray.txt")))


class AProjectBelowTheRepositoryTop(unittest.TestCase):
    """The real layout: the git top level is three folders above the
    project. A file written up there is outside `.`."""

    def test_a_file_above_the_project_folder_is_seen_and_removed(self):
        top = tempfile.mkdtemp(prefix="looptest_top_")
        self.addCleanup(shutil.rmtree, top, ignore_errors=True)
        sh(top, "init", "-q", "-b", "main")
        sh(top, "config", "user.email", "t@example.com")
        sh(top, "config", "user.name", "t")
        root = os.path.join(top, "src", "main", "proj")
        write(top, ".gitignore", "target/\n")
        write(top, "TOP.md", "top\n")
        write(root, JIRA + "ExistingTest.java", "class ExistingTest {}\n")
        sh(top, "add", "-A")
        sh(top, "commit", "-q", "-m", "base")
        job = os.path.join(root, "target", "agent", "job1")
        write(root, "target/agent/job1/plan.md", "# plan\n")
        policy = loop.load_policy(os.path.join(root, "none.json"))

        def agent(_prompt):
            write(root, JIRA + "NewTest.java", "class NewTest {}\n")
            write(top, "TOP.md", "edited above the project\n")
            write(top, "ABOVE.txt", "new above the project\n")
            return {"result": ""}
        rc = loop.cmd_generate(job, root, policy, agent=agent, verifier=OK)
        self.assertEqual(rc, 1)
        self.assertIn("outside the allowed paths", loop.read_review(job)["reason"])
        self.assertEqual(io.open(os.path.join(top, "TOP.md")).read(), "top\n")
        self.assertFalse(os.path.exists(os.path.join(top, "ABOVE.txt")))
        self.assertEqual(sh(top, "status", "--porcelain"), "")

        def good(_prompt):
            write(root, JIRA + "NewTest.java", "class NewTest {}\n")
            return {"result": ""}
        write(root, "target/agent/job2/plan.md", "# plan\n")
        job2 = os.path.join(root, "target", "agent", "job2")
        self.assertEqual(loop.cmd_generate(job2, root, policy, agent=good, verifier=OK), 0,
                         "NEGATIVE CONTROL: an in-scope change in a nested project still passes")


class WhatApprovePublishes(Push):
    def test_local_commits_that_were_never_pushed_are_not_published(self):
        write(self.root, "private-notes.md", "not for the remote\n")
        sh(self.root, "add", "-A")
        sh(self.root, "commit", "-q", "-m", "local only, not pushed")
        self.pending()
        self.assertEqual(self.push(), 0)
        files = sh(self.remote, "ls-tree", "-r", "--name-only", "agent/job1")
        self.assertNotIn("private-notes.md", files)
        self.assertIn(JIRA + "NewTest.java", files)
        self.assertEqual(sh(self.remote, "rev-list", "--count", "main..agent/job1"), "1")

    def test_it_is_refused_when_the_unpushed_commits_touch_the_same_file(self):
        write(self.root, JIRA + "ExistingTest.java", "class ExistingTest { int local; }\n")
        sh(self.root, "commit", "-q", "-am", "local only")
        def agent(_prompt):
            write(self.root, JIRA + "ExistingTest.java", "class ExistingTest { int local; int agent; }\n")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 0)
        self.assertEqual(self.push(), 1)
        self.assertEqual(sh(self.remote, "branch", "--list", "agent/*"), "")

    def test_the_pasted_story_is_not_in_the_commit_message(self):
        write(self.root, "target/agent/job1/brief.md",
              "# brief\nSee https://jira.internal.corp.test/browse/SECRET-1\n")
        self.pending()
        self.assertEqual(self.push(), 0)
        msg = sh(self.remote, "log", "-1", "--format=%B", "agent/job1")
        self.assertNotIn("internal.corp.test", msg)
        self.assertIn(JIRA + "NewTest.java", msg)

    def test_a_staged_file_with_a_credential_cannot_be_pushed(self):
        def agent(_prompt):
            write(self.root, JIRA + "NewTest.java",
                  'class NewTest { String password = "hunter2-very-secret"; }\n')
            sh(self.root, "add", JIRA + "NewTest.java")
            return {"result": ""}
        self.assertEqual(self.generate(agent), 0)
        self.assertEqual(self.push(), 1)
        self.assertEqual(sh(self.remote, "branch", "--list", "agent/*"), "")
        self.assertEqual(sh(self.root, "branch", "--list", "agent/*"), "")

    def test_the_checkout_and_the_working_tree_are_left_alone(self):
        write(self.root, "README.md", "my unsaved work\n")
        self.pending()
        self.assertEqual(self.push(), 0)
        self.assertEqual(sh(self.root, "rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertTrue(os.path.isfile(os.path.join(self.root, JIRA + "NewTest.java")),
                        "the new test does not vanish from the working tree")
        self.assertEqual(io.open(os.path.join(self.root, "README.md")).read(), "my unsaved work\n")

    def test_a_file_edited_after_the_review_needs_a_second_look(self):
        self.pending()
        write(self.root, JIRA + "NewTest.java", "class NewTest { /* edited after review */ }\n")
        self.assertEqual(self.push(), 1)
        self.assertEqual(sh(self.remote, "branch", "--list", "agent/*"), "")
        with io.open(os.path.join(self.job, "proposed.diff"), encoding="utf-8") as fh:
            self.assertIn("edited after review", fh.read())
        self.assertEqual(self.push(), 0, "approved again, now that the diff is current")

    def test_a_review_file_pointing_outside_its_scope_is_refused(self):
        self.pending()
        r = self.state()
        r["files"]["created"].append("README.md")
        loop.write_review(self.job, r)
        self.assertEqual(self.push(), 1)

    def test_no_hook_runs_on_the_way_out(self):
        self.pending()
        write(self.root, ".git/hooks/pre-push", "#!/bin/sh\nexit 1\n")
        write(self.root, ".git/hooks/pre-commit", "#!/bin/sh\nexit 1\n")
        self.assertEqual(self.push(), 0)


class TheSecretScan(unittest.TestCase):
    def found(self, line, known=None):
        return bool(loop.scan_secrets("+" + line + "\n", known or {}))

    def test_the_usual_spellings_of_a_credential_name(self):
        for line in ('String dbPassword = "Pr0d-s3cret!";',
                     'String accessToken = "a1b2c3d4e5f6g7";',
                     'API_TOKEN = "zz99yy88xx77";',
                     'headers.put("password", "Pr0d-s3cret!");',
                     'given().header("Authorization", "Basic dXNlcjpwYXNzd29yZDEyMw==");',
                     'String u = "jdbc:oracle:thin:@db.internal.test:1521/x";'):
            self.assertTrue(self.found(line), line)

    def test_ordinary_test_code_is_not_blocked(self):
        """NEGATIVE CONTROL: a scan that cries wolf gets switched off."""
        for line in ('String token = "access_token";',
                     'String password = "invalid-password";',
                     'String password = "${#Project#password}";',
                     'String t = Config.get("jira_config.token", "");',
                     'String tokenPath = "/oauth2/v1/token";'):
            self.assertFalse(self.found(line), line)

    def test_config_values_hosts_addresses_and_routes(self):
        cfg = os.path.join(tempfile.mkdtemp(prefix="looptest_cfg_"), "c.json")
        with io.open(cfg, "w") as fh:
            json.dump({"baseUrl": "https://Api.Internal-Stage.corp.test/v2",
                       "db": {"host": "10.20.30.40"},
                       "token_route": "/oauth2/v1/token",
                       "jira_config": {"token": "ABC#1234xyz"}}, fh)
        known = loop.config_secrets(cfg)
        self.assertTrue(self.found('String u = "https://API.internal-stage.CORP.test/x";', known))
        self.assertTrue(self.found('String h = "10.20.30.40";', known))
        self.assertTrue(self.found('String t = "abc#1234XYZ";', known), "case does not hide it")
        self.assertFalse(self.found('String r = "/oauth2/v1/token";', known),
                         "a route is not a secret")

    def test_the_cursor_log_redacts_whatever_the_case(self):
        d = tempfile.mkdtemp(prefix="looptest_log_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        loop.CursorLog(d, secrets=["ABC#1234xyz"]).write("the agent printed abc#1234XYZ here")
        with io.open(os.path.join(d, "cursor.log"), encoding="utf-8") as fh:
            self.assertNotIn("1234", fh.read())


class TheStoreIsNotAWayOut(Repo):
    def test_forget_cannot_be_pointed_at_the_store_itself(self):
        write(self.root, ".agent-patches/amex/001-j/manifest.json", "{}")
        write(self.root, ".agent-patches/goal/001-j/manifest.json", "{}")
        for bad in ("..", ".", "../goal", "001-j/..", ""):
            with self.assertRaises(ValueError):
                loop.patches.forget(self.root, "amex", bad)
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".agent-patches", "goal")))

    def test_line_endings_alone_do_not_make_a_conflict(self):
        base = b"class A {\r\n    int a;\r\n    int b;\r\n    int c;\r\n    int d;\r\n}\r\n"
        after = base.replace(b"\r\n", b"\n").replace(b"int d;", b"int d; // approved")
        now = base.replace(b"int a;", b"int a; // converter")
        merged, clean = loop.patches.merge3(base, after, now)
        self.assertTrue(clean)
        self.assertIn(b"// approved", merged)
        self.assertIn(b"// converter", merged)
        self.assertNotIn(b"\n", merged.replace(b"\r\n", b""), "CRLF like the converter's file")


class TheRealCursorPath(unittest.TestCase):
    """Everything else here uses a fake agent. These run the code a real
    run goes through first, short of calling Cursor -- the part that
    failed the first time the page was used."""

    def test_the_converters_cursor_helper_loads(self):
        ca = loop.cursor_assist()
        self.assertTrue(hasattr(ca, "_sdk_prompt"))
        self.assertTrue(hasattr(ca, "redact_for_log"))

    def test_the_key_and_model_are_read_from_its_config(self):
        old = os.environ.get("CURSOR_API_KEY")
        os.environ["CURSOR_API_KEY"] = "key_from_the_environment"
        try:
            _ca, cfg = loop.cursor_config(loop.ROOT)
        finally:
            if old is None:
                del os.environ["CURSOR_API_KEY"]
            else:
                os.environ["CURSOR_API_KEY"] = old
        self.assertEqual(cfg.api_key, "key_from_the_environment")
        self.assertEqual(cfg.cwd, loop.ROOT)
        self.assertTrue(cfg.model)

    def test_setup_runs_to_a_verdict_instead_of_a_traceback(self):
        """With the SDK import faked and no key, setup must end in its own
        FAIL line (exit 1), not an exception."""
        d = tempfile.mkdtemp(prefix="looptest_setup_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        had = "cursor_sdk" in sys.modules
        sys.modules.setdefault("cursor_sdk", types.ModuleType("cursor_sdk"))
        warm = loop.cursor_call.warm_up
        loop.cursor_call.warm_up = lambda ca: (True, "started")
        self.addCleanup(setattr, loop.cursor_call, "warm_up", warm)
        old = os.environ.pop("CURSOR_API_KEY", None)
        orig = loop.cursor_config
        def no_key(root):
            ca, cfg = orig(root)
            cfg.api_key = ""
            return ca, cfg
        loop.cursor_config = no_key
        try:
            self.assertEqual(loop.cmd_setup(d, loop.ROOT), 1)
            loop.cursor_config = orig
            os.environ["CURSOR_API_KEY"] = "key_for_this_test"
            self.assertEqual(loop.cmd_setup(d, loop.ROOT), 0)
        finally:
            loop.cursor_config = orig
            os.environ.pop("CURSOR_API_KEY", None)
            if old is not None:
                os.environ["CURSOR_API_KEY"] = old
            if not had:
                sys.modules.pop("cursor_sdk", None)


class FakeSdk:
    """A stand-in for cursor_sdk with the surface cursor_call.py uses."""

    def __init__(self, messages=(), status="finished", result="done", raise_on_create=None,
                 with_create=True):
        outer = self
        self.options = None
        self.sent = []
        self.closed = False

        class CursorAgentError(Exception):
            pass

        class Run:
            def messages(self_inner):
                return iter(messages)

            def wait(self_inner):
                return types.SimpleNamespace(status=status, result=result, id="run-7")

            def supports(self_inner, what):
                return False

        class Session:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                outer.closed = True
                return False

            def send(self_inner, prompt):
                outer.sent.append(prompt)
                return Run()

        class Agent:
            pass

        def create(options):
            outer.options = options
            if raise_on_create == "startup":
                raise CursorAgentError("401 invalid api key")
            if raise_on_create:
                raise raise_on_create
            return Session()

        if with_create:
            Agent.create = staticmethod(create)
        self.module = types.ModuleType("cursor_sdk")
        self.module.Agent = Agent
        self.module.AgentOptions = lambda **kw: types.SimpleNamespace(**kw)
        self.module.LocalAgentOptions = lambda **kw: types.SimpleNamespace(**kw)
        self.module.CursorAgentError = CursorAgentError


class CallingCursor(unittest.TestCase):
    """The call itself, against a fake SDK: what is sent, what is reported
    while it runs, and how each kind of failure reads."""

    def setUp(self):
        self.saved = {k: sys.modules.get(k) for k in ("cursor_sdk",)}
        self.ca = types.SimpleNamespace(
            _patch_cursor_sdk_bridge_for_windows=lambda: None,
            _sdk_prompt=lambda prompt, cfg: {"status": "finished", "result": "old path", "id": "p1"})
        self.cfg = types.SimpleNamespace(api_key="key_abc", model="composer-2.5",
                                         cwd=os.path.abspath("."))
        self._patch = loop.cursor_call.patch_bridge
        loop.cursor_call.patch_bridge = lambda ca: None

    def tearDown(self):
        loop.cursor_call.patch_bridge = self._patch
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def use(self, fake):
        sys.modules["cursor_sdk"] = fake.module
        return fake

    def test_it_runs_in_agent_mode_in_one_directory_and_closes_the_session(self):
        fake = self.use(FakeSdk())
        out = loop.cursor_call.run("write the test", self.ca, self.cfg)
        self.assertEqual(fake.options.mode, "agent", "plan mode would change nothing")
        self.assertIsInstance(fake.options.local.cwd, str, "a list here crashes inside the SDK")
        self.assertEqual(fake.options.api_key, "key_abc")
        self.assertEqual(fake.sent, ["write the test"])
        self.assertTrue(fake.closed)
        self.assertEqual((out["result"], out["id"], out["transport"]), ("done", "run-7", "session"))

    def test_progress_is_reported_while_the_agent_works(self):
        msgs = [types.SimpleNamespace(type="status", status="running", message=""),
                types.SimpleNamespace(type="tool_call", name="edit_file", status="completed", result="ok"),
                types.SimpleNamespace(type="tool_call", name="run_terminal", status="error", result="denied"),
                types.SimpleNamespace(type="thinking")]
        self.use(FakeSdk(messages=msgs))
        seen = []
        loop.cursor_call.run("p", self.ca, self.cfg, on_event=seen.append)
        self.assertIn("status running", seen)
        self.assertIn("tool edit_file completed", seen)
        self.assertIn("tool run_terminal error: denied", seen)
        self.assertEqual(len(seen), 4, "sent + three describable messages; the fourth has no line")

    def test_a_run_that_never_started_says_so(self):
        self.use(FakeSdk(raise_on_create="startup"))
        with self.assertRaises(loop.cursor_call.CursorCallError) as got:
            loop.cursor_call.run("p", self.ca, self.cfg)
        self.assertEqual(got.exception.kind, "startup")
        self.assertIn("did not start", str(got.exception))
        self.assertIn("not the prompt", str(got.exception))

    def test_a_run_that_failed_half_way_says_that_instead(self):
        msgs = [types.SimpleNamespace(type="tool_call", name="edit_file", status="error", result="disk full")]
        self.use(FakeSdk(messages=msgs, status="error", result="partial"))
        with self.assertRaises(loop.cursor_call.CursorCallError) as got:
            loop.cursor_call.run("p", self.ca, self.cfg)
        self.assertEqual(got.exception.kind, "run")
        self.assertEqual(got.exception.run_id, "run-7")
        self.assertIn("disk full", got.exception.details)

    def test_an_sdk_without_sessions_falls_back_to_the_single_call(self):
        self.use(FakeSdk(with_create=False))
        seen = []
        out = loop.cursor_call.run("p", self.ca, self.cfg, on_event=seen.append)
        self.assertEqual((out["result"], out["transport"]), ("old path", "prompt"))
        self.assertTrue(any("no Agent.create" in s for s in seen))

    def test_no_key_is_a_setup_problem_before_anything_is_imported(self):
        self.cfg.api_key = ""
        with self.assertRaises(loop.cursor_call.CursorCallError) as got:
            loop.cursor_call.run("p", self.ca, self.cfg)
        self.assertEqual(got.exception.kind, "setup")

    def test_the_loop_writes_progress_to_the_cursor_log_and_redacts_the_key(self):
        msgs = [types.SimpleNamespace(type="tool_call", name="edit_file", status="error",
                                      result="could not use key_abc")]
        self.use(FakeSdk(messages=msgs, result="I used key_abc"))
        orig = loop.cursor_config
        loop.cursor_config = lambda root: (types.SimpleNamespace(
            redact_for_log=lambda text, key: str(text).replace(key, "***"),
            _patch_cursor_sdk_bridge_for_windows=lambda: None), self.cfg)
        try:
            seen = []
            out = loop.call_cursor("p", ".", on_event=seen.append)
        finally:
            loop.cursor_config = orig
        self.assertEqual(out["result"], "I used ***")
        self.assertIn("tool edit_file error: could not use ***", seen)

    def test_the_bridge_is_given_time_on_windows(self):
        self.assertGreaterEqual(loop.cursor_call.WINDOWS_BRIDGE_TIMEOUT, 90)

    def test_warm_up_reports_a_bridge_that_will_not_start(self):
        client = types.ModuleType("cursor_sdk._client")
        def boom():
            raise OSError("bridge binary blocked")
        client._default_client = boom
        sys.modules["cursor_sdk"] = types.ModuleType("cursor_sdk")
        sys.modules["cursor_sdk._client"] = client
        try:
            self.assertEqual(loop.cursor_call.warm_up(self.ca), (False, "bridge binary blocked"))
            client._default_client = lambda: object()
            self.assertEqual(loop.cursor_call.warm_up(self.ca), (True, "started"))
            del client._default_client
            ok, why = loop.cursor_call.warm_up(self.ca)
            self.assertIsNone(ok, "an SDK without the entry point is 'not checked', not a failure")
        finally:
            sys.modules.pop("cursor_sdk._client", None)


class SdkBootstrap(unittest.TestCase):
    def test_an_installed_sdk_is_left_alone(self):
        sys.modules["cursor_sdk"] = types.ModuleType("cursor_sdk")
        try:
            calls = []
            self.assertEqual(loop.ensure_sdk(pip=lambda a: calls.append(a)), "present")
            self.assertEqual(calls, [])
        finally:
            del sys.modules["cursor_sdk"]

    def test_a_missing_sdk_is_installed_once(self):
        sys.modules.pop("cursor_sdk", None)
        try:
            import cursor_sdk  # noqa: F401
            self.skipTest("cursor-sdk is really installed here")
        except ImportError:
            pass
        def pip(argv):
            self.assertIn("pip", argv)
            self.assertIn("install", argv)
            sys.modules["cursor_sdk"] = types.ModuleType("cursor_sdk")
            return types.SimpleNamespace(returncode=0, stdout="")
        try:
            self.assertEqual(loop.ensure_sdk(pip=pip), "installed")
        finally:
            sys.modules.pop("cursor_sdk", None)

    def test_a_failed_install_says_what_to_do_instead_of_a_traceback(self):
        sys.modules.pop("cursor_sdk", None)
        try:
            import cursor_sdk  # noqa: F401
            self.skipTest("cursor-sdk is really installed here")
        except ImportError:
            pass
        pip = lambda argv: types.SimpleNamespace(
            returncode=1, stdout="ERROR: Could not find a version that satisfies")
        with self.assertRaises(RuntimeError) as got:
            loop.ensure_sdk(pip=pip)
        self.assertIn("pip install cursor-sdk", str(got.exception))
        self.assertIn("Could not find a version", str(got.exception))


if __name__ == "__main__":
    unittest.main(verbosity=1)
