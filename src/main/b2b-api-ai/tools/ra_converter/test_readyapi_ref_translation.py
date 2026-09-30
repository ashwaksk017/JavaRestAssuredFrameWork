"""ReadyAPI syntax must not reach the runtime as data, by ANY path.

Two paths have now shipped the same bug:

  * a spreadsheet cell holding `${generatedDates#arrivalDate}`, copied into
    the CSV verbatim,
  * a `Simple Contains` token holding `${groupId#roomTypeCode}`, emitted as
    a Java string literal -- so the assertion asked whether the response
    contained those characters. It never can, so the assertion could only
    fail, and it failed with a message that reads like a data problem.

Both now go through one named helper, so a reference means the same thing
wherever it is written. These tests pin the helper and both callers.
"""

import ra_converter


T = ra_converter._translate_readyapi_refs


class _Assertion:
    def __init__(self, atype, config):
        self.type = atype
        self.config = config


def test_a_plain_value_is_returned_untouched():
    for plain in ("2026-10-31", "ABCD", "", "  ", "active", "200",
                  "a token, with a comma"):
        assert T(plain) == plain


def test_a_reference_is_translated():
    assert T("${groupId#roomTypeCode}") == "#groupId_roomTypeCode#"


def test_an_untranslatable_groovy_value_is_left_raw():
    """The converter's own marker as DATA would ship `#groovy_expr#` to
    the server -- the failure the external-secrets fix exists to stop."""
    v = "${= someGroovy() }"
    assert T(v) == v
    assert "groovy_expr" not in T(v)


def test_the_workbook_cell_path_uses_the_same_helper():
    for v in ("${generatedDates#arrivalDate}", "NYCNH", "12"):
        assert ra_converter._translate_workbook_cell(v) == T(v)


def test_a_simple_contains_token_is_translated():
    """The live failure: `Simple Contains: ${groupId#roomTypeCode}`
    expected [true] but found [false] -- forever."""
    a = _Assertion("Simple Contains", {"token": "${groupId#roomTypeCode}"})
    assert ra_converter._assert_default_value(a) == "#groupId_roomTypeCode#"


def test_simple_equals_and_notcontains_are_translated_too():
    for atype in ("Simple Equals", "Simple NotContains"):
        a = _Assertion(atype, {"token": "${s#f}"})
        assert ra_converter._assert_default_value(a) == "#s_f#"


def test_a_literal_token_is_not_disturbed():
    a = _Assertion("Simple Contains", {"token": "ACTIVE"})
    assert ra_converter._assert_default_value(a) == "ACTIVE"


def test_an_empty_token_stays_empty():
    a = _Assertion("Simple Contains", {"token": ""})
    assert ra_converter._assert_default_value(a) == ""


def test_an_sla_assertion_is_untouched():
    """Numeric config must not go anywhere near the translator."""
    a = _Assertion("Response SLA Assertion", {"SLA": "20000"})
    assert ra_converter._assert_default_value(a) == "20000"


def test_none_is_safe():
    assert T(None) is None
    assert T("") == ""
