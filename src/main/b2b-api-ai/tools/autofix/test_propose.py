"""The packet, the answer parser, and the proposal flow.

Three of these pin silent failures found while building Phase 3, and each
one produced a confident wrong answer rather than an error:

  * `git apply` printing "Skipped patch" and EXITING 0, so a patch that
    changed nothing was reported as applied and the gate then failed for
    the original reason -- blaming the proposal.
  * an answer parser that uppercased a line and then asked whether it was
    uppercase (true of any alphabetic line), so every section ended at
    its own first line and SIDE always came back empty.
  * a task packet anchored on the files a checker happened to name, which
    for a unit failure meant the test file and two README.md paths, and
    never the emitter where the bug was.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402
import propose as pr  # noqa: E402
import taskpacket as tp  # noqa: E402

ANSWER = """\
DIAGNOSIS
_param_binding_sig reports every path param as "row".
It drops the row-vs-ref distinction.

SIDE
emitter

PATCH
diff --git a/tools/x.py b/tools/x.py
--- a/tools/x.py
+++ b/tools/x.py
@@ -1 +1 @@
-old
+new

BLAST RADIUS
none without a reconvert

EVIDENCE
python tools/ra_converter/test_converter_fixes.py
"""


class AnswerParsing(unittest.TestCase):
    def test_every_section_is_recovered(self):
        self.assertIn("_param_binding_sig", pr.extract_section(ANSWER, "DIAGNOSIS"))
        self.assertEqual(pr.extract_section(ANSWER, "SIDE"), "emitter")
        self.assertEqual(pr.extract_section(ANSWER, "BLAST RADIUS"),
                         "none without a reconvert")

    def test_a_multi_line_section_keeps_both_lines(self):
        """The isupper() bug ended a section at its own first line."""
        self.assertEqual(len(pr.extract_section(ANSWER, "DIAGNOSIS").split("\n")), 2)

    def test_a_missing_section_is_empty_not_an_error(self):
        self.assertEqual(pr.extract_section(ANSWER, "NOT A SECTION"), "")

    def test_the_patch_is_extracted_whole(self):
        p = pr.extract_patch(ANSWER)
        self.assertTrue(p.startswith("diff --git a/tools/x.py"))
        self.assertIn("+new", p)
        self.assertNotIn("BLAST RADIUS", p)

    def test_a_fenced_patch_is_unwrapped(self):
        fenced = "PATCH\n```diff\ndiff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n+x\n```\n"
        self.assertIn("diff --git a/t.py", pr.extract_patch(fenced))
        self.assertNotIn("```", pr.extract_patch(fenced))

    def test_a_trailing_space_only_context_line_survives(self):
        """A context line for a BLANK source line is a single space.
        rstrip() removed it, leaving a hunk one line shorter than its @@
        header promised -- git apply then said "corrupt patch", which
        reads like the agent produced nonsense."""
        ans = ("PATCH\n"
               "diff --git a/tools/x.py b/tools/x.py\n"
               "--- a/tools/x.py\n+++ b/tools/x.py\n"
               "@@ -1,3 +1,3 @@\n a\n-b\n+c\n \n"
               "\nBLAST RADIUS\nnone\n")
        got = pr.extract_patch(ans)
        self.assertTrue(got.endswith(" \n"), repr(got[-12:]))
        self.assertEqual(got.count("\n"), 8)

    def test_an_answer_with_no_diff_yields_no_patch(self):
        self.assertEqual(pr.extract_patch("DIAGNOSIS\nI could not find it.\n"), "")


class ApplyIsVerified(unittest.TestCase):
    """git apply exits 0 having skipped a patch it could not place."""

    def test_a_patch_that_changes_nothing_is_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            patch = ("diff --git a/tools/definitely_absent_xyz.py "
                     "b/tools/definitely_absent_xyz.py\n"
                     "--- a/tools/definitely_absent_xyz.py\n"
                     "+++ b/tools/definitely_absent_xyz.py\n"
                     "@@ -1 +1 @@\n-a\n+b\n")
            ok, out = pr.apply_patch(patch, td, before_hashes={
                "tools/definitely_absent_xyz.py": None})
            self.assertFalse(ok, "a no-op apply must not read as applied")

    def test_check_only_never_writes(self):
        with tempfile.TemporaryDirectory() as td:
            before = pr._hash("tools/verify_all.py")
            pr.apply_patch("diff --git a/tools/verify_all.py b/tools/verify_all.py\n"
                           "--- a/tools/verify_all.py\n+++ b/tools/verify_all.py\n"
                           "@@ -1 +1 @@\n-x\n+y\n", td, check_only=True)
            self.assertEqual(pr._hash("tools/verify_all.py"), before)


class Retries(unittest.TestCase):
    """taskpacket has always accepted attempt/previous and nothing passed
    them, so a rejection was final even when it was the kind an agent
    could act on ("you edited generated output")."""

    def test_feedback_carries_the_invariant_that_rejected_it(self):
        fb = pr.rejection_feedback({
            "outcome": "rejected", "why": "an invariant was violated",
            "violations": [{"verdict": "REJECT", "rule": "generated-output",
                            "path": "src/.../Specs3.java",
                            "message": "the next convert overwrites it"}]})
        self.assertIn("generated-output", fb)
        self.assertIn("overwrites", fb)

    def test_feedback_carries_the_failing_gate_output(self):
        fb = pr.rejection_feedback({
            "outcome": "failed-gate", "why": "stopped at 'unit'",
            "ladder": {"at": "unit", "failed": [
                {"command": "python tools/x.py",
                 "tail": ["FAIL test_a", "AssertionError: 1 != 2"]}]}})
        self.assertIn("python tools/x.py", fb)
        self.assertIn("AssertionError", fb)

    def test_the_retry_packet_says_what_was_rejected(self):
        text = tp.build(Packet.REC, attempt=2,
                        previous="REJECT generated-output: do not edit it")
        self.assertIn("ATTEMPT 2", text)
        self.assertIn("generated-output", text)
        self.assertIn("Do not resubmit it unchanged", text)

    def test_attempts_defaults_to_one(self):
        import inspect
        self.assertEqual(
            inspect.signature(pr.run).parameters["attempts"].default, 1)


class Workspace(unittest.TestCase):
    def test_editable_source_excludes_generated_and_manual(self):
        files = pr.editable_source()
        if not files:
            self.skipTest("no tree")
        for rel in files:
            self.assertTrue(fr.is_editable(fr.classify_path(rel)), rel)
        self.assertFalse([f for f in files if "/support/" in f and "/cases/" in f])
        self.assertFalse([f for f in files if "__pycache__" in f])

    def test_the_emitter_is_editable(self):
        files = pr.editable_source()
        if not files:
            self.skipTest("no tree")
        self.assertIn("tools/ra_converter/ra_converter.py", files)

    def test_snapshot_copies_only_what_git_cannot_restore(self):
        """82MB of source; copying it all per attempt is the thing to avoid."""
        with tempfile.TemporaryDirectory() as td:
            snap = pr.snapshot_source(td)
            self.assertGreater(len(snap["hashes"]), 50)
            self.assertLessEqual(snap["copied"], len(snap["dirty"]))
            for rel in snap["dirty"]:
                self.assertTrue(fr.is_editable(fr.classify_path(rel)), rel)

    def test_changed_since_sees_an_edit_and_restore_undoes_it(self):
        # Restoring a CLEAN TRACKED file is `git checkout --`, so this
        # test is about git as much as about the snapshot. Outside a
        # repository it failed with "unrestored: ['tools/autofix/
        # __init__.py']" -- a true statement that git could not restore
        # the file, read as a bug in restore_source. propose.run() now
        # refuses to start there at all.
        if not fr.in_git_repo():
            self.skipTest("not a git repository; propose.run() refuses here")
        target = "tools/autofix/__init__.py"
        p = os.path.join(pr.ROOT, target.replace("/", os.sep))
        original = io.open(p, "rb").read()
        with tempfile.TemporaryDirectory() as td:
            snap = pr.snapshot_source(td)
            io.open(p, "wb").write(original + b"# fixture\n")
            try:
                self.assertIn(target, pr.changed_since(snap)["modified"])
                ok, bad = pr.restore_source(snap)
                self.assertTrue(ok, f"unrestored: {bad}")
                self.assertEqual(io.open(p, "rb").read(), original)
            finally:
                io.open(p, "wb").write(original)

    def test_restore_removes_a_file_the_agent_created(self):
        created = "tools/autofix/_fixture_created.py"
        p = os.path.join(pr.ROOT, created.replace("/", os.sep))
        with tempfile.TemporaryDirectory() as td:
            snap = pr.snapshot_source(td)
            io.open(p, "w", encoding="utf-8").write("# created by a fixture\n")
            try:
                self.assertIn(created, pr.changed_since(snap)["added"])
                ok, _bad = pr.restore_source(snap)
                self.assertTrue(ok)
                self.assertFalse(os.path.exists(p))
            finally:
                if os.path.exists(p):
                    os.remove(p)


class StaleGeneratedWarning(unittest.TestCase):
    """restore_source restores SOURCE. If the ladder reached
    repro-convert, the generated tree holds output from a patch that was
    then rejected -- and the next verify_all measures that."""

    def test_a_rejected_patch_that_converted_is_reported(self):
        res = {}
        pr._note_stale_generated(res, {"total": 122, "by_suite": {
            "amexbackbook": {"changed": 10}, "(shared)": {"changed": 2}}})
        self.assertEqual(res["generated_stale"]["files"], 122)
        self.assertEqual(res["generated_stale"]["suites"], ["amexbackbook"])

    def test_nothing_is_said_when_no_generated_file_changed(self):
        res = {}
        pr._note_stale_generated(res, {"total": 0, "by_suite": {}})
        self.assertNotIn("generated_stale", res)

    def test_the_report_surfaces_it(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.md")
            pr.write_report({"check": "x", "outcome": "failed-gate",
                             "generated_stale": {"files": 9,
                                                 "suites": ["amexbackbook"]}}, p)
            body = io.open(p, encoding="utf-8").read()
            self.assertIn("amexbackbook", body)
            self.assertIn("9", body)


class AutoCommitMechanics(unittest.TestCase):
    """Six lines of git carrying a hard rule: never push, never commit to
    the working branch. Exercised against a recorded command log rather
    than a real repository."""

    def setUp(self):
        self.calls: list[tuple] = []
        self._git = pr._git
        pr._git = lambda *a: (self.calls.append(a), (0, ""))[1]

    def tearDown(self):
        pr._git = self._git

    def _commit(self):
        """Replay just the auto-commit block with stub git."""
        files = [inv_fd("tools/ra_converter/ra_converter.py")]
        result = {"ladder": {"outcome": "stopped", "at": "compile"},
                  "diagnosis": "d"}
        rec = {"name": "contracts", "salient_fingerprint": "abcdef1234"}
        with tempfile.TemporaryDirectory() as td:
            br = f"autofix/{rec['name']}-{rec['salient_fingerprint'][:8]}"
            pr._git("checkout", "-b", br)
            pr._git("add", "--", *[f.path for f in files if f.path])
            mp = os.path.join(td, "commitmsg.txt")
            io.open(mp, "w", encoding="utf-8", newline="\n").write("msg")
            pr._git("commit", "-F", mp)
        return br

    def test_it_never_pushes(self):
        self._commit()
        for c in self.calls:
            self.assertNotIn("push", c, f"auto mode must never push: {c}")

    def test_it_commits_on_a_new_branch_not_the_current_one(self):
        self._commit()
        self.assertEqual(self.calls[0][:2], ("checkout", "-b"))
        order = [c[0] for c in self.calls]
        self.assertLess(order.index("checkout"), order.index("commit"))

    def test_the_branch_name_identifies_the_failure(self):
        br = self._commit()
        self.assertTrue(br.startswith("autofix/contracts-"))
        self.assertIn("abcdef12", br)

    def test_only_the_patched_files_are_staged(self):
        self._commit()
        add = next(c for c in self.calls if c[0] == "add")
        self.assertEqual(add[1], "--")
        self.assertEqual(list(add[2:]), ["tools/ra_converter/ra_converter.py"])

    def test_the_source_says_it_never_pushes(self):
        src = io.open(os.path.join(pr.ROOT, "tools", "autofix", "propose.py"),
                      encoding="utf-8").read()
        self.assertNotIn('"push"', src)
        self.assertNotIn("'push'", src)


def inv_fd(path):
    import invariants as inv
    return inv.FileDiff(path=path)


class Packet(unittest.TestCase):
    REC = {
        "name": "contracts", "kind": "unit", "policy": "auto",
        "why": "converter behaviours previously regressed",
        "repro": "python tools/ra_converter/test_converter_fixes.py",
        "salient_fingerprint": "abc123def456",
        "salient": ["FAIL test_param_binding_sig_reports_row_vs_ref:",
                    "[ra_converter] SKIP (exists): src/main/resources/openapi/README.md"],
        "output_tail": [],
        "implicated_files": {"source": ["src/main/resources/openapi/README.md",
                                        "tools/ra_converter/test_converter_fixes.py"],
                             "generated": [], "never_touch": []},
        "locate": {"suites": [], "cases": []},
    }

    def test_a_failing_test_name_anchors_on_the_function_under_test(self):
        """`test_param_binding_sig_reports_row_vs_ref` -> _param_binding_sig."""
        if not tp.defs_index():
            self.skipTest("no tool tree")
        syms = [s for s, _r, _l in tp.symbols_from(self.REC["salient"])]
        self.assertIn("_param_binding_sig", syms)

    def test_the_packet_reaches_the_emitter(self):
        if not tp.defs_index():
            self.skipTest("no tool tree")
        text = tp.build(self.REC)
        self.assertIn("tools/ra_converter/ra_converter.py", text)
        self.assertIn("_param_binding_sig", text)

    def test_readmes_leaked_in_by_converter_logging_are_excluded(self):
        text = tp.build(self.REC)
        self.assertNotIn("openapi/README.md (whole file", text)

    def test_the_packet_is_far_smaller_than_the_emitter(self):
        if not tp.defs_index():
            self.skipTest("no tool tree")
        emitter = tp._read("tools/ra_converter/ra_converter.py") or []
        self.assertLess(len(tp.build(self.REC).splitlines()), len(emitter) // 10)

    def test_the_rules_name_every_author_editable_file(self):
        for base in fr.AUTHOR_EDITABLE_BASENAMES:
            self.assertIn(base, tp.RULES, base)

    def test_the_answer_format_asks_for_a_measurable_blast_radius(self):
        self.assertIn("BLAST RADIUS", tp.ANSWER)
        self.assertIn("hash manifest", tp.ANSWER)

    def test_a_retry_carries_the_previous_rejection(self):
        text = tp.build(self.REC, attempt=2, previous="rejected: generated-output")
        self.assertIn("ATTEMPT 2", text)
        self.assertIn("generated-output", text)

    def test_line_refs_are_read_from_checker_output(self):
        got = tp.line_refs(["tools/check_phase_order.py:88: boom",
                            "src/main/java/com/hi/api/X.java:[12,3] error"])
        self.assertEqual(got["tools/check_phase_order.py"], [88])
        self.assertEqual(got["src/main/java/com/hi/api/X.java"], [12])

    def test_an_excerpt_is_windowed_not_whole(self):
        if not os.path.exists(os.path.join(tp.ROOT, "tools", "ra_converter",
                                           "ra_converter.py")):
            self.skipTest("no emitter")
        ex = tp.excerpt("tools/ra_converter/ra_converter.py", [4720])
        self.assertIn("@@", ex)
        self.assertLess(len(ex.splitlines()), 120)

    def test_only_a_failing_check_becomes_a_task(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.json")
            json.dump({"checks": [{"name": "a", "status": "accepted",
                                   "salient_fingerprint": "x"}]}, open(p, "w"))
            with self.assertRaises(SystemExit):
                tp.from_records(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
