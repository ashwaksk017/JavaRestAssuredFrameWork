"""The repository as the page sees it, and a checkout that cannot lose work.

    python tools/agent/test_repo.py
"""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("repo_under_test", os.path.join(HERE, "repo.py"))
repo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repo)


def sh(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")
    return r.stdout.decode("utf-8", "replace").strip()


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class WithARepo(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="repo-test-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.remote = os.path.join(self.base, "remote.git")
        sh(self.base, "init", "-q", "--bare", self.remote)
        self.root = os.path.join(self.base, "work")
        sh(self.base, "clone", "-q", self.remote, self.root)
        sh(self.root, "config", "user.email", "t@example.com")
        sh(self.root, "config", "user.name", "t")
        sh(self.root, "checkout", "-q", "-b", "main")
        write(self.root, "README.md", "x\n")
        sh(self.root, "add", "-A")
        sh(self.root, "commit", "-q", "-m", "base")
        sh(self.root, "push", "-q", "-u", "origin", "main")
        sh(self.root, "branch", "feature/one")
        sh(self.root, "push", "-q", "origin", "feature/one")
        sh(self.root, "checkout", "-q", "-b", "local-only")
        sh(self.root, "checkout", "-q", "main")
        sh(self.root, "fetch", "-q", "origin")
        # A remote address with a credential in it, as people write them.
        # Set AFTER the fetch: nothing here reaches the network.
        sh(self.root, "remote", "set-url", "origin", "https://user:tok-SECRET@git.example.test/team/repo.git")


class Info(WithARepo):
    def test_it_says_where_the_tree_is_and_shows_the_remote_without_credentials(self):
        r = repo.info(self.root)
        self.assertTrue(r["is_repo"])
        self.assertEqual(r["branch"], "main")
        self.assertTrue(r["clean"])
        self.assertEqual(r["remote_url"], "https://git.example.test/team/repo.git")
        self.assertNotIn("tok-SECRET", str(r))
        self.assertEqual(sorted(r["local_branches"]), ["feature/one", "local-only", "main"])
        self.assertEqual(sorted(r["remote_branches"]), ["feature/one", "main"])
        self.assertEqual(r["upstream"], "origin/main")
        self.assertEqual((r["ahead"], r["behind"]), (0, 0))

    def test_a_changed_tree_is_not_clean_and_a_commit_is_counted_as_ahead(self):
        write(self.root, "new.txt", "y\n")
        r = repo.info(self.root)
        self.assertEqual((r["clean"], r["dirty"]), (False, 1))
        self.assertEqual((r["untracked"], r["modified"], r["switchable"]), (["new.txt"], [], True),
                         "an untracked file is named, and does not stand in the way of a switch")
        sh(self.root, "add", "-A")
        sh(self.root, "commit", "-q", "-m", "more")
        self.assertEqual((repo.info(self.root)["ahead"], repo.info(self.root)["clean"]), (1, True))

    def test_not_a_repository_is_said_not_raised(self):
        plain = tempfile.mkdtemp(prefix="repo-plain-")
        self.addCleanup(shutil.rmtree, plain, True)
        r = repo.info(plain)
        self.assertFalse(r["is_repo"])
        self.assertIn("not a git work tree", r["note"])

    def test_the_ssh_form_of_a_remote_loses_its_user(self):
        self.assertEqual(repo._shown_remote("git@git.example.test:team/repo.git"),
                         "git.example.test:team/repo.git")
        self.assertEqual(repo._shown_remote("https://tok@git.example.test:8443/r.git"),
                         "https://git.example.test:8443/r.git")


class Checkout(WithARepo):
    def test_it_switches_a_clean_tree_to_a_local_branch(self):
        r = repo.checkout(self.root, "local-only", "local-only")
        self.assertTrue(r["ok"])
        self.assertEqual(sh(self.root, "rev-parse", "--abbrev-ref", "HEAD"), "local-only")

    def test_a_branch_known_only_on_the_remote_is_checked_out_tracking_it(self):
        sh(self.root, "branch", "-D", "feature/one")
        r = repo.checkout(self.root, "feature/one", "feature/one")
        self.assertIn("from origin/feature/one", r["message"])
        self.assertEqual(sh(self.root, "rev-parse", "--abbrev-ref", "@{upstream}"), "origin/feature/one")

    def test_a_changed_tracked_file_is_named_and_stops_the_switch(self):
        write(self.root, "README.md", "edited\n")
        r = repo.info(self.root)
        self.assertEqual((r["modified"], r["blocking"], r["switchable"]), (["README.md"], ["README.md"], False))
        with self.assertRaises(repo.Refused) as got:
            repo.checkout(self.root, "local-only", "local-only")
        self.assertIn("README.md", str(got.exception))
        self.assertEqual(sh(self.root, "rev-parse", "--abbrev-ref", "HEAD"), "main")

    def test_untracked_files_and_the_mock_switches_ride_along(self):
        # The machine where the real Jira is has mock.json turned off, and
        # odd files lying about; neither must pin it to one branch.
        write(self.root, "tools/agent/mock.json", '{"jira": true, "cursor": true}\n')
        sh(self.root, "add", "-A")
        sh(self.root, "commit", "-q", "-m", "switches")
        # Both branches hold the same mock.json, as every branch of the
        # real repository does; git then carries a local edit across.
        sh(self.root, "branch", "-f", "local-only", "main")
        write(self.root, "tools/agent/mock.json", '{"jira": false, "cursor": false}\n')
        write(self.root, "notes.txt", "mine\n")
        r = repo.info(self.root)
        self.assertEqual((r["clean"], r["switchable"], r["blocking"]), (False, True, []))
        self.assertEqual(r["modified"], ["tools/agent/mock.json"])
        got = repo.checkout(self.root, "local-only", "local-only")
        self.assertTrue(got["ok"])
        self.assertEqual(sh(self.root, "rev-parse", "--abbrev-ref", "HEAD"), "local-only")
        with io.open(os.path.join(self.root, "tools", "agent", "mock.json"), encoding="utf-8") as fh:
            self.assertIn('"jira": false', fh.read(), "the local switches came along")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "notes.txt")))

    def test_the_name_twice_a_real_branch_and_a_plain_name(self):
        with self.assertRaises(repo.Refused):
            repo.checkout(self.root, "local-only", "other")
        with self.assertRaises(repo.Refused) as got:
            repo.checkout(self.root, "nowhere", "nowhere")
        self.assertIn("no branch nowhere", str(got.exception))
        for bad in ("-x", "a..b", "a@{1}", "x.lock", "x/", "a b"):
            with self.assertRaises(repo.Refused, msg=bad):
                repo.checkout(self.root, bad, bad)
        self.assertEqual(sh(self.root, "rev-parse", "--abbrev-ref", "HEAD"), "main")

    def test_the_lock_is_taken_while_switching(self):
        taken = []

        class Lock:
            def __enter__(self_):
                taken.append("in")
                return self_

            def __exit__(self_, *a):
                taken.append("out")
                return False
        repo.checkout(self.root, "local-only", "local-only", lock=Lock())
        self.assertEqual(taken, ["in", "out"])


if __name__ == "__main__":
    import partest
    partest.main(__file__)
