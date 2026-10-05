"""The ladder and the blast-radius manifest.

Two of these pin mistakes made while building it, because both were
invisible and both would have been believed:

  * `full-convert` issuing fifteen single-suite converts instead of one
    directory convert. Fast, green, and NOT the authoritative output --
    the worst combination, because it looks authoritative.
  * `target-check` judging on exit code alone, so the accepted
    `tokenRequest 2` artifact failed the rung for every candidate fix,
    forever.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import failure_record as fr  # noqa: E402
import ladder as L  # noqa: E402
import manifest as mf  # noqa: E402


class SuiteResolution(unittest.TestCase):
    def test_every_input_maps_to_a_support_directory(self):
        xmls, support = L.input_xmls(), set(fr.known_suites())
        if not xmls or not support:
            self.skipTest("no tree/inputs present")
        self.assertEqual(set(xmls) - support, set(),
                         "an input whose derived name matches no support dir "
                         "means the convert silently targets nothing")
        self.assertEqual(support - set(xmls), set())

    def test_the_hyphen_case_is_the_one_that_breaks(self):
        """`topicsprogramaccountsstgkafka-Events.xml` is the suite
        `topicsprogramaccountsstgkafka_events`."""
        xmls = L.input_xmls()
        if not xmls:
            self.skipTest("no inputs present")
        self.assertIn("topicsprogramaccountsstgkafka_events", xmls)
        self.assertTrue(xmls["topicsprogramaccountsstgkafka_events"]
                        .endswith("topicsprogramaccountsstgkafka-Events.xml"))

    def test_mixed_case_inputs_resolve(self):
        xmls = L.input_xmls()
        if not xmls:
            self.skipTest("no inputs present")
        for s in ("eadkafkaevents", "h4bbookonbehalf", "ltareservationsluxurybrands"):
            self.assertIn(s, xmls, s)

    def test_unknown_suites_are_reported_not_silently_dropped(self):
        """The real suite is taken from the inputs that are PRESENT.

        This named `amexbackbook`, which made the test a statement about
        one machine's input directory rather than about resolve_suites. On
        a tree holding a single XML it failed with
        ['amexbackbook', 'no_such_suite'] != ['no_such_suite'] -- a true
        report of a missing input, read as a bug in the code under test.
        """
        xmls = L.input_xmls()
        if not xmls:
            ok, missing = L.resolve_suites(["no_such_suite"])
            self.assertEqual(missing, ["no_such_suite"])
            self.assertEqual(ok, [])
            return
        real = sorted(xmls)[0]
        ok, missing = L.resolve_suites([real, "no_such_suite"])
        self.assertEqual(missing, ["no_such_suite"])
        self.assertEqual(ok, [real])


class Sharding(unittest.TestCase):
    def test_batches_fit_the_command_budget(self):
        for batch in L.shard([f"s{i}" for i in range(15)]):
            self.assertLessEqual(len(batch) * L.SECONDS_PER_SUITE, L.SHARD_BUDGET_S)

    def test_nothing_is_lost_or_duplicated(self):
        suites = [f"s{i}" for i in range(15)]
        flat = [s for b in L.shard(suites) for s in b]
        self.assertEqual(flat, suites)

    def test_a_single_suite_is_one_batch(self):
        self.assertEqual(L.shard(["only"]), [["only"]])


class Rungs(unittest.TestCase):
    def test_order_is_cheapest_first(self):
        self.assertEqual(L.RUNG_NAMES,
                         ("unit", "repro-convert", "compile", "target-check",
                          "static", "full-convert", "full-verify"))

    def test_full_convert_is_one_directory_command_not_fifteen(self):
        """Fifteen single-suite converts would be fast, green, and not the
        authoritative output -- `--input <dir>` exists so cross-suite reuse
        is computed over the whole set."""
        cmds = L._full_convert_cmds({"suites": [], "checks": []})
        self.assertEqual(len(cmds), 1)
        self.assertIn(L.INPUT_DIR, cmds[0][1])

    def test_repro_convert_is_one_command_per_suite(self):
        cmds = L._repro_convert_cmds({"suites": ["amexbackbook"], "checks": []})
        if not L.input_xmls():
            self.skipTest("no inputs present")
        self.assertEqual(len(cmds), 1)
        self.assertIn("amexbackbook.xml", " ".join(cmds[0][1]))

    def test_full_convert_gets_a_timeout_longer_than_the_work(self):
        need = len(L.input_xmls() or range(15)) * L.SECONDS_PER_SUITE
        self.assertGreater(L.timeout_for("full-convert", 1700), need)
        self.assertEqual(L.timeout_for("compile", 1700), 1700)

    def test_every_builder_returns_name_command_pairs(self):
        ctx = {"suites": [], "checks": ["step-parity"]}
        for r in L.RUNGS:
            for item in r.build(ctx):
                self.assertEqual(len(item), 2, r.name)
                name, cmd = item
                self.assertTrue(name is None or isinstance(name, str), r.name)
                self.assertIsInstance(cmd, list, r.name)

    def test_only_the_convert_and_verify_rungs_are_authoritative(self):
        auth = {r.name for r in L.RUNGS if r.authoritative}
        self.assertEqual(auth, {"unit", "compile", "full-convert", "full-verify"})


class BaselineAwareJudging(unittest.TestCase):
    """The accepted artifact must not fail a rung on every candidate."""

    PARITY = ("[step-parity] 1 case(s) lose steps\n"
              "    B2B-422 1/17 step(s) unreachable: tokenRequest 2\n")

    def setUp(self):
        self._run = L._run
        fp = fr.fingerprints(self.PARITY)[1]
        self.baseline = {"schema": 1, "accepted": [{
            "check": "step-parity", "salient_fingerprint": fp,
            "reason": "x" * 50, "recorded": "2026-10-03",
            "expires": "2099-01-01"}]}

        def fake(cmd, timeout):
            if "check_step_parity.py" in " ".join(cmd):
                return False, 0.1, self.PARITY
            return True, 0.1, "ok"
        L._run = fake

    def tearDown(self):
        L._run = self._run

    def test_an_accepted_failure_passes_the_rung(self):
        got = L.climb([], ["step-parity"], "target-check", baseline=self.baseline)
        self.assertEqual(got["outcome"], "stopped")
        rung = next(r for r in got["rungs"] if r["rung"] == "target-check")
        self.assertEqual(rung["status"], "pass")
        self.assertEqual(rung["accepted"], ["step-parity"])

    def test_the_same_failure_fails_without_a_baseline(self):
        got = L.climb([], ["step-parity"], "target-check",
                      baseline={"schema": 1, "accepted": []})
        self.assertEqual(got["outcome"], "failed")
        self.assertEqual(got["at"], "target-check")

    def test_a_changed_failure_is_not_accepted(self):
        L._run = lambda cmd, timeout: (
            (False, 0.1, self.PARITY.replace("1/17", "4/17"))
            if "check_step_parity.py" in " ".join(cmd) else (True, 0.1, "ok"))
        got = L.climb([], ["step-parity"], "target-check", baseline=self.baseline)
        self.assertEqual(got["outcome"], "failed")

    def test_it_stops_at_the_first_failing_rung(self):
        L._run = lambda cmd, timeout: (False, 0.1, "boom")
        got = L.climb([], ["step-parity"], "full-verify", baseline=self.baseline)
        self.assertEqual(got["at"], "unit", "a bad edit must cost seconds")


class Records(unittest.TestCase):
    def test_suites_and_checks_come_from_a_records_file(self):
        doc = {"failing_suites": ["amexbackbook"],
               "checks": [{"name": "a", "status": "fail"},
                          {"name": "b", "status": "pass"},
                          {"name": "c", "status": "accepted"}]}
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.json")
            json.dump(doc, open(p, "w"))
            suites, checks = L.suites_from_records(p)
        self.assertEqual(suites, ["amexbackbook"])
        self.assertEqual(checks, ["a", "c"], "a passing check is not a target")


class Manifest(unittest.TestCase):
    def test_suite_attribution(self):
        self.assertEqual(
            mf.suite_of("src/test/resources/csv/amexbackbook/x/y.csv"), "amexbackbook")
        self.assertEqual(
            mf.suite_of("src/main/java/com/hi/api/support/eadkafkaevents/cases/S.java"),
            "eadkafkaevents")

    def test_shared_files_belong_to_no_suite(self):
        """Attributing support/CtxFields.java to a suite would report a
        tree-wide change as suite-local."""
        self.assertEqual(mf.suite_of("src/main/java/com/hi/api/support/CtxFields.java"), "")

    def test_reports_are_excluded_by_default(self):
        self.assertNotIn("_audit", mf.MANIFEST_ROOTS)
        self.assertNotIn("_flows", mf.MANIFEST_ROOTS)

    def test_compare_classifies_and_groups_by_suite(self):
        before = {"count": 2, "files": {
            "src/test/resources/csv/a/x.csv": "1",
            "src/test/resources/csv/b/y.csv": "2"}}
        after = {"count": 2, "files": {
            "src/test/resources/csv/a/x.csv": "9",
            "src/test/resources/csv/c/z.csv": "3"}}
        d = mf.compare(before, after)
        self.assertEqual(d["changed"], ["src/test/resources/csv/a/x.csv"])
        self.assertEqual(d["removed"], ["src/test/resources/csv/b/y.csv"])
        self.assertEqual(d["added"], ["src/test/resources/csv/c/z.csv"])
        self.assertEqual(d["total"], 3)
        self.assertEqual(set(d["by_suite"]), {"a", "b", "c"})

    def test_identical_snapshots_report_no_change(self):
        snap = {"count": 1, "files": {"src/test/resources/csv/a/x.csv": "1"}}
        d = mf.compare(snap, snap)
        self.assertEqual(d["total"], 0)
        self.assertIn("No change", mf.format_report(d))

    def test_mismatched_report_scope_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "a.json"), os.path.join(td, "b.json")
            mf.save({"schema": 1, "include_reports": True, "files": {}}, a)
            mf.save({"schema": 1, "include_reports": False, "files": {}}, b)
            self.assertEqual(mf.main(["--compare", a, b]), 2)

    def test_a_real_snapshot_finds_the_generated_tree(self):
        snap = mf.snapshot()
        if not snap["count"]:
            self.skipTest("no generated tree present")
        self.assertGreater(snap["count"], 100)
        self.assertTrue(snap["git"].get("head"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
