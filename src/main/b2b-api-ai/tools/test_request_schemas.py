"""The request-schema check has to find real mismatches and invent none.

The findings it produces are read as "look at this", so a false positive
costs someone a wasted investigation and a missed one costs a run against
a real environment. Both halves are tested here.

The spec-inconsistent case is not hypothetical: the first spec this ran
against declares `required: [guestId, email]` on a definition whose
`properties` only contain `emailAddress`. Every generated payload sends
emailAddress -- correctly -- and a validator that blamed the payload would
have sent someone to "fix" seventeen correct templates.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_request_schemas as RS


def _sev(findings):
    return sorted(s for s, _m in findings)


def test_a_required_field_that_is_absent_is_reported():
    spec = {"definitions": {}}
    schema = {"type": "object", "required": ["activationSource"],
              "properties": {"activationSource": {"type": "string"}}}

    got = RS.check_body(spec, schema, {"other": "x"})

    assert _sev(got) == ["required"], got
    assert "activationSource" in got[0][1]


def test_a_required_field_carrying_the_null_literal_is_reported():
    """`null` is what RestUtilities substitutes for an unresolved
    placeholder, so a required field holding it is a real gap."""
    spec = {"definitions": {}}
    schema = {"type": "object", "required": ["cutOffDate"],
              "properties": {"cutOffDate": {"type": "string"}}}

    got = RS.check_body(spec, schema, {"cutOffDate": "null"})

    assert _sev(got) == ["required"], got


def test_a_schema_requiring_a_field_it_never_defines_blames_the_spec():
    """The finding that justified reporting over rewriting."""
    spec = {"definitions": {}}
    schema = {"type": "object", "required": ["email"],
              "properties": {"emailAddress": {"type": "string"}}}

    got = RS.check_body(spec, schema, {"emailAddress": "a@b.c"})

    assert _sev(got) == ["spec-inconsistent"], got
    assert "contradicts itself" in got[0][1]


def test_a_satisfied_body_produces_nothing():
    spec = {"definitions": {}}
    schema = {"type": "object", "required": ["a"],
              "properties": {"a": {"type": "string"}}}

    assert RS.check_body(spec, schema, {"a": "v"}) == []


def test_a_malformed_date_is_caught():
    """The Excel bug, statically: `2026-12-17 00:00:00` against
    `format: date`. This is the class the check exists to catch before a
    run rather than after one."""
    spec = {"definitions": {}}
    schema = {"type": "object",
              "properties": {"startDate": {"type": "string", "format": "date"}}}

    got = RS.check_body(spec, schema, {"startDate": "2026-12-17 00:00:00"})

    assert _sev(got) == ["format"], got


def test_a_well_formed_date_is_not_flagged():
    spec = {"definitions": {}}
    schema = {"type": "object",
              "properties": {"startDate": {"type": "string", "format": "date"}}}

    assert RS.check_body(spec, schema, {"startDate": "2026-12-17"}) == []


def test_an_unresolved_placeholder_is_not_judged_on_its_text():
    """A placeholder's VALUE is unknown at convert time. Reporting
    `#startDate#` as a malformed date would flag almost every template."""
    spec = {"definitions": {}}
    schema = {"type": "object",
              "properties": {"startDate": {"type": "string", "format": "date"}}}

    assert RS.check_body(spec, schema, {"startDate": "#startDate#"}) == []


def test_an_enum_violation_is_caught():
    spec = {"definitions": {}}
    schema = {"type": "object",
              "properties": {"housingMethod": {"type": "string",
                                               "enum": ["individual", "master"]}}}

    got = RS.check_body(spec, schema, {"housingMethod": "nonsense"})

    assert _sev(got) == ["enum"], got


def test_a_pattern_violation_is_caught():
    spec = {"definitions": {}}
    schema = {"type": "object",
              "properties": {"roomTypeCode": {"type": "string",
                                              "pattern": "^[A-Z]{4}$"}}}

    got = RS.check_body(spec, schema, {"roomTypeCode": "[]"})

    assert _sev(got) == ["pattern"], got


def test_a_ref_is_followed():
    spec = {"definitions": {"Inner": {"type": "object", "required": ["x"],
                                      "properties": {"x": {"type": "string"}}}}}
    schema = {"type": "object",
              "properties": {"inner": {"$ref": "#/definitions/Inner"}}}

    got = RS.check_body(spec, schema, {"inner": {"y": 1}})

    assert _sev(got) == ["required"], got
    assert got[0][1].startswith("inner."), got


def test_a_dangling_ref_is_survived():
    """A spec that points at a definition it does not contain must not
    crash the convert."""
    spec = {"definitions": {}}
    schema = {"type": "object",
              "properties": {"inner": {"$ref": "#/definitions/Nope"}}}

    assert RS.check_body(spec, schema, {"inner": {"y": 1}}) == []


def test_a_non_dict_body_is_survived():
    spec = {"definitions": {}}
    schema = {"type": "object", "required": ["a"], "properties": {}}

    assert RS.check_body(spec, schema, []) == []
    assert RS.check_body(spec, schema, None) == []
    assert RS.check_body(spec, None, {"a": 1}) == []


def test_path_shapes_compare_by_structure():
    """{guestId} and {id} are the same endpoint."""
    assert RS._norm("/guests/{guestId}/businesses") == \
        RS._norm("/GUESTS/{id}/businesses/")
