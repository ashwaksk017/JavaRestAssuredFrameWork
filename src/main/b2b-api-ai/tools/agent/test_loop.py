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
        self.assertIn("reconvert goal", reason)
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
        self.assertIn("reconvert goal", self.state()["reason"])
        with io.open(os.path.join(self.root, GEN + "amex/Hooks.java")) as fh:
            self.assertEqual(fh.read(), "class Hooks { int amex; }\n",
                             "the in-scope edit of the failed attempt is undone")

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
