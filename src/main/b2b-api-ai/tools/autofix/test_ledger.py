"""What autonomy a track record does and does not earn.

The decision these tests pin is the one with the worst failure mode in the
whole loop: letting it apply fixes nobody reads. So the refusals matter
more than the approval, and the defaults are checked for being off.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ledger as lg  # noqa: E402

ON = dict(lg.DEFAULT_POLICY, enabled=True, min_proposals=3,
          min_accept_rate=0.8, max_invariant_rejections=0,
          max_blast_radius=50, auto_kinds=["unit", "compile"])


def rows(*specs):
    """(kind, outcome) pairs, optionally (kind, outcome, blast)."""
    out = []
    for s in specs:
        kind, outcome = s[0], s[1]
        r = {"check": f"c-{kind}", "kind": kind, "outcome": outcome}
        if len(s) > 2:
            r["blast_radius"] = {"total": s[2]}
        out.append(r)
    return out


class Defaults(unittest.TestCase):
    def test_autonomy_ships_off(self):
        self.assertFalse(lg.DEFAULT_POLICY["enabled"])

    def test_the_committed_policy_is_off(self):
        p = lg.load_policy()
        self.assertIn("enabled", p)
        self.assertFalse(p["enabled"],
                         "autonomy must not be enabled by a file in the repo; "
                         "it is a per-machine decision backed by that "
                         "machine's ledger")

    def test_tree_and_runtime_are_not_in_the_default_allowlist(self):
        for k in ("tree", "runtime"):
            self.assertNotIn(k, lg.DEFAULT_POLICY["auto_kinds"])

    def test_a_corrupt_policy_falls_back_to_the_safe_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "autonomy.json")
            io.open(p, "w", encoding="utf-8").write("{ not json")
            self.assertFalse(lg.load_policy(p)["enabled"])

    def test_a_policy_cannot_invent_new_keys(self):
        """An unknown key is ignored rather than becoming a lever."""
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "autonomy.json")
            json.dump({"enabled": True, "bypass_everything": True},
                      io.open(p, "w", encoding="utf-8"))
            self.assertNotIn("bypass_everything", lg.load_policy(p))


class NeverEarned(unittest.TestCase):
    def test_a_tree_check_is_never_auto_applied(self):
        """Only a whole-directory convert is authoritative for a tree
        check, so a green single-suite rung is a signal."""
        perfect = rows(*[("tree", "proposed")] * 50)
        ok, why = lg.eligible("tree", dict(ON, auto_kinds=["tree"]), perfect)
        self.assertFalse(ok)
        self.assertIn("authoritative", why)

    def test_the_guard_suite_is_never_auto_applied(self):
        ok, why = lg.eligible("runtime", dict(ON, auto_kinds=["runtime"]),
                              rows(*[("runtime", "proposed")] * 50))
        self.assertFalse(ok)
        self.assertIn("guard", why)


class Thresholds(unittest.TestCase):
    def test_no_history_is_not_eligible(self):
        ok, why = lg.eligible("unit", ON, [])
        self.assertFalse(ok)
        self.assertIn("no history", why)

    def test_too_few_runs_is_not_eligible(self):
        ok, why = lg.eligible("unit", ON, rows(("unit", "proposed"),
                                               ("unit", "proposed")))
        self.assertFalse(ok)
        self.assertIn("required", why)

    def test_a_clean_record_is_eligible(self):
        ok, why = lg.eligible("unit", ON, rows(*[("unit", "proposed")] * 3))
        self.assertTrue(ok, why)

    def test_one_invariant_rejection_blocks_it(self):
        """An agent that trips the guards is not ready to run without them."""
        r = rows(*[("unit", "proposed")] * 9) + rows(("unit", "rejected"))
        ok, why = lg.eligible("unit", ON, r)
        self.assertFalse(ok)
        self.assertIn("rejection", why)

    def test_a_poor_accept_rate_blocks_it(self):
        r = rows(*[("unit", "proposed")] * 2) + rows(*[("unit", "failed-gate")] * 2)
        ok, why = lg.eligible("unit", ON, r)
        self.assertFalse(ok)
        self.assertIn("accept rate", why)

    def test_a_kind_outside_the_allowlist_is_not_eligible(self):
        ok, why = lg.eligible("compile", dict(ON, auto_kinds=["unit"]),
                              rows(*[("compile", "proposed")] * 9))
        self.assertFalse(ok)
        self.assertIn("auto_kinds", why)

    def test_disabled_beats_a_perfect_record(self):
        ok, _ = lg.eligible("unit", dict(ON, enabled=False),
                            rows(*[("unit", "proposed")] * 99))
        self.assertFalse(ok)


class GuardedIsNotFailure(unittest.TestCase):
    def test_a_rejection_is_not_counted_against_the_accept_rate(self):
        """Counting the guards as unreliability creates pressure to
        loosen them."""
        s = lg.stats(rows(("unit", "proposed"), ("unit", "rejected"),
                          ("unit", "not-attempted")))["kinds"]["unit"]
        self.assertEqual(s["good"], 1)
        self.assertEqual(s["guarded"], 2)
        self.assertEqual(s["judged"], 1)
        self.assertEqual(s["accept_rate"], 1.0)

    def test_a_gate_failure_is_counted(self):
        s = lg.stats(rows(("unit", "proposed"),
                          ("unit", "failed-gate")))["kinds"]["unit"]
        self.assertEqual(s["accept_rate"], 0.5)

    def test_an_error_is_counted_as_a_failure(self):
        s = lg.stats(rows(("unit", "errored")))["kinds"]["unit"]
        self.assertEqual(s["failed"], 1)


class BlastRadiusCap(unittest.TestCase):
    def test_a_wide_fix_is_not_auto_applied(self):
        ok, why = lg.blast_within(ON, {"total": 400})
        self.assertFalse(ok)
        self.assertIn("unread", why)

    def test_a_narrow_fix_is_within_the_cap(self):
        ok, _ = lg.blast_within(ON, {"total": 3})
        self.assertTrue(ok)

    def test_no_measurement_counts_as_zero(self):
        self.assertTrue(lg.blast_within(ON, None)[0])

    def test_the_cap_is_recorded_per_kind_for_review(self):
        s = lg.stats(rows(("unit", "proposed", 7),
                          ("unit", "proposed", 120)))["kinds"]["unit"]
        self.assertEqual(s["max_blast"], 120)


class LedgerFile(unittest.TestCase):
    def test_append_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "l.jsonl")
            lg.append({"check": "a", "kind": "unit", "outcome": "proposed"}, p)
            lg.append({"check": "b", "kind": "unit", "outcome": "rejected"}, p)
            got = lg.load(p)
            self.assertEqual([r["check"] for r in got], ["a", "b"])
            self.assertTrue(all(r.get("at") for r in got))

    def test_a_torn_line_does_not_lose_the_rest(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "l.jsonl")
            lg.append({"check": "a", "kind": "unit", "outcome": "proposed"}, p)
            io.open(p, "a", encoding="utf-8").write('{"half": \n')
            lg.append({"check": "c", "kind": "unit", "outcome": "proposed"}, p)
            self.assertEqual([r["check"] for r in lg.load(p)], ["a", "c"])

    def test_a_missing_ledger_is_empty_not_an_error(self):
        self.assertEqual(lg.load(os.path.join(tempfile.gettempdir(), "nope.jsonl")), [])

    def test_the_policy_file_cannot_be_edited_by_a_proposal(self):
        """Asserted as behaviour, not as membership of a tuple: the rule
        moved from a list of filenames to a directory prefix, and a test
        pinned to the representation failed while the behaviour it cared
        about had just got stronger."""
        import invariants as inv
        v = {x.rule for x in inv.validate(
            [inv.FileDiff(path="tools/autofix/autonomy.json",
                          added=['  "enabled": true'])], frozenset())}
        self.assertIn("self-weakening", v)

    def test_nothing_in_the_loops_own_directory_is_editable(self):
        """Enumerating the guards left 13 of 18 files editable, including
        the fingerprinter and every test that pins the rest."""
        import invariants as inv
        d = os.path.join(inv.ROOT, "tools", "autofix")
        for f in sorted(os.listdir(d)):
            if not f.endswith((".py", ".json", ".md")):
                continue
            rel = f"tools/autofix/{f}"
            v = {x.rule for x in inv.validate(
                [inv.FileDiff(path=rel, added=["x"])], frozenset())}
            self.assertIn("self-weakening", v, rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
