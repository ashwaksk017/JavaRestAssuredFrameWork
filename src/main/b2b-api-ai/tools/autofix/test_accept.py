"""accept.py writes holes in the gate, so every refusal it makes is pinned.

The failures worth guarding against are the quiet ones: an entry recorded
against a check that was passing (which would suppress the first real
failure there), an entry whose reason says nothing, and an expiry far
enough out to be permanent.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import accept  # noqa: E402

TODAY = "2026-10-03"
GOOD_REASON = ("B2B-422 loses tokenRequest 2; the case registers no bootstrap, "
               "long-standing and still untraced.")


def _records(status="fail", fp="a" * 40, name="step-parity", locate=True):
    rec = {"name": name, "status": status, "kind": "tree", "policy": "prompt"}
    if status in ("fail", "accepted"):
        rec["salient_fingerprint"] = fp
        if locate:
            rec["locate"] = {"suites": ["accountdashboardregression"], "cases": []}
    return {"schema": 1, "git": {"head": "deadbee"}, "checks": [rec]}


class AcceptTool(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.rp = os.path.join(self.td, "records.json")
        self.bp = os.path.join(self.td, "baseline.json")
        io.open(self.bp, "w", encoding="utf-8").write('{"schema": 1, "accepted": []}')

    def _write(self, doc):
        with io.open(self.rp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def _run(self, *extra, reason=GOOD_REASON, expires="2027-01-15", check="step-parity"):
        return accept.main(["--records", self.rp, "--check", check,
                            "--reason", reason, "--expires", expires,
                            "--baseline", self.bp, "--today", TODAY, *extra])

    def _bl(self):
        return json.load(io.open(self.bp, encoding="utf-8"))

    # --- the happy path ------------------------------------------------
    def test_writes_an_entry_with_the_fingerprint_from_the_run(self):
        self._write(_records(fp="b" * 40))
        self.assertEqual(self._run("--owner", "ashwa"), 0)
        e = self._bl()["accepted"][0]
        self.assertEqual(e["salient_fingerprint"], "b" * 40)
        self.assertEqual(e["check"], "step-parity")
        self.assertEqual(e["recorded"], TODAY)
        self.assertEqual(e["owner"], "ashwa")

    def test_carries_the_suites_and_commit_for_later_reconstruction(self):
        self._write(_records())
        self._run()
        e = self._bl()["accepted"][0]
        self.assertEqual(e["suites"], ["accountdashboardregression"])
        self.assertEqual(e["recorded_at_commit"], "deadbee")

    def test_dry_run_writes_nothing(self):
        self._write(_records())
        self.assertEqual(self._run("--dry-run"), 0)
        self.assertEqual(self._bl()["accepted"], [])

    def test_accepting_twice_is_idempotent_not_duplicated(self):
        self._write(_records())
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self._bl()["accepted"]), 1)

    def test_preserves_the_explanatory_comment_in_the_file(self):
        io.open(self.bp, "w", encoding="utf-8").write(
            '{"schema": 1, "_comment": ["rules live here"], "accepted": []}')
        self._write(_records())
        self._run()
        self.assertEqual(self._bl()["_comment"], ["rules live here"])

    # --- refusals -----------------------------------------------------
    def test_refuses_to_accept_a_passing_check(self):
        """The quiet one: it would suppress the first real failure there."""
        self._write(_records(status="pass"))
        self.assertEqual(self._run(), 2)
        self.assertEqual(self._bl()["accepted"], [])

    def test_refuses_to_accept_a_skipped_check(self):
        self._write(_records(status="skipped"))
        self.assertEqual(self._run(), 2)

    def test_refuses_an_unknown_check(self):
        self._write(_records())
        self.assertEqual(self._run(check="no-such-check"), 2)

    def test_refuses_a_missing_records_file(self):
        self.assertEqual(self._run(), 2)

    def test_refuses_a_reason_that_says_nothing(self):
        self._write(_records())
        for bad in ("known issue", "flaky", "TODO", "pre-existing", "expected."):
            self.assertEqual(self._run(reason=bad), 2, bad)
        self.assertEqual(self._bl()["accepted"], [])

    def test_refuses_a_reason_that_is_merely_short(self):
        self._write(_records())
        self.assertEqual(self._run(reason="step parity is off by one here"), 2)

    def test_refuses_an_expiry_in_the_past_or_today(self):
        self._write(_records())
        for bad in ("2026-01-01", TODAY):
            self.assertEqual(self._run(expires=bad), 2, bad)

    def test_refuses_an_expiry_that_is_effectively_permanent(self):
        self._write(_records())
        self.assertEqual(self._run(expires="2030-01-01"), 2)

    def test_refuses_an_unparseable_expiry(self):
        self._write(_records())
        self.assertEqual(self._run(expires="next year"), 2)

    def test_refuses_a_record_with_no_fingerprint(self):
        doc = _records()
        del doc["checks"][0]["salient_fingerprint"]
        self._write(doc)
        self.assertEqual(self._run(), 2)

    # --- the failure changed ------------------------------------------
    def test_refuses_a_changed_fingerprint_without_replace(self):
        self._write(_records(fp="1" * 40))
        self.assertEqual(self._run(), 0)
        self._write(_records(fp="2" * 40))
        self.assertEqual(self._run(), 2, "a changed failure needs confirming")
        self.assertEqual(self._bl()["accepted"][0]["salient_fingerprint"], "1" * 40)

    def test_replace_swaps_the_entry_rather_than_adding_one(self):
        self._write(_records(fp="1" * 40))
        self._run()
        self._write(_records(fp="2" * 40))
        self.assertEqual(self._run("--replace"), 0)
        acc = self._bl()["accepted"]
        self.assertEqual(len(acc), 1)
        self.assertEqual(acc[0]["salient_fingerprint"], "2" * 40)

    # --- the file stays readable by the other half of the system -------
    def test_the_written_entry_satisfies_failure_records_own_matcher(self):
        import failure_record as fr
        self._write(_records(fp="c" * 40))
        self._run()
        bl = self._bl()
        entry, state = fr.match_baseline(bl, "step-parity", "c" * 40,
                                         today=date(2026, 10, 4))
        self.assertEqual(state, "accepted")
        self.assertEqual(entry["reason"], " ".join(GOOD_REASON.split()))

    def test_the_written_entry_has_every_field_the_committed_file_requires(self):
        self._write(_records())
        self._run()
        for required in ("check", "salient_fingerprint", "reason", "expires"):
            self.assertIn(required, self._bl()["accepted"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
