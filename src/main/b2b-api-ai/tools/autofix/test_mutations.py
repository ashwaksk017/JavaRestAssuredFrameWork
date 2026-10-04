"""The fixture harness edits real files, so its restore is pinned hard.

The harness runs against a temp file with a stub check here, not against
the tree. Running the real fixtures is `mutations.py --all`, which is a
deliberate act: it mutates 2000+ generated files one at a time, and a
unit-test run should not do that as a side effect.

The registry tests DO touch the tree, read-only, because a fixture whose
locator has rotted reports SKIP -- and SKIP is indistinguishable from
"this check has no fixture", which is how the two-sided gate would
silently stop existing.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mutations as mu  # noqa: E402

PY = sys.executable


def _sha(path):
    return hashlib.sha1(io.open(path, "rb").read()).hexdigest()


class Registry(unittest.TestCase):
    def test_every_fixture_names_a_real_check(self):
        """A fixture bound to a check that does not exist never runs, and
        reports NO-CHECK rather than protecting anything."""
        sys.path.insert(0, os.path.join(mu.ROOT, "tools"))
        import verify_all as va
        known = {c.name for c in va.CHECKS}
        for m in mu.MUTATIONS:
            self.assertIn(m.check, known, m.name)

    def test_fixture_names_are_unique(self):
        names = [m.name for m in mu.MUTATIONS]
        self.assertEqual(len(names), len(set(names)))

    def test_every_fixture_explains_what_it_breaks(self):
        for m in mu.MUTATIONS:
            self.assertGreater(len(m.breaks), 25, m.name)

    def test_has_fixture_tracks_the_registry(self):
        self.assertTrue(mu.has_fixture("phase-order"))
        self.assertFalse(mu.has_fixture("no-such-check"))

    def test_check_name_is_read_from_verify_all_not_guessed(self):
        """`check_phase_order.py` is the check `phase-order`; deriving it
        from the filename yields `phase_order` and silently finds no
        fixture."""
        self.assertEqual(mu.check_name_for_script("tools/check_phase_order.py"),
                         "phase-order")
        self.assertEqual(mu.check_name_for_script("tools/check_csv_contracts.py"),
                         "csv-contract")
        self.assertIsNone(mu.check_name_for_script("tools/not_a_check.py"))

    def test_every_locator_still_finds_a_site(self):
        """A rotted locator reports SKIP, which looks exactly like having
        no fixture at all."""
        for m in mu.MUTATIONS:
            site = m.locate()
            if site is None:
                self.skipTest(f"{m.name}: no tree present to locate against")
            path, old, new = site
            self.assertTrue(os.path.exists(path), m.name)
            self.assertIn(old, io.open(path, encoding="utf-8", newline="").read(),
                          f"{m.name}: located text is not in the file")
            self.assertNotEqual(old, new, f"{m.name}: mutation changes nothing")


class Harness(unittest.TestCase):
    """A stub check that fails iff the file contains BROKEN."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.target = os.path.join(self.td, "subject.java")
        # CRLF on purpose: the tree is 100% CRLF, and reading with
        # universal newlines while writing back with newline="" silently
        # rewrites every line ending and fails the restore hash.
        io.open(self.target, "w", encoding="utf-8", newline="").write(
            "line one\r\nGOOD marker\r\nline three\r\n")
        self.probe = os.path.join(self.td, "probe.py")
        io.open(self.probe, "w", encoding="utf-8").write(
            "import io,sys\n"
            "t=io.open(sys.argv[1],encoding='utf-8',newline='').read()\n"
            "sys.exit(1 if 'BROKEN' in t else 0)\n")
        self._orig_cmd = mu._check_cmd
        mu._check_cmd = lambda check: ([PY, self.probe, self.target]
                                       if check == "stub" else None)

    def tearDown(self):
        mu._check_cmd = self._orig_cmd

    def _mutation(self, old="GOOD marker", new="BROKEN marker", check="stub"):
        return mu.Mutation("stub-fixture", check,
                           lambda: (self.target, old, new), "a stubbed property")

    def test_a_catching_check_reports_caught(self):
        r = mu.run_mutation(self._mutation())
        self.assertEqual(r.outcome, "CAUGHT")
        self.assertTrue(r.ok)

    def test_a_blind_check_reports_missed(self):
        r = mu.run_mutation(self._mutation(new="STILL FINE marker"))
        self.assertEqual(r.outcome, "MISSED")
        self.assertFalse(r.ok)
        self.assertIn("still passed", r.detail)

    def test_the_file_is_restored_byte_for_byte_including_crlf(self):
        before = _sha(self.target)
        mu.run_mutation(self._mutation())
        self.assertEqual(_sha(self.target), before)
        self.assertIn(b"\r\n", io.open(self.target, "rb").read())

    def test_restored_even_when_the_check_blows_up(self):
        before = _sha(self.target)
        mu._check_cmd = lambda check: [PY, os.path.join(self.td, "missing.py")]
        mu.run_mutation(self._mutation())
        self.assertEqual(_sha(self.target), before)

    def test_a_locator_that_finds_nothing_skips_rather_than_fails(self):
        r = mu.run_mutation(mu.Mutation("none", "stub", lambda: None, "nothing"))
        self.assertEqual(r.outcome, "SKIP")
        self.assertTrue(r.ok, "an unavailable fixture is not a failure")

    def test_stale_located_text_skips(self):
        r = mu.run_mutation(self._mutation(old="text that is not there"))
        self.assertEqual(r.outcome, "SKIP")

    def test_a_fixture_for_an_unknown_check_reports_no_check(self):
        r = mu.run_mutation(self._mutation(check="nope"))
        self.assertEqual(r.outcome, "NO-CHECK")
        self.assertFalse(r.ok)

    def test_only_the_first_occurrence_is_mutated(self):
        io.open(self.target, "w", encoding="utf-8", newline="").write(
            "GOOD marker\r\nGOOD marker\r\n")
        before = _sha(self.target)
        r = mu.run_mutation(self._mutation())
        self.assertEqual(r.outcome, "CAUGHT")
        self.assertEqual(_sha(self.target), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
