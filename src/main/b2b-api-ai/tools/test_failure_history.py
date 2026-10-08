"""A run is compared with the runs before it, and nothing is over-claimed.

    python tools/test_failure_history.py

Everything is written to a temporary directory.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import failure_history as fh  # noqa: E402

REFUSED = "FlowStopped | POST /accounts/<*>/members answered 403 Forbidden: not the owner"
TIMEOUT = "AssertionError | status after polling GET /accounts/<*>/status: expected [ACTIVE] but found [PENDING]"
PLAIN = "AssertionError | <*> expected [true] but found [false]"


def digest(*failures, suite="B2B", header=True):
    lines = [f"FAILURE DIGEST -- {suite}",
             f"unique failing (test, row) pairs: {len(failures)}   distinct signatures: "
             f"{len({f[2] for f in failures})}", "",
             "== signatures, most frequent first ==", "", "[1] 3 failure(s)  something", "",
             "== first failing call behind each failure ==", "   (a note)", ""]
    if header:
        lines.append(fh.SECTION)
        lines += ["\t".join(f) for f in failures]
    return "\n".join(lines) + "\n"


class Store(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="failhist_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.n = 0

    def run_of(self, *failures, label="", suite="B2B", tests=None):
        self.n += 1
        if tests is not None:
            d = os.path.join(self.root, "target", "surefire-reports")
            os.makedirs(d, exist_ok=True)
            with io.open(os.path.join(d, "TEST-TestSuite.xml"), "w", encoding="utf-8") as fh_:
                fh_.write(f'<?xml version="1.0"?>\n<testsuite name="x" tests="{tests}" failures="1">')
        return fh.record(self.root, digest(*failures, suite=suite), label,
                         now=f"202601{self.n:02d}T000000Z")

    def text(self, snap, prev):
        return "\n".join(fh.report(self.root, snap, prev))


class ReadingADigest(unittest.TestCase):
    def test_the_per_test_section_is_what_is_read(self):
        p = fh.parse_digest(digest(("A.one", "-", PLAIN), ("B.two", "row 3", REFUSED)))
        self.assertEqual(p["suite"], "B2B")
        self.assertEqual(p["failures"], [{"test": "A.one", "row": "-", "signature": PLAIN},
                                         {"test": "B.two", "row": "row 3", "signature": REFUSED}])

    def test_a_signature_with_a_tab_in_it_stays_whole_and_a_repeat_is_one(self):
        p = fh.parse_digest(digest(("A.one", "-", "x\ty"), ("A.one", "-", "again")))
        self.assertEqual(p["failures"], [{"test": "A.one", "row": "-", "signature": "x\ty"}])

    def test_the_section_ends_at_a_blank_line_or_the_next_heading(self):
        text = digest(("A.one", "-", PLAIN)) + "\n== another section ==\nC.x\t-\tnot a failure\n"
        self.assertEqual(len(fh.parse_digest(text)["failures"]), 1)

    def test_no_failures_is_an_empty_run_not_an_error(self):
        self.assertEqual(fh.parse_digest(digest(header=False))["failures"], [])

    def test_a_file_that_is_not_a_digest_or_an_older_one_is_refused(self):
        with self.assertRaises(fh.Unusable):
            fh.parse_digest("some other log\n")
        with self.assertRaises(fh.Unusable):
            fh.parse_digest("")
        old = digest(("A.one", "-", PLAIN), header=False)
        with self.assertRaises(fh.Unusable) as got:
            fh.parse_digest(old)
        self.assertIn("digest version", str(got.exception))


class Comparing(Store):
    def test_the_first_run_has_nothing_to_compare_with(self):
        snap, prev, fresh = self.run_of(("A.one", "-", PLAIN))
        self.assertTrue(fresh)
        self.assertIsNone(prev)
        self.assertIn("first recorded run", self.text(snap, prev))

    def test_new_gone_changed_and_same_are_told_apart(self):
        self.run_of(("A.same", "-", PLAIN), ("B.changes", "r1", REFUSED), ("C.goes", "-", PLAIN))
        snap, prev, _ = self.run_of(("A.same", "-", PLAIN), ("B.changes", "r1", TIMEOUT),
                                    ("D.new", "-", REFUSED))
        d = fh.compare(snap, prev)
        self.assertEqual((d["new"], d["gone"], d["changed"], d["same"]),
                         ([("D.new", "-")], [("C.goes", "-")], [("B.changes", "r1")],
                          [("A.same", "-")]))
        text = self.text(snap, prev)
        self.assertIn("FAILING DIFFERENTLY (same test, another signature): 1", text)
        self.assertIn("was: FlowStopped", text)
        self.assertIn("now: AssertionError | status after polling", text)
        self.assertIn("B.changes [r1]", text)

    def test_gone_is_never_called_fixed(self):
        self.run_of(("A.one", "-", PLAIN), tests=600)
        snap, prev, _ = self.run_of(("B.two", "-", PLAIN), tests=40)
        text = self.text(snap, prev)
        self.assertIn("no longer failing (passed, or did not run): 1", text)
        self.assertNotIn("fixed", text.lower())
        self.assertIn("different number of tests (600 then, 40 now)", text)

    def test_without_a_test_count_that_is_said_too(self):
        self.run_of(("A.one", "-", PLAIN))
        snap, prev, _ = self.run_of(("B.two", "-", PLAIN))
        self.assertIn("how many tests each run executed is not known", self.text(snap, prev))

    def test_suites_are_compared_with_themselves_only(self):
        self.run_of(("A.one", "-", PLAIN), suite="Guards")
        snap, prev, _ = self.run_of(("A.one", "-", PLAIN), suite="B2B")
        self.assertIsNone(prev)
        self.assertIn("not seen in any earlier run", self.text(snap, prev))

    def test_the_same_digest_recorded_twice_is_one_run(self):
        text = digest(("A.one", "-", PLAIN))
        a, _, fresh_a = fh.record(self.root, text, now="20260101T000000Z")
        b, _, fresh_b = fh.record(self.root, text, now="20260102T000000Z")
        self.assertEqual((fresh_a, fresh_b, b["at"]), (True, False, "20260101T000000Z"))
        self.assertEqual(len(fh.snapshots(self.root)), 1)

    def test_two_runs_in_one_second_are_both_kept_in_the_order_recorded(self):
        fh.record(self.root, digest(("A.one", "-", PLAIN)), now="20260101T000000Z")
        fh.record(self.root, digest(("A.two", "-", PLAIN)), now="20260101T000000Z")
        _, prev, _ = fh.record(self.root, digest(("A.three", "-", PLAIN)), now="20260101T000000Z")
        snaps = fh.snapshots(self.root)
        self.assertEqual([s["failures"][0]["test"] for s in snaps], ["A.one", "A.two", "A.three"])
        self.assertEqual(prev["failures"][0]["test"], "A.two", "compared with the run just before")

    def test_the_same_failures_on_a_later_run_are_a_later_run(self):
        """The digest has no date in it. Yesterday's failures, unchanged,
        after today's fix attempt, is the result that matters most."""
        text = digest(("A.one", "-", REFUSED))
        a, _, fresh_a = fh.record(self.root, text, written=1_767_225_600)
        b, prev, fresh_b = fh.record(self.root, text, "after the token fix", written=1_767_312_000)
        self.assertEqual((fresh_a, fresh_b), (True, True))
        self.assertEqual((a["at"], b["at"]), ("20260101T000000Z", "20260102T000000Z"))
        self.assertEqual(b["label"], "after the token fix")
        out = "\n".join(fh.report(self.root, b, prev))
        self.assertIn("still failing the same way: 1", out)
        self.assertIn("seen in 1 earlier run(s)", out)
        again, _, fresh = fh.record(self.root, text, written=1_767_312_000)
        self.assertFalse(fresh, "the same FILE recorded twice is still one run")

    def test_a_digest_that_lost_rows_says_so(self):
        text = digest(("A.one", "-", PLAIN), ("B.two", "-", REFUSED)).replace(
            "B.two\t-\t" + REFUSED, "B.two\t-")           # cut off mid-line
        snap, prev, _ = fh.record(self.root, text, now="20260101T000000Z")
        out = "\n".join(fh.report(self.root, snap, prev))
        self.assertIn("header says 2 failing pair(s) and 1 could be read", out)

    def test_a_signature_keeps_characters_that_are_not_line_ends(self):
        sig = "AssertionError | page\x0cbreak and\u2028separator"
        p = fh.parse_digest(digest(("A.one", "-", sig)).replace("\n", "\r\n"))
        self.assertEqual(p["failures"][0]["signature"], sig)
        self.assertEqual(fh.parse_digest("\ufeff" + digest(("A.one", "-", PLAIN)))["suite"], "B2B")

    def test_a_digest_older_than_the_test_report_beside_it_is_called_out(self):
        d = os.path.join(self.root, "target", "surefire-reports")
        os.makedirs(d)
        report = os.path.join(d, "TEST-TestSuite.xml")
        with io.open(report, "w", encoding="utf-8") as f:
            f.write('<testsuite name="x" tests="10">')
        snap, prev, _ = fh.record(self.root, digest(("A.one", "-", PLAIN)),
                                  written=os.path.getmtime(report) - 3600)
        self.assertIn("left over from another run", "\n".join(fh.report(self.root, snap, prev)))
        snap, prev, _ = fh.record(self.root, digest(("A.two", "-", PLAIN)),
                                  written=os.path.getmtime(report) + 1)
        self.assertNotIn("left over", "\n".join(fh.report(self.root, snap, prev)))

    def test_only_so_many_runs_are_kept(self):
        old = fh.KEEP
        fh.KEEP = 3
        self.addCleanup(setattr, fh, "KEEP", old)
        for n in range(6):
            self.run_of((f"A.t{n}", "-", PLAIN))
        self.assertEqual(len(fh.snapshots(self.root, "B2B")), 3)

    def test_a_damaged_snapshot_is_skipped_not_fatal(self):
        self.run_of(("A.one", "-", PLAIN))
        with io.open(os.path.join(self.root, fh.STORE, "20250101T000000Z-B2B.json"), "w") as f:
            f.write("{ not json")
        with io.open(os.path.join(self.root, fh.STORE, "20250102T000000Z-B2B.json"), "w") as f:
            json.dump({"suite": "B2B", "failures": "nope"}, f)
        self.assertEqual(len(fh.snapshots(self.root)), 1)


class SeenBefore(Store):
    def test_the_same_signature_in_earlier_runs_is_counted_and_dated(self):
        self.run_of(("A.one", "-", REFUSED))
        self.run_of(("A.one", "-", PLAIN))
        snap, prev, _ = self.run_of(("Z.other", "-", REFUSED))
        text = self.text(snap, prev)
        self.assertIn("seen in 1 earlier run(s), first 20260101T000000Z", text)

    def test_a_note_comes_back_with_its_signature(self):
        snap, _, _ = self.run_of(("A.one", "-", REFUSED))
        sid = fh.signature_id(REFUSED)
        self.assertEqual(fh.add_note(self.root, f"[{sid.upper()}]",
                                     "member and owner used the same email; fixed in the CSV"), REFUSED)
        snap, prev, _ = self.run_of(("B.two", "-", REFUSED))
        self.assertIn("NOTE", self.text(snap, prev))
        self.assertIn("member and owner used the same email", self.text(snap, prev))

    def test_a_note_needs_a_real_signature_and_some_text(self):
        self.run_of(("A.one", "-", REFUSED))
        for sid, text in (("zzzz", "x"), ("0123456789", "x"), (fh.signature_id(REFUSED), "  "),
                          ("../../notes", "x")):
            with self.assertRaises(fh.Unusable, msg=sid):
                fh.add_note(self.root, sid, text)
        self.assertEqual(fh.notes(self.root), {})

    def test_a_new_signature_may_resemble_an_old_one_and_says_why(self):
        self.run_of(("A.one", "-", REFUSED))
        fh.add_note(self.root, fh.signature_id(REFUSED), "the token was for another account")
        near = "FlowStopped | POST /accounts/<*>/members answered 403 Forbidden: role missing"
        snap, prev, _ = self.run_of(("A.one", "-", near))
        text = self.text(snap, prev)
        self.assertIn("not seen in any earlier run", text)
        self.assertIn(f"RESEMBLES [{fh.signature_id(REFUSED)}]", text)
        self.assertIn("a pointer, not a diagnosis", text)
        self.assertIn("status 403", text)
        self.assertIn("its note: the token was for another account", text)

    def test_failures_that_only_share_being_assertions_do_not_resemble(self):
        """Every one of these scored over 50% when `AssertionError`, a
        date and a bare number counted as exception, path and status."""
        for a, b in (
            ("AssertionError | hotel name mismatch expected [Foo] but found [Bar]",
             "AssertionError | <*> expected [true] but found [false]"),
            ("AssertionError | arrival 12/31/2026 rejected",
             "AssertionError | birthday 01/02/1990 not echoed"),
            ("AssertionError | waited 300 ms for 120 rows on port 443",
             "AssertionError | expected [443] rows"),
            ("AssertionError | <*> expected [true] but found [false]",
             "NullPointerException | Cannot invoke String.length() because value is null"),
        ):
            self.assertEqual(fh.resemblance(a, b), (0.0, []), (a, b))

    def test_the_same_refusal_seen_by_a_step_and_by_an_assertion_does_resemble(self):
        score, shared = fh.resemblance(
            REFUSED, "AssertionError | POST /accounts/<*>/members: status expected [201] but found [403]")
        self.assertGreaterEqual(score, fh.RESEMBLES_AT)
        self.assertIn("path /accounts/{}/members", shared)
        self.assertIn("status 403", shared)

    def test_wording_alone_is_not_a_resemblance(self):
        a = "FlowStopped | POST /a/<*>/b answered 409 Conflict: username is not unique"
        b = "IllegalStateException | the username is not unique in the fixture table"
        self.assertEqual(fh.resemblance(a, b)[0], 0.0)

    def test_what_makes_a_failure_recognisable(self):
        f = fh.facets("FlowStopped | POST /accounts/12345/members/<*> answered 403 after 1.5s")
        self.assertEqual(f["status"], {"403"})
        self.assertEqual(f["path"], {"/accounts/{}/members/{}"})
        self.assertEqual(f["exception"], {"FlowStopped"})
        self.assertNotIn("the", f["words"])
        self.assertNotIn("flowstopped", f["words"], "a class name is not wording")
        self.assertEqual(fh.facets("x | took 1.500 seconds, 20260101")["status"], set())
        self.assertEqual(fh.facets("AssertionError | port 443, 300 ms, 120 rows")["status"], set())
        self.assertEqual(fh.facets("AssertionError | on 12/31/2026 at com.hi.api.Foo")["path"], set())
        self.assertEqual(fh.facets("AssertionError | x")["exception"], set(),
                         "a class every failure has says nothing about this one")
        self.assertEqual(fh.facets("AssertionError | status expected [201] but found [409]")["status"],
                         {"201", "409"})


class TheCommand(Store):
    def main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = fh.main(list(argv), root=self.root)
        return rc, out.getvalue()

    def put_digest(self, *failures):
        os.makedirs(os.path.join(self.root, "target"), exist_ok=True)
        with io.open(os.path.join(self.root, "target", "failure-digest.txt"), "w",
                     encoding="utf-8") as f:
            f.write(digest(*failures))

    def test_record_list_show_and_note(self):
        self.put_digest(("A.one", "-", REFUSED), ("A.two", "r2", REFUSED))
        rc, out = self.main("record", "--label", "first")
        self.assertEqual(rc, 0)
        sid = fh.signature_id(REFUSED)
        self.assertIn(f"[{sid}] 2 failure(s)", out)
        rc, out = self.main("record")
        self.assertIn("already recorded", out)
        rc, out = self.main("list")
        self.assertIn("1 run(s) recorded", out)
        self.assertIn("-- first", out)
        rc, out = self.main("note", sid, "owner and member shared an email")
        self.assertEqual(rc, 0)
        rc, out = self.main("show", sid)
        self.assertEqual(rc, 0)
        self.assertIn("A.two [r2]", out)
        self.assertIn("owner and member shared an email", out)
        self.assertEqual(self.main("show", "0000000000")[0], 1)

    def test_a_store_that_cannot_be_written_and_a_bad_id_are_refusals_not_tracebacks(self):
        self.put_digest(("A.one", "-", REFUSED))
        with io.open(os.path.join(self.root, fh.STORE), "w") as f:
            f.write("a file where the directory should be")
        rc, out = self.main("record")
        self.assertEqual(rc, 2)
        self.assertIn("could not be written", out)
        self.assertEqual(self.main("show", "../../etc")[0], 2)

    def test_a_snapshot_missing_its_date_is_skipped(self):
        os.makedirs(os.path.join(self.root, fh.STORE))
        with io.open(os.path.join(self.root, fh.STORE, "x-B2B.json"), "w") as f:
            json.dump({"suite": "B2B", "failures": []}, f)
        self.put_digest(("A.one", "-", REFUSED))
        self.assertEqual(self.main("record")[0], 0)

    def test_a_digest_from_somewhere_else_is_named_as_that(self):
        other = os.path.join(self.root, "copied-digest.txt")
        with io.open(other, "w", encoding="utf-8") as f:
            f.write(digest(("A.one", "-", REFUSED)))
        rc, out = self.main("record", "--digest", other)
        self.assertEqual(rc, 0)
        self.assertIn("not from this project's last run", out)

    def test_no_digest_is_a_refusal_that_says_why(self):
        rc, out = self.main("record")
        self.assertEqual(rc, 2)
        self.assertIn("run the tests first", out)
        self.assertFalse(os.path.isdir(os.path.join(self.root, fh.STORE)))

    def test_everything_is_written_inside_the_store(self):
        self.put_digest(("A.one", "-", REFUSED))
        fh.record(self.root, digest(("A.one", "-", PLAIN), suite="../../escape"),
                  now="20260101T000000Z")
        self.main("record")
        outside = [n for n in os.listdir(os.path.dirname(self.root)) if "escape" in n]
        self.assertEqual(outside, [])
        self.assertEqual(sorted(os.listdir(self.root)), [fh.STORE, "target"])
        self.assertEqual([n for n in os.listdir(os.path.join(self.root, fh.STORE))
                          if n.endswith(".tmp")], [])

    def test_the_store_is_ignored_by_git_in_this_repository(self):
        with io.open(os.path.join(fh.ROOT, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(fh.STORE + "/", f.read().split())


if __name__ == "__main__":
    unittest.main(verbosity=1)
