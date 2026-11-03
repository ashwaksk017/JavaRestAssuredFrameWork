"""The issue branch on GitLab and its merge request (issuebranch.py)."""
from __future__ import annotations

import contextlib
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


ib = _load("issuebranch_under_test", os.path.join(HERE, "issuebranch.py"))
CFG = {"base_url": "https://gitlab.example.com", "project": "qa/api-tests", "target": "develop",
       "token": "glpat-test-token-1234"}


class GitLab:
    """A fake GitLab: branches by name, merge requests by source branch."""

    def __init__(self, branches=("develop",), mrs=None):
        self.branches = set(branches)
        self.mrs = list(mrs or [])
        self.calls = []

    def __call__(self, method, url, token, body=None):
        self.calls.append((method, url, token, body))
        assert url.startswith("https://gitlab.example.com/api/v4/projects/qa%2Fapi-tests/"), url
        path = url.split("qa%2Fapi-tests", 1)[1]
        if method == "GET" and path.startswith("/repository/branches/"):
            name = path.rsplit("/", 1)[1].replace("%2F", "/")
            return (200, {"name": name, "commit": {"id": "abc"}}) if name in self.branches else (404, {})
        if method == "POST" and path.startswith("/repository/branches?"):
            q = dict(p.split("=") for p in path.split("?", 1)[1].split("&"))
            name, ref = q["branch"].replace("%2F", "/"), q["ref"]
            if ref not in self.branches:
                return 400, {"message": "Invalid reference name: " + ref}
            self.branches.add(name)
            return 201, {"name": name, "commit": {"id": "def"}}
        if method == "GET" and path.startswith("/merge_requests?"):
            return 200, [m for m in self.mrs if f"source_branch={m['source'].replace('/', '%2F')}" in path]
        if method == "POST" and path == "/merge_requests":
            mr = {"iid": 7, "web_url": "https://gitlab.example.com/qa/api-tests/-/merge_requests/7",
                  "source": body["source_branch"]}
            self.mrs.append(mr)
            return 201, mr
        return 500, {"message": "unexpected " + method + " " + path}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="issuebranch_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.job = os.path.join(self.root, ".workbench", "j1")
        os.makedirs(self.job)
        self.write("intake.json", {"story": {"key": "B2B-1234", "summary": "Owners see their stays"}})
        saved = ib.config
        ib.config = lambda root="", project="", block=None: dict(CFG)
        self.addCleanup(setattr, ib, "config", saved)

    def write(self, name, data):
        with io.open(os.path.join(self.job, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def read(self, name):
        with io.open(os.path.join(self.job, name), encoding="utf-8") as fh:
            return json.load(fh)

    def quiet(self, fn, *a, **kw):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            r = fn(*a, **kw)
        return r, out.getvalue()


class Naming(unittest.TestCase):
    def test_issue_by_default_feature_as_a_choice(self):
        self.assertEqual(ib.branch_name("issue/", "b2b-1234"), "issue/B2B-1234")
        self.assertEqual(ib.branch_name("feature/", "B2B-1234"), "feature/B2B-1234")

    def test_no_other_prefix_and_no_key_no_branch(self):
        for prefix, key in (("hotfix/", "B2B-1"), ("issue/", ""), ("issue/", "not a key")):
            with self.assertRaises(ib.Refused):
                ib.branch_name(prefix, key)

    def test_the_configuration_needs_its_address_project_and_token(self):
        with self.assertRaises(ib.Refused) as e:
            ib.config(block={"testrepo_config": {"base_url": "https://gitlab.example.com"}})
        self.assertIn("the project path", str(e.exception))
        self.assertEqual(ib.config(block={"testrepo_config": dict(CFG, target="")})["target"], "develop")
        with self.assertRaises(ib.Refused):
            ib.config(block={"testrepo_config": dict(CFG, base_url="http://gitlab.example.com")})


class Start(Base):
    def test_made_on_gitlab_from_develop_then_switched_here(self):
        gl, switched = GitLab(), []
        rec, said = self.quiet(ib.cmd_start, "j1", "issue/", self.root, transport=gl,
                               local=lambda root, name: switched.append(name) or f"on {name} now")
        self.assertIn("issue/B2B-1234", gl.branches)
        self.assertEqual(switched, ["issue/B2B-1234"])
        self.assertEqual((rec["created"], rec["local"]), (True, "issue/B2B-1234"))
        self.assertEqual(self.read(ib.BRANCH_FILE)["branch"], "issue/B2B-1234")
        self.assertTrue(all(c[2] == CFG["token"] for c in gl.calls))
        self.assertIn("created on GitLab from develop", said)

    def test_a_typed_name_takes_a_sub_task_key(self):
        gl = GitLab()
        rec, said = self.quiet(ib.cmd_start, "j1", "issue/", self.root, transport=gl,
                               local=lambda root, name: "on it", name="feature/b2b-1235")
        self.assertEqual(rec["branch"], "feature/B2B-1235")
        self.assertIn("feature/B2B-1235", gl.branches)
        for bad in ("B2B-1235", "hotfix/B2B-1235", "issue/not-a-key", "issue/develop"):
            with self.assertRaises(ib.Refused):
                self.quiet(ib.cmd_start, "j1", "issue/", self.root, transport=gl,
                           local=lambda root, name: "", name=bad)

    def test_an_existing_branch_is_reused(self):
        gl = GitLab(branches=("develop", "issue/B2B-1234"))
        rec, said = self.quiet(ib.cmd_start, "j1", "issue/", self.root, transport=gl,
                               local=lambda root, name: "on it")
        self.assertFalse(rec["created"])
        self.assertFalse(any(c[0] == "POST" for c in gl.calls), "nothing made twice")
        self.assertIn("this job adds to it", said)

    def test_a_dirty_tree_keeps_the_branch_on_gitlab_and_says_so(self):
        def refuse(root, name):
            raise RuntimeError("2 tracked file(s) are changed")
        rec, said = self.quiet(ib.cmd_start, "j1", "issue/", self.root, transport=GitLab(), local=refuse)
        self.assertEqual(rec["local"], "")
        self.assertIn("tracked file(s) are changed", rec["local_note"])
        self.assertIn("WARN not switched here", said)
        self.assertEqual(ib.issue_branch(self.job), "issue/B2B-1234", "Approve still pushes to it")

    def test_no_develop_on_gitlab_is_refused(self):
        with self.assertRaises(ib.Refused) as e:
            self.quiet(ib.cmd_start, "j1", "issue/", self.root, transport=GitLab(branches=()),
                       local=lambda r, n: "")
        self.assertIn("no branch develop", str(e.exception))

    def test_a_token_without_rights_is_said_plainly(self):
        def denied(method, url, token, body=None):
            return 401, {"message": "401 Unauthorized"}
        with self.assertRaises(ib.Refused) as e:
            self.quiet(ib.cmd_start, "j1", "issue/", self.root, transport=denied, local=lambda r, n: "")
        self.assertIn("api scope", str(e.exception))


class MergeRequest(Base):
    def ready(self, ok=True, state="pushed", branch="issue/B2B-1234"):
        self.write(ib.BRANCH_FILE, {"branch": "issue/B2B-1234", "local": "issue/B2B-1234"})
        self.write("review.json", {"state": state, "branch": branch, "commit": "0123456789abcdef",
                                   "files": {"created": ["src/test/java/X.java"]},
                                   "execution": {"ok": ok, "passed": 5, "failed": 0 if ok else 2,
                                                 "env": "stg", "at": "2026-11-02T10:00:00Z"}})

    def test_raised_after_a_green_run_is_pushed(self):
        self.ready()
        gl = GitLab()
        rec, said = self.quiet(ib.cmd_mr, "j1", self.root, transport=gl)
        post = [c for c in gl.calls if c[0] == "POST"][0][3]
        self.assertEqual((post["source_branch"], post["target_branch"]), ("issue/B2B-1234", "develop"))
        self.assertEqual(post["title"], "B2B-1234: Owners see their stays")
        self.assertIn("5 passed, 0 failed", post["description"])
        self.assertIn("src/test/java/X.java", post["description"], "the change's files are listed")
        self.assertTrue(rec["made"])
        self.assertEqual(self.read(ib.MR_FILE)["iid"], 7)

    def test_an_open_one_is_shown_not_made_twice(self):
        self.ready()
        gl = GitLab(mrs=[{"iid": 3, "web_url": "https://gitlab.example.com/x/3", "source": "issue/B2B-1234"}])
        rec, said = self.quiet(ib.cmd_mr, "j1", self.root, transport=gl)
        self.assertFalse(rec["made"])
        self.assertEqual(rec["iid"], 3)
        self.assertFalse(any(c[0] == "POST" for c in gl.calls))

    def test_waits_for_the_push_and_a_green_run(self):
        for kw, words in (({"state": "pending-review"}, "not pushed"), ({"branch": "feature_B2B-1234"}, "not pushed"),
                          ({"ok": False}, "green run")):
            self.ready(**kw)
            with self.assertRaises(ib.Refused) as e:
                self.quiet(ib.cmd_mr, "j1", self.root, transport=GitLab())
            self.assertIn(words, str(e.exception))

    def test_no_issue_branch_no_mr(self):
        with self.assertRaises(ib.Refused):
            self.quiet(ib.cmd_mr, "j1", self.root, transport=GitLab())


class Redirects(unittest.TestCase):
    """urllib would carry PRIVATE-TOKEN to wherever a 30x points."""

    def test_a_redirect_is_never_followed(self):
        self.assertIsNone(ib._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://sso.example.org/"))

    def test_a_redirect_is_said_as_such(self):
        with self.assertRaises(ib.Refused) as e:
            ib.ensure_branch(CFG, "issue/B2B-1", transport=lambda m, u, t, b=None: (302, None))
        self.assertIn("redirect", str(e.exception))


class TheTokenStaysHome(unittest.TestCase):
    def test_sent_only_to_the_configured_address(self):
        seen = []
        ib.call(CFG, "GET", "/repository/branches/x", transport=lambda m, u, t, b=None: seen.append(u) or (200, {}))
        self.assertTrue(seen[0].startswith("https://gitlab.example.com/api/v4/projects/qa%2Fapi-tests/"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
