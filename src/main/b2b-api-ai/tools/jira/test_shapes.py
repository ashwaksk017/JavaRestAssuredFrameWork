"""The shape index, and the two tiers a Jira candidate is scored against.

The expensive mistake this guards is not a missed match. It is a WRONG
match: in this converter a case joined to the wrong cluster inherits
cluster[0]'s body, and every case merged with it changes. So the tests
that matter most are the ones asserting two different requests do NOT
share a signature.

Three of these pin bugs found while building the module, each of which
produced a confident wrong answer rather than an error:

  * the loose tier rewrote the exact tier's output STRING, and `[SNB_]`
    matches key names too -- `{Status:S,Name:S}` became
    `{Vtatus:V,Vame:V}`.
  * dropping only an array's `*N` suffix left `[{id:V}|{id:V}*]`, still
    unequal to a one-element `[{id:V}*]`, so arity never actually
    collapsed.
  * expected status was read off `RestStep.expected_status` and the
    `Assertion` attributes, none of which exist -- it returned "" for all
    1,161 entries, silently, because "" is a plausible answer.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import shapes  # noqa: E402


def one(body="", path="/x", verb="POST", media="application/json",
        path_params=None, query_params=None):
    return shapes.candidate_case([{
        "path": path, "verb": verb, "media_type": media, "body": body,
        "path_params": path_params or {}, "query_params": query_params or {},
    }])


class RealClassesNotAShim(unittest.TestCase):
    def test_a_candidate_is_built_from_the_converters_own_dataclasses(self):
        """A stand-in would be unfaithful in ways that look like poor
        matching quality rather than a plumbing fault."""
        import ra_converter as rc
        case = one('{"a":1}')
        self.assertIsInstance(case, rc.TestCase)
        self.assertTrue(all(isinstance(s, rc.RestStep) for s in case.steps))

    def test_the_exact_signature_is_the_converters_own(self):
        import ra_converter as rc
        case = one('{"a":1}')
        self.assertEqual(shapes.exact_sig(case),
                         json.dumps(rc._rest_shape_sig(case), default=list))


class WhatMustNotCollide(unittest.TestCase):
    """A wrong match is worse than no match."""

    def test_different_verbs_differ(self):
        self.assertNotEqual(shapes.exact_sig(one(verb="POST")),
                            shapes.exact_sig(one(verb="PUT")))

    def test_different_paths_differ(self):
        self.assertNotEqual(shapes.exact_sig(one(path="/a")),
                            shapes.exact_sig(one(path="/b")))

    def test_different_media_types_differ(self):
        """A JSON POST and an XML POST at one path need different
        Content-Type headers; the generated client bakes in the first."""
        self.assertNotEqual(shapes.exact_sig(one('{"a":1}', media="application/json")),
                            shapes.exact_sig(one('{"a":1}', media="application/xml")))

    def test_different_query_param_names_differ(self):
        self.assertNotEqual(
            shapes.exact_sig(one(query_params={"status": "x"})),
            shapes.exact_sig(one(query_params={"force": "x"})))

    def test_different_body_keys_differ(self):
        self.assertNotEqual(shapes.exact_sig(one('{"a":1}')),
                            shapes.exact_sig(one('{"b":1}')))

    def test_nested_structure_is_not_flattened(self):
        self.assertNotEqual(shapes.exact_sig(one('{"a":{"b":1}}')),
                            shapes.exact_sig(one('{"a":1}')))

    def test_a_body_and_no_body_differ(self):
        self.assertNotEqual(shapes.exact_sig(one('{"a":1}')),
                            shapes.exact_sig(one("")))


class WhatMustMatch(unittest.TestCase):
    def test_values_are_not_in_the_key(self):
        """The rule that makes 'add a row' correct: values become CSV
        cells, so two calls differing only in data share one spec."""
        self.assertEqual(shapes.exact_sig(one('{"city":"Houston"}')),
                         shapes.exact_sig(one('{"city":"Dallas"}')))

    def test_query_param_values_are_not_in_the_key(self):
        self.assertEqual(
            shapes.exact_sig(one(query_params={"status": "active"})),
            shapes.exact_sig(one(query_params={"status": "pending"})))

    def test_key_order_does_not_matter(self):
        self.assertEqual(shapes.exact_sig(one('{"a":1,"b":2}')),
                         shapes.exact_sig(one('{"b":2,"a":1}')))


class LooseTier(unittest.TestCase):
    """Why it exists: a recording holds `"${Inputs#n}"` (a string) where a
    story holds `5` (a number). Identical request, different exact
    signature."""

    def test_a_placeholder_matches_a_literal_of_any_type(self):
        rec = one('{"Status":"${Inputs#s}","peakRooms":"${Inputs#n}"}')
        story = one('{"Status":"ok","peakRooms":5}')
        self.assertNotEqual(shapes.exact_sig(rec), shapes.exact_sig(story))
        self.assertEqual(shapes.loose_sig(rec), shapes.loose_sig(story))

    def test_array_arity_collapses(self):
        self.assertEqual(shapes.loose_sig(one('{"i":[{"id":1},{"id":2}]}')),
                         shapes.loose_sig(one('{"i":[{"id":1},{"id":2},{"id":3}]}')))

    def test_key_names_survive_loosening(self):
        """`[SNB_]` also matches key names: Status/Name/user_id became
        Vtatus/Vame/userVid, and two different keys could then collide."""
        for body in ('{"Status":"a"}', '{"Name":"a"}', '{"user_id":1}',
                     '{"Brand":"a"}'):
            loose = shapes.loose_sig(one(body))
            key = json.loads(body).popitem()[0]
            self.assertIn(key, loose, f"{key} was corrupted: {loose}")

    def test_different_keys_still_do_not_collide_when_loosened(self):
        self.assertNotEqual(shapes.loose_sig(one('{"Status":"a"}')),
                            shapes.loose_sig(one('{"Vtatus":"a"}')))

    def test_different_structure_still_does_not_collide_when_loosened(self):
        self.assertNotEqual(shapes.loose_sig(one('{"a":{"b":1}}')),
                            shapes.loose_sig(one('{"a":1}')))

    def test_loosening_does_not_touch_verb_path_or_params(self):
        self.assertNotEqual(shapes.loose_sig(one('{"a":1}', verb="POST")),
                            shapes.loose_sig(one('{"a":1}', verb="PUT")))
        self.assertNotEqual(
            shapes.loose_sig(one('{"a":1}', query_params={"x": "1"})),
            shapes.loose_sig(one('{"a":1}', query_params={"y": "1"})))

    def test_an_empty_body_loosens_to_itself(self):
        self.assertEqual(shapes.loose_body_key(one("").steps[0]), "-")

    def test_a_non_json_body_is_not_silently_treated_as_matching(self):
        a = shapes.loose_sig(one("<xml>a</xml>"))
        b = shapes.loose_sig(one("<xml>b</xml>"))
        self.assertNotEqual(a, b, "a raw body must not collapse to one shape")


class Index(unittest.TestCase):
    """These need the input XMLs and the audit mapping, so they skip on a
    tree that has not been converted."""

    @classmethod
    def setUpClass(cls):
        cls.inputs = shapes.suites_from_inputs()
        cls.mmap = shapes.method_map() if cls.inputs else {}

    def test_every_input_xml_resolves_a_suite_name(self):
        if not self.inputs:
            self.skipTest("no input XMLs")
        for suite, path in self.inputs:
            self.assertTrue(suite and suite == suite.lower(), suite)
            self.assertNotIn("-", suite, "'-' must map to '_'")
            self.assertTrue(os.path.isfile(path), path)

    def test_expected_status_comes_from_the_audit_mapping(self):
        """Read off RestStep/Assertion it was "" for all 1,161 entries."""
        if not self.mmap:
            self.skipTest("no audit mapping; tree not converted")
        with_status = [v for v in self.mmap.values() if v.get("expected_status")]
        self.assertTrue(with_status,
                        "the audit mapping carries expected_status; if this is "
                        "empty the index has lost the duplicate rule's input")

    def test_the_mapping_resolves_a_method_and_a_csv(self):
        if not self.mmap:
            self.skipTest("no audit mapping; tree not converted")
        sample = next(iter(self.mmap.values()))
        self.assertTrue(sample["java_method"])
        self.assertTrue(sample["csv_path"])

    def test_a_built_index_is_self_consistent(self):
        if not self.inputs or not self.mmap:
            self.skipTest("tree not converted")
        idx = shapes.build()
        c = idx["counts"]
        self.assertGreater(c["with_rest_steps"], 0)
        self.assertEqual(c["unmapped_to_method"], 0,
                         "every indexed case must resolve to a method")
        self.assertLessEqual(c["loose_shapes"], c["exact_shapes"],
                             "loosening can only merge shapes, never split them")
        entries = [e for v in idx["exact"].values() for e in v]
        self.assertEqual(len(entries), c["with_rest_steps"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
