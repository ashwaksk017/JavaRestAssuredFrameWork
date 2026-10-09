"""The stand-ins answer like the services, and never pass for them.

    python tools/agent/test_mock.py

Each test switches the stand-ins on for itself (WORKBENCH_MOCK=1) and
off again. Nothing reaches a network; the mock Jira's memory of writes
is kept in a temporary directory.
"""
from __future__ import annotations

import os as _os
_os.environ["WORKBENCH_MOCK"] = "0"      # off unless a test turns it on

import importlib.util
import io
import json
import os
import shutil
import subprocess
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


mock = _load("mock_under_test", os.path.join(HERE, "mock.py"))
defects = _load("defects_for_mock", os.path.join(HERE, "defects.py"))
search = defects.search
design = defects.design
loop = defects.loop


class Mocked(unittest.TestCase):
    """Stand-ins on, and everything they write kept in a temp directory."""
    ON = "1"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mocktest_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.environ["WORKBENCH_MOCK"] = self.ON
        self.addCleanup(os.environ.__setitem__, "WORKBENCH_MOCK", "0")
        saved = (search.ROOT, defects.ROOT, search.mock().ROOT, list(search._MOCKED))
        search.ROOT = defects.ROOT = search.mock().ROOT = self.root

        def restore():
            search.ROOT, defects.ROOT, search.mock().ROOT = saved[:3]
            search._MOCKED[:] = saved[3]
        self.addCleanup(restore)

    def run_main(self, fn, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fn(list(argv))
        return rc, out.getvalue()

    def result(self, name, job="j1"):
        with io.open(os.path.join(self.root, "target", "agent", job, name), encoding="utf-8") as fh:
            return json.load(fh)


class TheSwitch(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp(prefix="mockcfg_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "mock.json")
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def setUp(self):
        self.env = os.environ.pop("WORKBENCH_MOCK", None)
        self.addCleanup(os.environ.__setitem__, "WORKBENCH_MOCK", "0")

    def test_the_file_decides_and_each_service_has_its_own_switch(self):
        self.assertEqual(mock.switches(self.write('{"jira": true, "cursor": false}')),
                         {"jira": True, "cursor": False})

    def test_no_file_means_everything_is_real(self):
        self.assertEqual(mock.switches(os.path.join(tempfile.gettempdir(), "no-such-mock.json")),
                         {"jira": False, "cursor": False})

    def test_a_value_that_is_neither_true_nor_false_is_an_error_not_a_guess(self):
        for text in ('{"jira": "true", "cursor": true}', '{"jira": 1, "cursor": true}',
                     '{"jira": "yes", "cursor": true}', '{"jira": null, "cursor": true}',
                     '{"jira": true, "cursor": "false"}', "{ not json", "[]", '"x"', ""):
            with self.assertRaises(mock.MockConfigError, msg=text):
                mock.switches(self.write(text))

    def test_a_typo_never_quietly_selects_the_real_service(self):
        for text, word in (('{"jira": true, "cursur": true}', "cursur"),
                           ('{"Jira": true, "Cursor": true}', "does not know"),
                           ('{"jira": true}', 'no "cursor" switch'),
                           ('{"jira": true, "cursor": true, "confluence": true}', "confluence")):
            with self.assertRaises(mock.MockConfigError) as got:
                mock.switches(self.write(text))
            self.assertIn(word, str(got.exception), text)
        for env in ("true", "false", "off", "yes", "2"):
            os.environ["WORKBENCH_MOCK"] = env
            with self.assertRaises(mock.MockConfigError, msg=env):
                mock.switches(self.write('{"jira": true, "cursor": true}'))
        os.environ["WORKBENCH_MOCK"] = " 1 "
        self.assertTrue(mock.switches()["jira"])

    def test_the_environment_overrides_the_file_for_one_process(self):
        path = self.write('{"jira": true, "cursor": true}')
        os.environ["WORKBENCH_MOCK"] = "0"
        self.assertEqual(mock.switches(path), {"jira": False, "cursor": False})
        os.environ["WORKBENCH_MOCK"] = "1"
        self.assertEqual(mock.switches(self.write('{"jira": false, "cursor": false}')),
                         {"jira": True, "cursor": True})

    def test_as_shipped_the_file_is_valid(self):
        os.environ.pop("WORKBENCH_MOCK", None)
        got = mock.switches()
        self.assertEqual(sorted(got), ["cursor", "jira"])
        self.assertTrue(all(v in (True, False) for v in got.values()))


class JiraIsAStandIn(Mocked):
    def test_no_configuration_host_or_token_is_needed(self):
        saved = search.projectconfig.section
        search.projectconfig.section = lambda name: (_ for _ in ()).throw(
            AssertionError("the configuration must not be read in mock mode"))
        self.addCleanup(setattr, search.projectconfig, "section", saved)
        conn = search.connect()
        self.assertTrue(conn["mock"])
        self.assertTrue(conn["base"].endswith(".invalid"), "a host that cannot exist")

    def test_every_jira_command_works_and_says_it_is_a_mock(self):
        rc, out = self.run_main(search.main, "verify", "--job", "j1")
        self.assertEqual(rc, 0)
        self.assertIn("MOCK JIRA", out)
        r = self.result("jira-result.json")
        self.assertEqual((r["kind"], r["ok"], r["mock"]), ("verify", True, True))
        self.assertIn("MOCK USER", r["message"])

        rc, out = self.run_main(search.main, "versions", "--project", "abc", "--job", "j1")
        r = self.result("jira-result.json")
        self.assertEqual([v["name"] for v in r["versions"]],
                         ["Release 2.0", "Release 1.1", "Release 1.0"])
        self.assertTrue(r["mock"])

        rc, out = self.run_main(search.main, "fix-version", "--project", "abc", "--version",
                                "Release 2.0", "--compare", "Release 1.1", "--job", "j1")
        r = self.result("jira-result.json")
        self.assertEqual(rc, 0)
        self.assertTrue(r["delta"]["reliable"])
        self.assertTrue(all(i["key"].startswith("ABC-") for i in r["result"]["issues"]),
                        "it answers for the project that was asked about")
        self.assertTrue(r["delta"]["added"] and r["delta"]["removed"])

        rc, out = self.run_main(search.main, "tests", "--project", "abc", "--job", "j1")
        r = self.result("jira-result.json")
        self.assertEqual((rc, len(r["issues"]), r["issues"][0]["type"]), (0, 3, "Test"))
        self.assertTrue(all(i["summary"].startswith("[MOCK]") for i in r["issues"]))

        rc, out = self.run_main(search.main, "paste", "--text", "xyz-1, XYZ-101", "--job", "j1")
        r = self.result("jira-result.json")
        self.assertEqual([i["key"] for i in r["issues"]], ["XYZ-1", "XYZ-101"])

    def test_the_real_guards_still_apply_to_the_stand_in(self):
        rc, out = self.run_main(search.main, "paste", "--text",
                                "the status in the response is wrong", "--job", "j1")
        self.assertEqual(rc, 2)
        rc, out = self.run_main(search.main, "tests", "--project", "abc", "--max", "2", "--job", "j1")
        self.assertEqual(rc, 1, "a list cut at the limit is still a failing exit")
        self.assertTrue(self.result("jira-result.json")["limited"])

    def test_the_stand_in_refuses_what_it_has_no_answer_for(self):
        with self.assertRaises(ValueError):
            mock.jira_transport("DELETE", "https://x/rest/api/2/issue/ABC-1", "t", 5)

    def test_a_query_the_stand_in_cannot_answer_is_refused_not_answered_with_everything(self):
        for jql in ('project = ABC AND status = "Closed"', "assignee = currentUser()",
                    "issuetype in (Bug, Story)", "key = ABC-101"):
            rc, out = self.run_main(search.main, "search", "--jql", jql, "--job", "j1")
            self.assertEqual(rc, 1, jql)
            r = self.result("jira-result.json")
            self.assertEqual(r["kind"], "error", jql)
            self.assertIn("MOCK Jira understands only", r["message"])
        rc, out = self.run_main(search.main, "search", "--jql",
                                'project = ABC AND fixVersion = "Release 1"', "--job", "j1")
        self.assertEqual(self.result("jira-result.json")["total"], 0, "a version is matched exactly")

    def test_the_files_under_target_say_mock_and_do_not_replace_the_real_ones(self):
        self.run_main(search.main, "versions", "--project", "abc", "--job", "j1")
        d = os.path.join(self.root, "target", "jira", "search")
        self.assertEqual(os.listdir(d), ["MOCK-ABC-versions.json"])
        with io.open(os.path.join(d, "MOCK-ABC-versions.json"), encoding="utf-8") as fh:
            self.assertIs(json.load(fh)["mock"], True)

    def test_a_pasted_jira_address_is_taken_for_what_is_in_it(self):
        rc, out = self.run_main(search.main, "paste", "--text",
                                "https://jira.example.com/browse/ABC-101", "--job", "j1")
        self.assertEqual(rc, 0, out)
        self.assertEqual([i["key"] for i in self.result("jira-result.json")["issues"]], ["ABC-101"])


class DefectsAgainstTheStandIns(Mocked):
    def test_load_suggest_and_write_all_work_and_the_write_stays_on_this_machine(self):
        rc, out = self.run_main(defects.main, "load", "--job", "j1", "--project", "abc",
                                "--version", "Release 2.0")
        self.assertEqual(rc, 0)
        r = self.result("defects-result.json")
        self.assertEqual((r["mock_jira"], r["write_back"], r["field"]["id"]),
                         (True, True, mock.FIELD_ID))
        by = {d["key"]: d for d in r["defects"]}
        self.assertEqual(sorted(by), ["ABC-101", "ABC-102", "ABC-103"])
        self.assertEqual(by["ABC-101"]["current"], "Test data")
        self.assertEqual(by["ABC-101"]["precedents"][0]["key"], "ABC-150")

        rc, out = self.run_main(defects.main, "suggest", "--job", "j1")
        self.assertEqual(rc, 0)
        self.assertIn("MOCK CURSOR", out)
        r = self.result("defects-result.json")
        by = {d["key"]: d for d in r["defects"]}
        self.assertTrue(r["mock_cursor"])
        self.assertEqual(by["ABC-101"]["suggested"], "Code defect")
        self.assertEqual(by["ABC-101"]["flag"], "DIFFERS from the current reason")
        self.assertIn("MOCK", by["ABC-101"]["why"])
        self.assertEqual(by["ABC-103"]["flag"], "agrees with the current reason")

        rc, out = self.run_main(defects.main, "apply", "--job", "j1", "--key", "ABC-101",
                                "--reason", "Code defect", "--confirm", "ABC-101")
        self.assertEqual(rc, 0)
        self.assertIn("Jira now holds 'Code defect'", out)
        store = os.path.join(self.root, "target", "agent", "mock-jira.json")
        self.assertEqual(json.load(io.open(store, encoding="utf-8")),
                         {"ABC-101": {"value": "Code defect"}})
        # ...and it is still there the next time the bugs are loaded.
        self.run_main(defects.main, "load", "--job", "j1", "--project", "abc",
                      "--version", "Release 2.0")
        by = {d["key"]: d for d in self.result("defects-result.json")["defects"]}
        self.assertEqual(by["ABC-101"]["current"], "Code defect")

    def test_what_was_loaded_from_the_stand_in_is_never_written_to_the_real_jira(self):
        """ABC-101 in the sample data and ABC-101 in a real project are
        different bugs. Flipping the switch between Load and Write must
        not carry one's reason over to the other."""
        self.run_main(defects.main, "load", "--job", "j1", "--project", "abc")
        self.run_main(defects.main, "suggest", "--job", "j1")
        os.environ["WORKBENCH_MOCK"] = "0"             # the switch is turned to real
        sent = []
        real_conn = {"base": "https://jira.example.com", "bases": ["https://jira.example.com"],
                     "token": "tok", "timeout": 5, "api": "/rest/api/2", "cleartext": False}
        real_cfg = {"defects": {"field": mock.FIELD_ID, "reasons": mock.REASONS,
                                "write_back": True}}
        with self.assertRaises(defects.Refused) as got:
            defects.cmd_apply("j1", "ABC-101", "Code defect", "ABC-101",
                              transport=lambda *a, **k: sent.append(a), cfg=real_cfg,
                              conn=real_conn)
        self.assertIn("loaded from the MOCK Jira", str(got.exception))
        self.assertEqual(sent, [], "not one request reached the real Jira")
        with self.assertRaises(defects.Refused):
            with redirect_stdout(io.StringIO()):
                defects.cmd_suggest("j1", None, agent=lambda p, f: {"result": "{}"},
                                    root=self.root)

    def test_a_stand_ins_suggestion_is_not_written_to_a_real_bug(self):
        """Jira real, Cursor mock: the keyword match must not ride into Jira."""
        fid = mock.FIELD_ID
        bug = {"key": "ABC-1", "fields": {
            "summary": "times out in stage", "description": "SocketTimeoutException 504",
            "status": {"name": "Open"}, "issuetype": {"name": "Bug"}, "priority": {"name": "P"},
            "components": [], "labels": [], fid: None}}
        sent = []

        def real_jira(method, url, token, timeout, body=None):
            sent.append(method)
            path = url.split("/rest/api/2", 1)[1]
            if path.endswith("/editmeta"):
                return {"fields": {fid: {"schema": {"type": "option"},
                                         "allowedValues": [{"value": r} for r in mock.REASONS]}}}
            if method == "POST":
                return {"issues": [] if "is not EMPTY" in body["jql"] else [bug], "total": 1}
            return {"key": "ABC-1", "fields": {fid: bug["fields"][fid]}}
        conn = {"base": "https://jira.example.com", "bases": ["https://jira.example.com"],
                "token": "tok", "timeout": 5, "api": "/rest/api/2", "cleartext": False}
        cfg = {"defects": {"field": fid, "reasons": mock.REASONS, "write_back": True}}
        defects.cmd_load("j1", text="ABC-1", transport=real_jira, cfg=cfg, conn=conn)
        os.environ["WORKBENCH_MOCK"] = "1"
        saved = search.mock_jira
        search.mock_jira = lambda: False               # jira: false, cursor: true
        self.addCleanup(setattr, search, "mock_jira", saved)
        with redirect_stdout(io.StringIO()):
            state = defects.cmd_suggest("j1", None, root=self.root)
        d = state["defects"][0]
        self.assertEqual((d["suggested"], d["suggested_by"], state["mock_cursor"]),
                         ("Environment", "mock", True))
        with self.assertRaises(defects.Refused) as got:
            defects.cmd_apply("j1", "ABC-1", "Environment", "ABC-1", transport=real_jira,
                              cfg=cfg, conn=conn)
        self.assertIn("MOCK Cursor", str(got.exception))
        self.assertNotIn("PUT", sent)
        # A reason the person chose themselves is theirs to write.
        defects.cmd_apply("j1", "ABC-1", "Code defect", "ABC-1", transport=real_jira,
                          cfg=cfg, conn=conn)
        self.assertIn("PUT", sent)

    def test_the_write_guards_are_the_real_ones(self):
        self.run_main(defects.main, "load", "--job", "j1", "--project", "abc")
        for argv in (("--key", "ABC-101", "--reason", "Code defect", "--confirm", "ABC-102"),
                     ("--key", "ABC-999", "--reason", "Code defect", "--confirm", "ABC-999"),
                     ("--key", "ABC-101", "--reason", "Made up", "--confirm", "ABC-101")):
            rc, out = self.run_main(defects.main, "apply", "--job", "j1", *argv)
            self.assertEqual(rc, 2, argv)
        self.assertFalse(os.path.exists(os.path.join(self.root, "target", "agent", "mock-jira.json")))


class DesignAgainstTheStandIn(Mocked):
    SPEC = {"openapi": "3.0.1", "paths": {"/groups/{id}/rates": {"get": {}, "post": {}},
                                          "/health": {"get": {}}}}

    def test_a_design_is_produced_marked_mock_and_no_sdk_or_key_is_touched(self):
        saved = (loop.ensure_sdk, loop.cursor_config)
        boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Cursor must not be set up"))
        loop.ensure_sdk = loop.cursor_config = boom
        self.addCleanup(lambda: (setattr(loop, "ensure_sdk", saved[0]),
                                 setattr(loop, "cursor_config", saved[1])))
        out = io.StringIO()
        with redirect_stdout(out):
            rc = design.design("j1", swagger=json.dumps(self.SPEC), root=self.root, speed="fast")
        self.assertEqual(rc, 0)
        self.assertIn("MOCK CURSOR", out.getvalue())
        d = self.result("design.json")
        self.assertTrue(d["mock"])
        self.assertEqual(len(d["test_cases"]), 6, "two per endpoint")
        self.assertTrue(all(c["endpoint_in_spec"] for c in d["test_cases"]))
        self.assertTrue(all(c["title"].startswith("[MOCK]") for c in d["test_cases"]))
        md = io.open(os.path.join(self.root, "target", "agent", "j1", "design.md"),
                     encoding="utf-8").read()
        self.assertIn("MOCK. This is not a design.", md)

    def test_a_mock_design_is_not_handed_to_the_agent_as_proposed_test_cases(self):
        with redirect_stdout(io.StringIO()):
            design.design("j1", swagger=json.dumps(self.SPEC), root=self.root)
        text, note = loop.designed_cases(os.path.join(self.root, "target", "agent", "j1"))
        self.assertEqual(text, "")
        self.assertIn("is a MOCK", note)

    def test_ask_again_in_mock_mode_keeps_what_cursor_was_paid_for(self):
        out = os.path.join(self.root, "target", "agent", "j1")
        design.cache_put(out, "a" * 64, '{"test_cases": []}', "composer-2.5")
        design.cache_put(out, "b" * 64, '{"test_cases": []}', "mock")
        with redirect_stdout(io.StringIO()):
            design.design("j1", swagger=json.dumps(self.SPEC), root=self.root, fresh=True)
        kept = os.listdir(os.path.join(out, design.CACHE_DIR))
        self.assertIn("a" * 64 + ".json", kept)
        self.assertNotIn("b" * 64 + ".json", kept)

    def test_a_mock_design_is_never_handed_back_later_as_what_cursor_said(self):
        with redirect_stdout(io.StringIO()):
            design.design("j1", swagger=json.dumps(self.SPEC), root=self.root)
        os.environ["WORKBENCH_MOCK"] = "0"
        asked = []

        def real(prompt, followup):
            asked.append(1)
            return {"result": json.dumps({"test_cases": [{
                "title": "real", "endpoint": "GET /health",
                "steps": [{"action": "a", "expected": "200"}]}]})}
        with redirect_stdout(io.StringIO()):
            design.design("j1", swagger=json.dumps(self.SPEC), root=self.root, agent=real)
        self.assertEqual(asked, [1], "the real path asked; it did not reuse the mock's reply")
        self.assertFalse(self.result("design.json")["mock"])


class TheAgentLoopAgainstTheStandIn(Mocked):
    def setUp(self):
        super().setUp()
        self.repo = tempfile.mkdtemp(prefix="mockloop_")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        git = lambda *a: subprocess.run(["git", *a], cwd=self.repo, check=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        for rel, text in ((".gitignore", "target/\n"), ("README.md", "x\n")):
            with io.open(os.path.join(self.repo, rel), "w") as fh:
                fh.write(text)
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        self.job = os.path.join(self.repo, "target", "agent", "j1")
        os.makedirs(self.job)
        for name in ("plan.md", "brief.md"):
            with io.open(os.path.join(self.job, name), "w") as fh:
                fh.write("# plan\ncreate POST /x\n")
        self.policy = loop.load_policy(os.path.join(self.repo, "no-policy.json"))
        self.ok = lambda: {"ok": True, "steps": [{"name": "compile", "rc": 0, "tail": ""}]}

    def generate(self, scope="new-test"):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = loop.cmd_generate(self.job, self.repo, self.policy, scope, "",
                                   agent=None, verifier=self.ok)
        return rc, out.getvalue()

    def test_a_placeholder_is_written_held_for_review_and_can_only_be_discarded(self):
        rc, out = self.generate()
        self.assertEqual(rc, 0, out)
        self.assertIn("MOCK CURSOR", out)
        review = loop.read_review(self.job)
        self.assertEqual((review["state"], review["mock"]), ("pending-review", True))
        self.assertEqual(review["files"]["created"], [mock.SAMPLE_TEST])
        body = io.open(os.path.join(self.repo, mock.SAMPLE_TEST), encoding="utf-8").read()
        self.assertIn("MOCK", body)
        self.assertIn("enabled = false", body)

        out = io.StringIO()
        with redirect_stdout(out):
            rc = loop.cmd_approve(self.job, self.repo, self.policy, "j1", "j1")
        self.assertEqual(rc, 1)
        self.assertIn("made by the MOCK agent", out.getvalue())
        self.assertEqual(loop.read_review(self.job)["state"], "pending-review")

        with redirect_stdout(io.StringIO()):
            self.assertEqual(loop.cmd_discard(self.job, self.repo, self.policy), 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, mock.SAMPLE_TEST)))

    def test_the_mock_agent_writes_over_nothing(self):
        path = os.path.join(self.repo, mock.SAMPLE_TEST)
        os.makedirs(os.path.dirname(path))
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("// mine\n")
        said = mock.loop_agent(self.repo)("p")["result"]
        self.assertIn("Nothing was written", said)
        self.assertEqual(io.open(path, encoding="utf-8").read(), "// mine\n")

    def test_a_mock_run_cannot_be_approved_even_after_the_switch_is_turned_off(self):
        self.generate()
        os.environ["WORKBENCH_MOCK"] = "0"
        out = io.StringIO()
        with redirect_stdout(out):
            rc = loop.cmd_approve(self.job, self.repo, self.policy, "j1", "j1")
        self.assertEqual(rc, 1)
        self.assertIn("MOCK agent", out.getvalue())

    def test_setup_says_cursor_is_simulated_and_asks_for_nothing(self):
        saved = loop.ensure_sdk
        loop.ensure_sdk = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no install"))
        self.addCleanup(setattr, loop, "ensure_sdk", saved)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = loop.cmd_setup(self.job, self.repo)
        self.assertEqual(rc, 0)
        self.assertIn("MOCK CURSOR is switched on", out.getvalue())


class SwitchedOff(Mocked):
    ON = "0"

    def test_with_the_switches_off_the_real_configuration_is_what_is_read(self):
        asked = []
        saved = search.projectconfig.section
        search.projectconfig.section = lambda name: (asked.append(name), ({}, "test"))[1]
        self.addCleanup(setattr, search.projectconfig, "section", saved)
        with self.assertRaises(search.Refused):
            search.connect()
        self.assertEqual(asked, ["jira_config"])
        with self.assertRaises(defects.Refused):
            defects.settings()
        self.assertFalse(loop.mock_cursor())


class TheServerSaysWhichAreStandIns(unittest.TestCase):
    def test_the_page_is_told_and_a_broken_file_counts_as_on(self):
        jobs = _load("jobs_for_mock", os.path.join(HERE, "jobs.py"))
        os.environ["WORKBENCH_MOCK"] = "1"
        self.addCleanup(os.environ.__setitem__, "WORKBENCH_MOCK", "0")
        self.assertEqual(jobs.mock_state(), {"jira": True, "cursor": True, "error": ""})
        os.environ["WORKBENCH_MOCK"] = "0"
        self.assertEqual(jobs.mock_state(), {"jira": False, "cursor": False, "error": ""})


if __name__ == "__main__":
    unittest.main(verbosity=1)
