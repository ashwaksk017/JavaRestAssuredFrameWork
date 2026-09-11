"""A captured value and the code that reads it must agree on the ctx key.

Reported from a remote run: Salesforce tokens, and other values captured from
responses, arrived empty in later requests.

Three producer/consumer key mismatches caused it. None was a compile error --
`ctxGet` returns "" for an unknown key -- so every one of them looked like an
environment problem at runtime.

  1. BARE WHOLE-RESPONSE REF. `_STEP_RESPONSE_RX` requires a third `#`
     segment (`${Step#Response#field}`), so `${Step#Response}` matched
     NOTHING. No extract was emitted at all, and the Groovy that read it
     got "". 62 reads across the suite.

  2. GROOVY FIELD REF. The translator synthesized `<Step>.<field>` while
     the emitter's auto-extract publishes `<Step>_Response_<field>`.

  3. REST PATH / PARAM / HEADER REF. Same mismatch, in
     `soapui_expr_to_java`'s ctx fallbacks -- including the Headers variant
     (`<Step>.Header_<n>` vs `<Step>_Response_Header_<n>`).

And one adjacent bug in the same family:

  4. ASSERTION FALLBACKS kept the raw ReadyAPI value, so
     `${Step#Request#$['a']['b']}` reached runtime untranslated.
     `PlaceholderResolver` cannot match that shape -- its key pattern
     excludes `[`, `'` and `$` -- so the assertion compared the LITERAL
     against the response and failed every time. The XML uses `#Request#`
     117 times.

    python tools/ra_converter/test_ctx_key_conventions.py
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ra_converter as RC  # noqa: E402
import groovy_translator  # noqa: E402


# ------------------------------------------------------------------ defect 1
def test_bare_response_ref_is_recognised():
    """`${Step#Response}` must be seen by the demand scan."""
    m = RC._STEP_RESPONSE_BARE_RX.search("${Partition_before_200#Response}")
    assert m and m.group(1) == "Partition_before_200"


def test_bare_response_ref_is_not_matched_by_the_field_regex():
    """Pins WHY a second pattern is needed rather than a tweak."""
    assert not RC._STEP_RESPONSE_RX.search("${Partition_before_200#Response}")


def test_field_response_ref_still_matches_the_field_regex():
    m = RC._STEP_RESPONSE_RX.search("${S#Response#$['a']['b']}")
    assert m and m.group(1) == "S"


def test_bare_variants_are_recognised():
    for variant in ("", "AsXml", "AsJson", "Headers", "AsHtml"):
        ref = "${S#Response" + variant + "}"
        assert RC._STEP_RESPONSE_BARE_RX.search(ref), ref


# ------------------------------------------------------------------ defect 2
def _expand(inner):
    return groovy_translator._soapui_ref_to_java(inner, {}) \
        if hasattr(groovy_translator, "_soapui_ref_to_java") else None


def test_groovy_whole_response_uses_the_underscore_key():
    java = groovy_translator.translate(
        'def r = context.expand(\'${Partition_before_200#Response}\')\n',
        {}, "s")[0]
    blob = "\n".join(java)
    assert '"Partition_before_200_Response"' in blob, blob
    assert '"Partition_before_200.Response"' not in blob, blob


def test_groovy_field_response_uses_the_underscore_key():
    """Only when the source step's response var is out of scope -- in scope
    it extracts directly, which is better."""
    java = groovy_translator.translate(
        "def r = context.expand('${http_request_200_2#Response#$[\\'a\\'][\\'b\\']}')\n",
        {}, "s")[0]
    blob = "\n".join(java)
    if "ctxGet" in blob:
        assert "_Response_" in blob, blob
        assert '"http_request_200_2.a_b"' not in blob, blob


# ------------------------------------------------------------------ defect 3
def _rest_expr(expr):
    out = RC.soapui_expr_to_java(expr, response_var_by_step={})
    return out if isinstance(out, str) else str(out)


def test_rest_field_ref_uses_the_underscore_key():
    got = _rest_expr("${http_request_200_2#Response#$['alternateAccounts']['salesforceId']}")
    assert "_Response_" in got, got
    assert "http_request_200_2.alternateAccounts" not in got, got


def test_rest_header_ref_uses_the_underscore_key():
    got = _rest_expr("${S#ResponseHeaders#X-Req-Id}")
    assert "_Response_Header_" in got, got
    assert '"S.Header_' not in got, got


def test_emitter_publishes_the_key_the_reader_uses():
    """The two halves must agree -- that is the whole defect.

    `_needed_response_extracts` is what writes; the expression translator is
    what reads. Same step, same field, same key.
    """
    class _Case:
        steps = []

    step = RC.RestStep(
        step_name="src", service="s", method_name="m",
        resource_path="/x", http_method="GET", endpoint="", original_uri="",
        media_type="application/json",
        request_body="${src#Response#$['a']['b']}",
        headers={}, path_params={}, query_params={})
    case = _Case()
    case.steps = [step]
    published = RC._needed_response_extracts("src", case)
    assert "src_Response_a_b" in published, published

    read = _rest_expr("${src#Response#$['a']['b']}")
    # The reader either extracts from the in-scope response var, or reads the
    # published key -- never a key nobody writes.
    if "ctxGet" in read:
        assert "src_Response_a_b" in read, read


def test_bare_ref_publishes_the_whole_response():
    class _Case:
        steps = []

    step = RC.RestStep(
        step_name="src", service="s", method_name="m",
        resource_path="/x", http_method="GET", endpoint="", original_uri="",
        media_type="application/json",
        request_body="${src#Response}",
        headers={}, path_params={}, query_params={})
    case = _Case()
    case.steps = [step]
    published = RC._needed_response_extracts("src", case)
    assert "src_Response" in published, published
    assert published["src_Response"] == "", published


def test_url_refs_are_scanned_for_demand():
    """The ref can live in the URL, not only in a parameter bag."""
    class _Case:
        steps = []

    step = RC.RestStep(
        step_name="src", service="s", method_name="m",
        resource_path="/a/${src#Response#$['id']}", http_method="GET",
        endpoint="", original_uri="", media_type="application/json",
        request_body="", headers={}, path_params={}, query_params={})
    case = _Case()
    case.steps = [step]
    assert "src_Response_id" in RC._needed_response_extracts("src", case)


# ------------------------------------------------------------------ defect 4
def test_assertion_fallback_translates_readyapi_refs():
    got = RC.assertion_fallback_literal(
        "${http_request_200_createAccount#Request#$['contactInfo']['name']}")
    assert "${" not in got, got
    assert "#" in got, got


def test_assertion_fallback_leaves_plain_values_alone():
    assert RC.assertion_fallback_literal("Hilton") == RC._jlit("Hilton")
    assert RC.assertion_fallback_literal("") == RC._jlit("")


def test_placeholder_resolver_cannot_parse_the_untranslated_shape():
    """Pins WHY translation is required rather than leaving it to runtime."""
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.#-]*)\}")
    raw = "${http_request_200_createAccount#Request#$['contactInfo']['name']}"
    assert not pattern.search(raw), (
        "if this ever matches, the runtime could resolve it and the emit-time "
        "translation would be optional")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("ok  " + fn.__name__)
        except Exception as ex:
            failed += 1
            print("FAIL " + fn.__name__ + ": " + repr(ex))
    if failed:
        print("")
        print(str(failed) + " of " + str(len(tests)) + " FAILED")
        sys.exit(1)
    print(str(len(tests)) + " passed")
