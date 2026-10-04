"""Every invariant, stated as the shortcut it closes.

These are the controls that stop a green gate being bought instead of
earned, so each one is pinned in both directions: it fires on the thing it
forbids, and it does NOT fire on the ordinary fix it must allow. A guard
that rejects everything is as useless as one that rejects nothing, and the
false-positive half is what makes it survive contact with real work.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import invariants as inv  # noqa: E402

HOSTS = frozenset({"known.example-vendor.com", "jsonplaceholder.typicode.com"})


def diff(path, added=(), removed=(), new_file=False, deleted=False):
    return inv.FileDiff(path=path, added=list(added), removed=list(removed),
                        new_file=new_file, deleted=deleted)


def rules(files, hosts=HOSTS):
    return {v.rule for v in inv.validate(list(files), hosts)}


def verdicts(files, hosts=HOSTS):
    return {v.rule: v.verdict for v in inv.validate(list(files), hosts)}


class DiffParsing(unittest.TestCase):
    PATCH = (
        "diff --git a/src/main/b2b-api-ai/tools/x.py b/src/main/b2b-api-ai/tools/x.py\n"
        "index 111..222 100644\n"
        "--- a/src/main/b2b-api-ai/tools/x.py\n"
        "+++ b/src/main/b2b-api-ai/tools/x.py\n"
        "@@ -1 +1 @@\n"
        "-old line\n"
        "+new line\n")

    def test_strips_the_subdirectory_prefix(self):
        """Diff paths are git-root-relative; every rule here is
        ROOT-relative. Without the strip nothing matches and the whole
        module becomes a confident no-op."""
        got = inv.parse_diff(self.PATCH, "src/main/b2b-api-ai/")
        self.assertEqual([f.path for f in got], ["tools/x.py"])
        self.assertEqual(got[0].added, ["new line"])
        self.assertEqual(got[0].removed, ["old line"])

    def test_does_not_mistake_marker_lines_for_content(self):
        got = inv.parse_diff(self.PATCH, "src/main/b2b-api-ai/")[0]
        self.assertNotIn("+ b/src/main/b2b-api-ai/tools/x.py", got.added)
        self.assertEqual(len(got.added), 1)

    def test_a_reversed_header_still_yields_the_path(self):
        """`git diff -R` emits `diff --git b/x a/x`."""
        rev = ("diff --git b/tools/x.py a/tools/x.py\n"
               "--- b/tools/x.py\n+++ a/tools/x.py\n@@ -1 +1 @@\n-a\n+b\n")
        self.assertEqual([f.path for f in inv.parse_diff(rev)], ["tools/x.py"])

    def test_a_header_with_no_diff_git_line_is_recovered(self):
        plain = ("--- a/tools/x.py\n+++ b/tools/x.py\n@@ -1 +1 @@\n-a\n+b\n")
        got = inv.parse_diff(plain)
        self.assertEqual([f.path for f in got], ["tools/x.py"])

    def test_an_unreadable_header_is_rejected_not_validated(self):
        """FAIL-OPEN BUG, found by a real malformed patch: an unparsed
        header left path="" and every rule keys on the path, so the diff
        validated clean while touching anything it liked."""
        broken = inv.FileDiff(path="", added=['  password = "Hunter2xyz"'])
        v = verdicts([broken])
        self.assertEqual(v.get("unparseable-diff"), inv.REJECT)

    def test_an_unparseable_header_cannot_be_waived_into_silence(self):
        broken = inv.FileDiff(path="", added=["x"])
        standing, waived, _ = inv.apply_waivers(
            self._novio(broken), inv.parse_waivers(["generated-output"]))
        self.assertTrue(standing)

    def _novio(self, fd):
        return inv.validate([fd], HOSTS)

    def test_detects_a_new_file_and_a_deletion(self):
        new = ("diff --git a/tools/n.py b/tools/n.py\n--- /dev/null\n"
               "+++ b/tools/n.py\n@@ -0,0 +1 @@\n+hello\n")
        self.assertTrue(inv.parse_diff(new)[0].new_file)
        gone = ("diff --git a/tools/n.py b/tools/n.py\n--- a/tools/n.py\n"
                "+++ /dev/null\n@@ -1 +0,0 @@\n-hello\n")
        self.assertTrue(inv.parse_diff(gone)[0].deleted)


class PatchNormalisation(unittest.TestCase):
    """`git diff` emits GIT-ROOT-relative paths and this project is a
    subdirectory, so the likeliest patch an agent produces arrives with an
    extra prefix. Validated as-is, every rule missed it: a support/ path
    behind the prefix was not recognised as generated and the diff passed
    clean."""

    GEN = "src/main/java/com/hi/api/support/amexbackbook/cases/Specs3.java"

    def _patch(self, path):
        return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
                f"@@ -1 +1 @@\n-a\n+b\n")

    def test_a_git_root_relative_patch_is_still_caught(self):
        pfx = inv.repo_prefix()
        if not pfx:
            self.skipTest("project is the repository root here")
        norm = inv.normalize_patch(self._patch(pfx + self.GEN), pfx)
        self.assertIn("generated-output",
                      rules(inv.parse_diff(norm, pfx)))

    def test_a_root_relative_patch_is_unchanged_by_normalisation(self):
        pfx = inv.repo_prefix()
        raw = self._patch(self.GEN)
        self.assertEqual(inv.normalize_patch(raw, pfx), raw)

    def test_normalisation_leaves_dev_null_alone(self):
        raw = ("diff --git a/tools/n.py b/tools/n.py\n--- /dev/null\n"
               "+++ b/tools/n.py\n@@ -0,0 +1 @@\n+x\n")
        self.assertIn("--- /dev/null", inv.normalize_patch(raw, "src/x/"))

    def test_normalisation_leaves_content_lines_alone(self):
        raw = ("diff --git a/tools/x.py b/tools/x.py\n--- a/tools/x.py\n"
               "+++ b/tools/x.py\n@@ -1 +1 @@\n-  p = 'a/b'\n+  p = 'c/d'\n")
        out = inv.normalize_patch(raw, "tools/")
        self.assertIn("-  p = 'a/b'", out)
        self.assertIn("+  p = 'c/d'", out)

    def test_an_empty_prefix_is_a_no_op(self):
        raw = self._patch(self.GEN)
        self.assertEqual(inv.normalize_patch(raw, ""), raw)


class PathEscapes(unittest.TestCase):
    """classify_path called these 'source', so the only thing between them
    and a write outside the tree was whatever git apply refused."""

    def test_relative_escapes_are_rejected(self):
        for p in ("../../../etc/passwd", "tools/../../outside.py",
                  "a/../../b.py"):
            self.assertIn("path-escape", rules([diff(p, added=["x"])]), p)

    def test_absolute_paths_are_rejected(self):
        for p in ("/etc/passwd", "C:/Windows/system32/x.py",
                  "D:/other/repo/tools/x.py"):
            self.assertIn("path-escape", rules([diff(p, added=["x"])]), p)

    def test_a_double_prefixed_path_is_rejected(self):
        pfx = inv.repo_prefix()
        if not pfx:
            self.skipTest("project is the repository root here")
        self.assertIn("path-escape",
                      rules([diff(pfx + "tools/x.py", added=["x"])]))

    def test_an_ordinary_path_is_not_an_escape(self):
        for p in ("tools/ra_converter/ra_converter.py",
                  "src/main/java/com/hi/api/rest/utilities/ResponseAsserts.java"):
            self.assertNotIn("path-escape", rules([diff(p, added=["x"])]), p)

    def test_a_dotfile_is_not_an_escape(self):
        self.assertNotIn("path-escape",
                         rules([diff("tools/.keep", added=["x"])]))


class GeneratedOutput(unittest.TestCase):
    def test_rejects_an_edit_to_generated_output(self):
        self.assertIn("generated-output", rules([
            diff("src/main/java/com/hi/api/support/amexbackbook/cases/Specs3.java",
                 added=["// tweak"])]))

    def test_rejects_generated_tests_and_csvs(self):
        for p in ("src/test/java/com/hi/api/tests/imported/X.java",
                  "src/test/resources/csv/b2b.csv",
                  "src/main/resources/templates/amexbackbook/body.json"):
            self.assertIn("generated-output", rules([diff(p, added=["x"])]), p)

    def test_allows_the_emitter(self):
        self.assertNotIn("generated-output",
                         rules([diff("tools/ra_converter/ra_converter.py",
                                     added=["# fix"])]))

    def test_rejects_the_manual_suites(self):
        for p in ("src/main/java/com/hi/api/rest/manual/client/ManualClient.java",
                  "src/test/java/com/hi/api/tests/manual/GuestEnrollTest.java"):
            self.assertIn("never-touch", rules([diff(p, added=["x"])]), p)


class AuthorEditablePairing(unittest.TestCase):
    """SKIP-IF-EXISTS files survive in THIS tree and vanish from the next
    clean convert. This cost a round once, on PlaceholderResolver."""

    FW = "src/main/java/com/hi/api/support/CtxFields.java"
    PR = "src/main/java/com/hi/api/data/PlaceholderResolver.java"

    def test_rejects_editing_the_emitted_copy_alone(self):
        self.assertIn("unpaired-framework-edit",
                      rules([diff(self.FW, added=["// fix"])]))

    def test_accepts_the_pair(self):
        self.assertNotIn("unpaired-framework-edit", rules([
            diff(self.FW, added=["// fix"]),
            diff("tools/ra_converter/framework/CtxFields.java", added=["// fix"])]))

    def test_an_inline_sourced_file_pairs_with_the_emitter(self):
        self.assertIn("unpaired-framework-edit", rules([diff(self.PR, added=["// fix"])]))
        self.assertNotIn("unpaired-framework-edit", rules([
            diff(self.PR, added=["// fix"]),
            diff("tools/ra_converter/ra_converter.py", added=["// fix"])]))

    def test_an_author_editable_file_is_not_rejected_as_generated(self):
        """It lives inside the generated root; rejecting it would forbid a
        legitimate fix site."""
        self.assertNotIn("generated-output", rules([diff(self.FW, added=["// fix"])]))

    def test_the_pairing_list_matches_the_converters_own(self):
        import failure_record as fr
        src = open(os.path.join(inv.ROOT, "tools", "ra_converter", "ra_converter.py"),
                   encoding="utf-8").read()
        i = src.index("_AUTHOR_EDITABLE_BASENAMES = frozenset({")
        block = src[i:src.index("})", i)]
        import re
        declared = set(re.findall(r'"([^"]+\.java)"', block))
        self.assertEqual(declared, set(fr.AUTHOR_EDITABLE_BASENAMES),
                         "this list must track ra_converter's or the pairing "
                         "rule silently stops covering a file")


class SelfWeakening(unittest.TestCase):
    def test_rejects_editing_the_baseline(self):
        """One entry there turns any failure into an accepted one."""
        self.assertIn("self-weakening",
                      rules([diff("tools/autofix/baseline.json",
                                  added=['      "check": "anything"'])]))

    def test_rejects_editing_the_invariants_or_their_tests(self):
        for p in ("tools/autofix/invariants.py", "tools/autofix/mutations.py",
                  "tools/autofix/test_invariants.py"):
            self.assertIn("self-weakening", rules([diff(p, added=["pass"])]), p)

    def test_rejects_project_plumbing(self):
        for p in ("pom.xml", ".gitignore", ".github/workflows/ci.yml",
                  "tools/ra_converter/converter.config.json"):
            self.assertIn("infrastructure", rules([diff(p, added=["x"])]), p)


class RemovedGuards(unittest.TestCase):
    def test_rejects_removing_a_check_from_the_gate(self):
        self.assertIn("check-removed", rules([
            diff("tools/verify_all.py",
                 removed=['    Check("step-parity",'])]))

    def test_allows_adding_a_check(self):
        self.assertNotIn("check-removed", rules([
            diff("tools/verify_all.py", added=['    Check("new-thing",'])]))

    def test_allows_moving_a_check(self):
        self.assertNotIn("check-removed", rules([
            diff("tools/verify_all.py",
                 removed=['    Check("a",'], added=['    Check("a",'])]))

    def test_rejects_deleting_a_test(self):
        self.assertIn("test-removed", rules([
            diff("tools/ra_converter/test_converter_fixes.py",
                 removed=["    def test_the_awkward_one(self):"])]))

    def test_allows_renaming_a_test_that_keeps_its_name(self):
        self.assertNotIn("test-removed", rules([
            diff("tools/ra_converter/test_converter_fixes.py",
                 removed=["    def test_x(self):"], added=["    def test_x(self):"])]))

    def test_rejects_net_assertion_removal(self):
        self.assertIn("assertion-removed", rules([
            diff("tools/ra_converter/ra_converter.py",
                 removed=["        assert emitted == expected",
                          "        assert other == thing"],
                 added=["        pass"])]))

    def test_allows_rewriting_an_assertion(self):
        self.assertNotIn("assertion-removed", rules([
            diff("tools/ra_converter/ra_converter.py",
                 removed=["        assert a == b"],
                 added=["        assert a == c  # b was wrong"])]))


class PublicRepo(unittest.TestCase):
    def test_rejects_a_hostname_not_already_tracked(self):
        v = verdicts([diff("tools/ra_converter/ra_converter.py",
                           added=['    URL = "https://secret-vendor-api.internal.net/v2"'])])
        self.assertEqual(v.get("new-hostname"), inv.REJECT)

    def test_allows_a_hostname_already_published(self):
        self.assertNotIn("new-hostname", rules([
            diff("tools/ra_converter/ra_converter.py",
                 added=['    URL = "https://jsonplaceholder.typicode.com/posts"'])]))

    def test_allows_documentation_placeholders(self):
        for host in ("api.example.com", "foo.example.org", "svc.local", "db.test"):
            self.assertNotIn("new-hostname", rules([
                diff("tools/x.py", added=[f'  u = "https://{host}/p"'])]), host)

    def test_does_not_police_generated_output_for_hostnames(self):
        """Generated files are gitignored and full of vendor hosts; they are
        rejected on the path rule, and double-reporting hides that."""
        self.assertNotIn("new-hostname", rules([
            diff("src/main/java/com/hi/api/support/a/cases/S.java",
                 added=['  "https://brand-new-vendor.example-not.com/x"'])]))


class Secrets(unittest.TestCase):
    def test_rejects_a_literal_credential(self):
        for line in ('    password = "Hunter2Hunter2"',
                     '    client_secret: "abc123def456ghi"',
                     '    String t = "Bearer eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop";'):
            self.assertIn("secret-added", rules([
                diff("tools/ra_converter/ra_converter.py", added=[line])]), line)

    def test_allows_reading_a_credential_from_config(self):
        for line in ('    String pw = Config.get("api_config.password", "");',
                     '    password = os.getenv("DB_PASSWORD")',
                     '    "password": "<redacted>"',
                     '    client_secret = row.get("client_secret")',
                     '    password = ""'):
            self.assertNotIn("secret-added", rules([
                diff("tools/ra_converter/ra_converter.py", added=[line])]), line)


class CheckerOnly(unittest.TestCase):
    def test_a_checker_only_change_is_held_for_a_fixture(self):
        v = verdicts([diff("tools/check_phase_order.py", added=["    pass"])])
        self.assertEqual(v.get("checker-only-change"), inv.NEEDS_FIXTURE)

    def test_it_names_the_fixture_command_when_one_exists(self):
        vs = inv.validate([diff("tools/check_phase_order.py", added=["x"])], HOSTS)
        msg = next(v.message for v in vs if v.rule == "checker-only-change")
        self.assertIn("mutations.py --check phase-order", msg)

    def test_it_says_so_when_no_fixture_exists_yet(self):
        vs = inv.validate([diff("tools/check_xml_wellformed.py", added=["x"])], HOSTS)
        msg = next(v.message for v in vs if v.rule == "checker-only-change")
        self.assertIn("no fixture exists", msg)

    def test_a_checker_change_alongside_an_emitter_fix_is_not_held(self):
        """The ordinary case: the emitter was wrong and the checker could not
        see it. That is two real fixes, not a weakened measurement."""
        self.assertNotIn("checker-only-change", rules([
            diff("tools/check_phase_order.py", added=["    pass"]),
            diff("tools/ra_converter/phase_emit.py", added=["    pass"])]))

    def test_a_checker_plus_its_own_test_is_still_held(self):
        """A test written against the weakened checker proves nothing."""
        self.assertIn("checker-only-change", rules([
            diff("tools/check_phase_order.py", added=["    pass"]),
            diff("tools/ra_converter/test_step_parity_rules.py", added=["    pass"])]))


class OrdinaryFixesPass(unittest.TestCase):
    """The false-positive half. A guard that blocks real work gets removed."""

    def test_an_emitter_fix_with_a_test_is_clean(self):
        self.assertEqual(rules([
            diff("tools/ra_converter/ra_converter.py",
                 added=["    if literal_locals:", "        q = inline(q)"],
                 removed=["    q = old(q)"]),
            diff("tools/ra_converter/test_converter_fixes.py",
                 added=["    def test_inlines_a_literal_local(self):",
                        "        self.assertEqual(got, want)"])]), set())

    def test_a_framework_java_fix_with_a_test_is_clean(self):
        self.assertEqual(rules([
            diff("src/main/java/com/hi/api/rest/utilities/ResponseAsserts.java",
                 added=["        softAssert.assertTrue(codes.contains(sc));"]),
            diff("src/test/java/com/hi/api/tests/framework/ResponseAssertsTest.java",
                 added=["    @Test", "    public void rowOverridesCodes() {",
                        "        Assert.assertEquals(a, b);", "    }"])]), set())

    def test_a_new_tool_file_is_clean(self):
        """Outside tools/autofix/ -- the loop's own directory is off-limits
        to a proposal, including new files in it."""
        self.assertEqual(rules([
            diff("tools/report_summary.py", added=["def main():", "    return 0"],
                 new_file=True)]), set())

    def test_a_new_file_inside_the_loops_directory_is_not_clean(self):
        self.assertIn("self-weakening", rules([
            diff("tools/autofix/helper.py", added=["x"], new_file=True)]))


class Waivers(unittest.TestCase):
    """The rules stay strict; a human overrides. So the override itself is
    pinned: it has to be scoped, reasoned, recorded, and refused where the
    constraint has no exceptions."""

    def _vs(self, files):
        return inv.validate(list(files), HOSTS)

    def test_a_held_precondition_can_be_waived(self):
        vs = self._vs([diff("tools/verify_all.py", added=['    Check("new",'])])
        standing, waived, refused = inv.apply_waivers(
            vs, inv.parse_waivers(["checker-only-change"]))
        self.assertEqual(standing, [])
        self.assertEqual([v.rule for v in waived], ["checker-only-change"])
        self.assertEqual(refused, [])

    def test_waiving_is_not_dropping(self):
        """A waived violation is still reported, or the next reader cannot
        tell a considered override from a rule that never fired."""
        vs = self._vs([diff("tools/verify_all.py", added=['    Check("new",'])])
        _standing, waived, _ = inv.apply_waivers(
            vs, inv.parse_waivers(["checker-only-change"]))
        self.assertTrue(waived)
        self.assertIn("checker-only-change", str(waived[0]))

    def test_a_waiver_can_be_scoped_to_a_path(self):
        vs = self._vs([
            diff("src/main/java/com/hi/api/support/a/cases/S.java", added=["x"]),
            diff("src/main/java/com/hi/api/support/b/cases/T.java", added=["x"])])
        standing, waived, _ = inv.apply_waivers(
            vs, inv.parse_waivers(["generated-output:a/cases"]))
        self.assertEqual(len(waived), 1)
        self.assertIn("b/cases", standing[0].path)

    def test_an_unrelated_rule_is_not_waived(self):
        vs = self._vs([diff("tools/autofix/baseline.json", added=["x"])])
        standing, waived, _ = inv.apply_waivers(
            vs, inv.parse_waivers(["checker-only-change"]))
        self.assertEqual(waived, [])
        self.assertEqual([v.rule for v in standing], ["self-weakening"])

    def test_a_credential_can_never_be_waived(self):
        vs = self._vs([diff("tools/ra_converter/ra_converter.py",
                            added=['  password = "Hunter2Hunter2"'])])
        standing, waived, refused = inv.apply_waivers(
            vs, inv.parse_waivers(["secret-added"]))
        self.assertEqual(waived, [])
        self.assertTrue(refused)
        self.assertEqual([v.rule for v in standing], ["secret-added"])

    def test_the_manual_suites_can_never_be_waived(self):
        vs = self._vs([diff("src/test/java/com/hi/api/tests/manual/"
                            "GuestEnrollTest.java", added=["x"])])
        standing, waived, refused = inv.apply_waivers(
            vs, inv.parse_waivers(["never-touch"]))
        self.assertEqual(waived, [])
        self.assertTrue(refused)
        self.assertTrue(standing)

    def test_a_waiver_without_a_reason_is_refused_at_the_cli(self):
        import io as _io
        import contextlib
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = inv.main(["--diff-file", os.devnull, "--waive", "generated-output"])
        self.assertEqual(rc, 2)
        self.assertIn("needs --waive-reason", buf.getvalue())

    def test_unwaivable_set_is_exactly_the_standing_constraints(self):
        self.assertEqual(inv.UNWAIVABLE, {"secret-added", "never-touch"})


class Published(unittest.TestCase):
    def test_tracked_hostnames_are_discovered_from_git(self):
        hosts = inv.published_hosts()
        if not hosts:
            self.skipTest("git unavailable")
        self.assertIn("jsonplaceholder.typicode.com", hosts,
                      "application.properties is tracked and names this host")

    def test_a_vendor_host_only_in_generated_output_is_not_published(self):
        """If the known set came from the whole tree, every vendor host
        would already be 'present' and the rule would protect nothing.

        The host is DISCOVERED from the generated tree rather than written
        here. Naming a real one would put it in a tracked file on a public
        repo -- which is the thing this rule exists to prevent, and would
        also make the assertion false the moment it was committed.
        """
        hosts = inv.published_hosts()
        if not hosts:
            self.skipTest("git unavailable")
        import glob
        found = ""
        pat = os.path.join(inv.ROOT, "src", "main", "resources", "config", "*.json")
        for f in glob.glob(pat):
            for h in inv._HOSTLIKE_RX.findall(
                    open(f, encoding="utf-8", errors="replace").read()):
                if h.lower() not in hosts:
                    found = h.lower()
                    break
            if found:
                break
        if not found:
            self.skipTest("no generated-only host available to test with")
        self.assertNotIn(found, hosts)
        self.assertIn("new-hostname", rules([
            diff("tools/ra_converter/ra_converter.py",
                 added=[f'    URL = "https://{found}/v2"'])]),
            "a host that exists only in gitignored output must still be new")


if __name__ == "__main__":
    unittest.main(verbosity=2)
