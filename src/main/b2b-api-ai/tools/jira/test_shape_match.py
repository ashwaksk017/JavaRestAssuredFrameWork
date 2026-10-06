"""Verdicts, and the two places this must refuse to guess.

The expensive mistake is not a missed match. It is confidently naming ONE
destination when the call is automated in several, or calling a loose
match exact -- in this converter a case attached to the wrong cluster
inherits cluster[0]'s body, and every case merged with it changes.
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

import shape_match as sm  # noqa: E402
import shapes  # noqa: E402


def cand(body='{"a":1}', path="/x", verb="POST", status="", key="K-1",
         media="application/json", path_params=None, query_params=None):
    return {"issue_key": key, "steps": [{
        "verb": verb, "path": path, "media_type": media, "body": body,
        "path_params": path_params or {}, "query_params": query_params or {},
        "expected_status": status}]}


def index_of(*cases_with_meta):
    """Build a tiny index: (candidate dict, entry overrides) pairs."""
    exact, loose, step_exact, step_loose = {}, {}, {}, {}
    for n, (c, meta) in enumerate(cases_with_meta, 1):
        case = shapes.candidate_case(c["steps"], c.get("issue_key", "x"))
        # A distinct case_id per case, as the real index has: the
        # converter writes a GUID. Without one, two different cases in
        # the same method deduplicated into one target and the rollup
        # under-counted.
        entry = {"suite": "s", "case_id": f"case-{n}", "case_name": f"c{n}",
                 "java_class_fqn": "C",
                 "java_method": "m", "csv_path": "csv/s/C/m.csv",
                 "expected_status": "",
                 "rest_steps": len(c["steps"])}
        entry.update(meta)
        exact.setdefault(shapes.exact_sig(case), []).append(entry)
        loose.setdefault(shapes.loose_sig(case), []).append(entry)
        for which, target in ((False, step_exact), (True, step_loose)):
            for idx, name, sig in shapes.step_sigs(case, loose=which):
                target.setdefault(sig, []).append(
                    dict(entry, step_index=idx, step_name=name))
    return {"exact": exact, "loose": loose,
            "step_exact": step_exact, "step_loose": step_loose}


def flow(*steps, key="FLOW-1"):
    """A multi-step recorded case, as a converted flow actually is."""
    out = []
    for n, (verb, path) in enumerate(steps, 1):
        out.append({"verb": verb, "path": path,
                    "media_type": "application/json", "body": "",
                    "path_params": {}, "query_params": {},
                    "expected_status": ""})
    return {"issue_key": key, "steps": out}


class Verdicts(unittest.TestCase):
    def test_no_match_is_new(self):
        idx = index_of((cand(path="/a"), {}))
        got = sm.match(cand(path="/b"), idx)
        self.assertEqual(got["verdict"], sm.NEW)
        self.assertEqual(got["targets"], [])

    def test_same_call_different_values_is_a_row(self):
        """The rule that makes 'add a row' correct: values are CSV cells."""
        idx = index_of((cand(body='{"city":"Houston"}'), {}))
        got = sm.match(cand(body='{"city":"Dallas"}'), idx)
        self.assertEqual(got["verdict"], sm.EXACT_ONE)
        self.assertIn("add a data row", got["action"])

    def test_same_call_same_status_is_a_suspected_duplicate(self):
        idx = index_of((cand(status="204"), {"expected_status": "204"}))
        got = sm.match(cand(status="204"), idx)
        self.assertEqual(got["verdict"], sm.DUPLICATE_SUSPECT)

    def test_same_call_different_status_is_not_a_duplicate(self):
        """A 200 and a 400 of one call share a method and differ per row."""
        idx = index_of((cand(status="200"), {"expected_status": "200"}))
        got = sm.match(cand(status="400"), idx)
        self.assertEqual(got["verdict"], sm.EXACT_ONE)

    def test_several_targets_are_all_reported(self):
        """Measured: only 25 of 153 shared shapes map to one method. Naming
        one would be a guess dressed as an answer."""
        idx = index_of((cand(), {"java_method": "m1", "case_name": "c1"}),
                       (cand(), {"java_method": "m2", "case_name": "c2"}))
        got = sm.match(cand(), idx)
        self.assertEqual(got["verdict"], sm.EXACT_MANY)
        self.assertEqual(got["target_count"], 2)
        self.assertIn("choose", got["action"])

    def test_a_literal_matching_a_placeholder_is_loose_not_exact(self):
        """A recording holds "${Inputs#n}" (a string) where a story holds 5."""
        idx = index_of((cand(body='{"n":"${Inputs#n}"}'), {}))
        got = sm.match(cand(body='{"n":5}'), idx)
        self.assertEqual(got["verdict"], sm.LOOSE_ONLY)
        self.assertEqual(got["target_count"], 1)
        self.assertIn("confirm", got["action"])

    def test_a_loose_match_is_never_reported_as_exact(self):
        idx = index_of((cand(body='{"n":"${Inputs#n}"}'), {}))
        got = sm.match(cand(body='{"n":5}'), idx)
        self.assertEqual(got["exact_matches"], [])


class PerCallFallback(unittest.TestCase):
    """A story describes ONE call; a recording is a whole flow.

    Matching only whole cases answered NEW for calls that were
    demonstrably automated -- the worst answer available, because it
    reads as permission to write a duplicate. These tests pin the
    fallback and, just as importantly, what it still refuses to claim.
    """

    def test_a_single_call_is_found_inside_a_recorded_flow(self):
        index = index_of((flow(("POST", "/token"), ("POST", "/enroll"),
                               ("POST", "/activate")), {}))
        got = sm.match(cand(body="", path="/activate"), index)
        self.assertNotEqual(got["verdict"], sm.NEW)
        self.assertEqual(got["match_level"], "step")

    def test_it_says_which_step_of_how_many(self):
        index = index_of((flow(("POST", "/token"), ("POST", "/enroll"),
                               ("POST", "/activate")), {}))
        got = sm.match(cand(body="", path="/activate"), index)
        self.assertEqual(got["targets"][0]["step_index"], 3)
        self.assertEqual(got["targets"][0]["rest_steps"], 3)

    def test_a_whole_flow_match_stays_at_case_level(self):
        """The fallback must not fire when the real thing matched."""
        c = flow(("POST", "/token"), ("POST", "/activate"), key="K-1")
        got = sm.match(c, index_of((c, {})))
        self.assertEqual(got["match_level"], "case")

    def test_a_call_in_no_flow_is_still_NEW(self):
        index = index_of((flow(("POST", "/token")), {}))
        got = sm.match(cand(body="", path="/nowhere"), index)
        self.assertEqual(got["verdict"], sm.NEW)

    def test_partial_coverage_is_not_coverage(self):
        """Two of the story's three calls matching is not 'already
        automated'. Reporting a hit would send an author to add a row to
        a test that never makes the third call."""
        index = index_of((flow(("POST", "/a"), ("POST", "/b")), {}))
        three = flow(("POST", "/a"), ("POST", "/b"), ("POST", "/c"))
        got = sm.match(three, index)
        self.assertEqual(got["verdict"], sm.NEW)

    def test_only_cases_containing_every_call_are_listed(self):
        """The recorded flows are deliberately LONGER than the story, so
        no whole-case signature matches and the per-call fallback is the
        thing under test. An earlier version of this used two-step flows,
        which matched at case level and exercised none of it."""
        index = index_of(
            (flow(("POST", "/a"), ("POST", "/b"), ("POST", "/c")),
             {"java_method": "hasAandB"}),
            (flow(("POST", "/a"), ("POST", "/z"), ("POST", "/y")),
             {"java_method": "hasAonly"}),
        )
        got = sm.match(flow(("POST", "/a"), ("POST", "/b")), index)
        self.assertEqual(got["match_level"], "step")
        methods = {t["java_method"] for t in got["targets"]}
        self.assertEqual(methods, {"hasAandB"})

    def test_a_case_missing_one_of_the_calls_is_excluded_not_unioned(self):
        """A union here would send an author to add a row to a test that
        never makes the second call at all."""
        index = index_of(
            (flow(("POST", "/a"), ("POST", "/b"), ("POST", "/c")),
             {"java_method": "hasAandB"}),
            (flow(("POST", "/a"), ("POST", "/z"), ("POST", "/y")),
             {"java_method": "hasAonly"}),
        )
        got = sm.match(flow(("POST", "/a"), ("POST", "/b")), index)
        self.assertEqual(got["target_count"], 1)
        self.assertEqual(got["target_method_count"], 1)

    def test_a_duplicate_is_never_claimed_from_a_per_call_match(self):
        """The index records expected status per CASE, so matching it at
        step level says the flow ends in 204, not that this call does."""
        index = index_of((flow(("POST", "/token"), ("POST", "/activate")),
                          {"expected_status": "204"}))
        got = sm.match(cand(body="", path="/activate", status="204"), index)
        self.assertEqual(got["verdict"], sm.EXACT_MANY
                         if got["target_count"] > 1 else sm.EXACT_ONE)
        self.assertNotEqual(got["verdict"], sm.DUPLICATE_SUSPECT)

    def test_a_duplicate_is_still_claimed_from_a_whole_flow_match(self):
        c = flow(("POST", "/token"), ("POST", "/activate"))
        c["steps"][0]["expected_status"] = "204"
        got = sm.match(c, index_of((c, {"expected_status": "204"})))
        self.assertEqual(got["verdict"], sm.DUPLICATE_SUSPECT)

    def test_targets_are_rolled_up_by_method(self):
        index = index_of(
            (flow(("POST", "/a"), ("POST", "/x")), {"java_method": "m1"}),
            (flow(("POST", "/b"), ("POST", "/x")), {"java_method": "m1"}),
            (flow(("POST", "/c"), ("POST", "/x")), {"java_method": "m2"}),
        )
        got = sm.match(cand(body="", path="/x"), index)
        self.assertEqual(got["target_count"], 3)
        self.assertEqual(got["target_method_count"], 2)
        self.assertEqual(got["target_methods"][0]["cases"], 2)

    def test_a_call_in_many_methods_is_flagged_as_a_building_block(self):
        index = index_of(*[
            (flow(("POST", f"/u{i}"), ("POST", "/shared")),
             {"java_method": f"m{i}"})
            for i in range(sm.SHARED_BLOCK_METHODS + 2)])
        got = sm.match(cand(body="", path="/shared"), index)
        self.assertTrue(got["shared_building_block"])
        self.assertIn("BUILDING BLOCK", sm.report(got))

    def test_a_call_in_a_few_methods_is_not_flagged(self):
        index = index_of(
            (flow(("POST", "/u1"), ("POST", "/shared")), {"java_method": "m1"}),
            (flow(("POST", "/u2"), ("POST", "/shared")), {"java_method": "m2"}),
        )
        got = sm.match(cand(body="", path="/shared"), index)
        self.assertFalse(got["shared_building_block"])

    def test_an_index_without_the_per_step_maps_still_works(self):
        """An index dumped before per-step matching existed must not
        crash the matcher -- it just cannot fall back."""
        c = cand(body="", path="/activate")
        got = sm.match(c, {"exact": {}, "loose": {}})
        self.assertEqual(got["verdict"], sm.NEW)


class MustNotCollide(unittest.TestCase):
    def test_a_different_verb_does_not_match(self):
        idx = index_of((cand(verb="POST"), {}))
        self.assertEqual(sm.match(cand(verb="PUT"), idx)["verdict"], sm.NEW)

    def test_a_different_media_type_does_not_match(self):
        idx = index_of((cand(media="application/json"), {}))
        self.assertEqual(
            sm.match(cand(media="application/xml"), idx)["verdict"], sm.NEW)

    def test_different_query_param_names_do_not_match(self):
        idx = index_of((cand(query_params={"status": "a"}), {}))
        self.assertEqual(
            sm.match(cand(query_params={"force": "a"}), idx)["verdict"], sm.NEW)


class Destination(unittest.TestCase):
    def test_a_generated_target_says_the_row_belongs_upstream(self):
        d = sm.destination({"java_class_fqn":
                            "com.hi.api.tests.imported.s.a.XTest",
                            "csv_path": "csv/s/a/XTest/m.csv"})
        self.assertIn("ReadyAPI XML", d)
        self.assertIn("overwrites", d)

    def test_a_CLASSIC_generated_target_also_says_upstream(self):
        """--classic emits into `.tests.classic.`, not `.tests.imported.`.

        Without that root here, a classic test reads as hand-written and
        this tells the user to edit a file the next convert overwrites.
        No csv_path, so the `/csv/` fallback cannot mask it."""
        d = sm.destination({"java_class_fqn":
                            "com.hi.api.tests.classic.s.a.XTest"})
        self.assertIn("ReadyAPI XML", d)
        self.assertIn("overwrites", d)

    def test_a_hand_written_target_says_this_repository(self):
        d = sm.destination({"java_class_fqn": "com.hi.api.tests.jira.MyTest",
                            "csv_path": "csv/manual/MyTest/m.csv"})
        self.assertIn("this repository", d)


class DuplicateDecision(unittest.TestCase):
    DUP = {"verdict": sm.DUPLICATE_SUSPECT}

    def test_a_pipeline_that_cannot_be_asked_must_not_pick(self):
        self.assertEqual(sm.resolve_duplicate(self.DUP, "fail"), "fail")

    def test_explicit_modes_are_honoured(self):
        self.assertEqual(sm.resolve_duplicate(self.DUP, "skip"), "skip")
        self.assertEqual(sm.resolve_duplicate(self.DUP, "create"), "create")

    def test_a_non_duplicate_never_prompts(self):
        self.assertEqual(
            sm.resolve_duplicate({"verdict": sm.EXACT_ONE}, "prompt"), "create")


class Input(unittest.TestCase):
    def _write(self, doc):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
        json.dump(doc, fh)
        fh.close()
        return fh.name

    def test_a_step_with_no_path_is_refused(self):
        p = self._write({"steps": [{"verb": "POST"}]})
        try:
            with self.assertRaises(SystemExit):
                sm.load_candidate(p)
        finally:
            os.unlink(p)

    def test_an_empty_steps_array_is_refused(self):
        p = self._write({"steps": []})
        try:
            with self.assertRaises(SystemExit):
                sm.load_candidate(p)
        finally:
            os.unlink(p)

    def test_a_valid_candidate_loads(self):
        p = self._write(cand())
        try:
            self.assertEqual(len(sm.load_candidate(p)["steps"]), 1)
        finally:
            os.unlink(p)


class AgainstTheRealIndex(unittest.TestCase):
    """Skips unless the index has been built for this tree."""

    @classmethod
    def setUpClass(cls):
        p = os.path.join(shapes.ROOT, "target", "shape-index.json")
        cls.index = shapes.load(p) if os.path.isfile(p) else None

    def test_a_recorded_call_matches_itself(self):
        if not self.index:
            self.skipTest("no shape index built")
        sig, ents = next(iter(self.index["exact"].items()))
        steps = json.loads(sig)
        verb, path, media = steps[0][0], steps[0][1], steps[0][2]
        got = sm.match({"issue_key": "R", "steps": [
            {"verb": verb, "path": path, "media_type": media, "body": "",
             "path_params": {}, "query_params": {}}]}, self.index)
        # The body is not reconstructed here, so this asserts only that a
        # real path/verb resolves to SOMETHING rather than erroring.
        self.assertIn(got["verdict"],
                      (sm.NEW, sm.EXACT_ONE, sm.EXACT_MANY, sm.LOOSE_ONLY,
                       sm.DUPLICATE_SUSPECT))

    def test_report_renders_for_every_verdict(self):
        for v in (sm.NEW, sm.EXACT_ONE, sm.EXACT_MANY, sm.LOOSE_ONLY,
                  sm.DUPLICATE_SUSPECT):
            text = sm.report({"issue_key": "K", "verdict": v,
                              "action": sm.ACTION[v], "expected_status": "",
                              "exact_fingerprint": "x" * 70, "targets": [],
                              "target_count": 0})
            self.assertIn(v, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
