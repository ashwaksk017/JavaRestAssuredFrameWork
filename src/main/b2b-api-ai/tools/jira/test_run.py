"""The orchestrator's only job is to stop in the right place.

Each stage is tested on its own elsewhere. What can only go wrong here is
the wiring: a stage that runs after one that said stop, a verdict read
out of the wrong key, or a refusal reported as a problem with the story.

Every stage is replaced with a stub, so nothing here touches Jira, the
converter or the shape index.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run as runner  # noqa: E402


class Chain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.calls: list = []
        self._saved = {
            "ROOT": runner.ROOT,
            "fetch": runner.fetch.main,
            "extract": runner.extract.main,
            "match": runner.shape_match.main,
            "packet": runner.packet_mod.main,
            "build": runner.shapes.build,
            "save": runner.shapes.save,
        }
        runner.ROOT = self.root
        runner.shapes.build = lambda verbose=False: {"exact": {}}
        runner.shapes.save = lambda index, path: path
        self.addCleanup(self._restore)
        self.addCleanup(self.tmp.cleanup)
        # An index on disk so the rebuild branch is not taken by default.
        os.makedirs(os.path.join(self.root, "target"), exist_ok=True)
        self.index = os.path.join(self.root, "target", "shape-index.json")
        with io.open(self.index, "w", encoding="utf-8") as fh:
            json.dump({"exact": {}, "loose": {}}, fh)

    def _restore(self):
        runner.ROOT = self._saved["ROOT"]
        runner.fetch.main = self._saved["fetch"]
        runner.extract.main = self._saved["extract"]
        runner.shape_match.main = self._saved["match"]
        runner.packet_mod.main = self._saved["packet"]
        runner.shapes.build = self._saved["build"]
        runner.shapes.save = self._saved["save"]

    def outdir(self, key="ABC-1"):
        return os.path.join(self.root, "target", "jira", key)

    def stub(self, name, rc=0, writes=None):
        def fn(argv=None):
            self.calls.append(name)
            for rel, content in (writes or {}).items():
                p = os.path.join(self.outdir(), rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with io.open(p, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(content))
            return rc
        return fn

    def wire(self, fetch_rc=0, extract_rc=0, match_rc=0, verdict=None):
        runner.fetch.main = self.stub("fetch", fetch_rc,
                                      {"story.json": {"summary": {}}}
                                      if fetch_rc == 0 else None)
        runner.extract.main = self.stub("extract", extract_rc,
                                        {"candidate.json": {"extraction": "OK"}}
                                        if extract_rc == 0 else None)
        runner.shape_match.main = self.stub(
            "match", match_rc,
            {"verdict.json": verdict} if verdict is not None else None)
        runner.packet_mod.main = self.stub("packet", 0)

    # --- stopping ----------------------------------------------------
    def test_a_refusal_at_fetch_runs_nothing_else(self):
        self.wire(fetch_rc=2)
        rc = runner.main(["--key", "ABC-1"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls, ["fetch"])

    def test_a_refusal_is_not_reported_as_a_problem_with_the_story(self):
        self.wire(fetch_rc=2)
        out = io.StringIO()
        real, sys.stdout = sys.stdout, out
        try:
            runner.main(["--key", "ABC-1"])
        finally:
            sys.stdout = real
        text = out.getvalue()
        self.assertIn("refused before it reached Jira", text)
        self.assertNotIn("whose rule is unclear", text)

    def test_the_ac_gate_says_the_rule_is_unclear(self):
        self.wire(fetch_rc=1)
        out = io.StringIO()
        real, sys.stdout = sys.stdout, out
        try:
            runner.main(["--key", "ABC-1"])
        finally:
            sys.stdout = real
        self.assertIn("whose rule is unclear", out.getvalue())

    def test_a_failed_extraction_stops_before_matching(self):
        self.wire(extract_rc=1)
        rc = runner.main(["--key", "ABC-1"])
        self.assertEqual(rc, 1)
        self.assertNotIn("match", self.calls)

    def test_a_failed_extraction_still_writes_a_packet(self):
        """Printing a refusal and leaving no artefact is how a story gets
        handed to an agent anyway, minus the file explaining why not."""
        self.wire(extract_rc=1)
        runner.main(["--key", "ABC-1"])
        self.assertEqual(self.calls, ["fetch", "extract", "packet"])

    def test_a_bad_key_is_refused_without_fetching_anything_further(self):
        self.wire()
        self.assertEqual(runner.main(["--key", "not-a-key"]), 2)

    # --- the happy path ----------------------------------------------
    def test_all_four_stages_run_in_order(self):
        self.wire(verdict={"schema": 1, "match": {"verdict": "NEW"}})
        rc = runner.main(["--key", "ABC-1"])
        self.assertEqual(self.calls, ["fetch", "extract", "match", "packet"])
        self.assertEqual(rc, 0)

    def test_the_duplicate_decision_is_read_from_the_nested_match_key(self):
        """shape_match writes {"schema": 1, "match": {...}}. Reading the
        wrapper gave an empty decision and a packet that blamed the
        operator for a step they had run."""
        captured: dict = {}

        def packet_main(argv=None):
            captured["argv"] = list(argv or [])
            self.calls.append("packet")
            return 0

        self.wire(verdict={"schema": 1,
                           "match": {"verdict": "DUPLICATE_SUSPECT",
                                     "duplicate_decision": "skip"}})
        runner.packet_mod.main = packet_main
        runner.main(["--key", "ABC-1", "--on-duplicate", "skip"])
        self.assertIn("--duplicate-choice", captured["argv"])
        at = captured["argv"].index("--duplicate-choice")
        self.assertEqual(captured["argv"][at + 1], "skip")

    def test_a_missing_index_is_built_before_extracting(self):
        os.remove(self.index)
        built: list = []
        runner.shapes.build = lambda verbose=False: built.append(1) or {}
        self.wire(verdict={"schema": 1, "match": {"verdict": "NEW"}})
        runner.main(["--key", "ABC-1"])
        self.assertEqual(len(built), 1)

    def test_an_existing_index_is_not_rebuilt_unless_asked(self):
        built: list = []
        runner.shapes.build = lambda verbose=False: built.append(1) or {}
        self.wire(verdict={"schema": 1, "match": {"verdict": "NEW"}})
        runner.main(["--key", "ABC-1"])
        self.assertEqual(built, [])
        runner.main(["--key", "ABC-1", "--rebuild-index"])
        self.assertEqual(len(built), 1)

    def test_a_match_failure_is_carried_into_the_exit_code(self):
        self.wire(match_rc=1, verdict={"schema": 1, "match": {}})
        self.assertEqual(runner.main(["--key", "ABC-1"]), 1)

    def test_fetch_is_given_the_url_when_one_was_passed(self):
        captured: dict = {}

        def fetch_main(argv=None):
            captured["argv"] = list(argv or [])
            self.calls.append("fetch")
            return 2
        self.wire()
        runner.fetch.main = fetch_main
        runner.main(["--url", "https://jira.example.com/browse/ABC-1",
                     "--api-path", "/rest/api/3", "--no-attachments"])
        self.assertIn("--url", captured["argv"])
        self.assertIn("/rest/api/3", captured["argv"])
        self.assertIn("--no-attachments", captured["argv"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
