"""Assertion types that must convert to a REAL check, not a TODO stub.

An assertion that falls through to the TODO stub does nothing at runtime,
while the coverage report still counts its case FULL. That is worse than a
visible gap: the suite looks greener than it is.

`HTTP Header Exists` was in exactly that state -- 22 occurrences in
accountdashboardregression, 3 of them active, all silently unconverted. No
unit test caught it; `tools/check_generated_output.py` did, by scanning the
emitted tree for leftover TODO markers.

    python tools/ra_converter/test_assertion_coverage.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ra_converter  # noqa: E402


def _emitter():
    return ra_converter.Emitter(output_dir=".", package_root="com.ak.api")


def test_http_header_exists_is_converted():
    a = ra_converter.Assertion(
        type="HTTP Header Exists",
        name="HTTP Header Exists",
        config={"Header": "content-range"},
    )
    lines, cov = _emitter()._render_assertion(a, "resVar", "get_brandspend_206")
    joined = "\n".join(lines)
    assert "ResponseAsserts.headerExists" in joined, joined
    assert "content-range" in joined, joined
    assert "TODO" not in joined, joined
    assert cov == "FULL", cov


def test_http_header_exists_without_name_stays_todo():
    """No header name means it genuinely cannot be converted.

    Emitting an assertion that checks nothing would be worse than the TODO --
    it would look converted while asserting nothing at all.
    """
    a = ra_converter.Assertion(
        type="HTTP Header Exists", name="HTTP Header Exists", config={})
    lines, cov = _emitter()._render_assertion(a, "resVar", "step")
    assert cov == "TODO", cov
    assert "headerExists" not in "\n".join(lines)


def test_unknown_assertion_type_still_reports_todo():
    """The stub must remain for genuinely unknown types.

    Silently emitting nothing would hide the gap entirely -- the TODO is the
    signal that a human needs to look.
    """
    a = ra_converter.Assertion(
        type="Some Future SoapUI Assertion", name="whatever", config={})
    lines, cov = _emitter()._render_assertion(a, "resVar", "step")
    assert cov == "TODO", cov
    assert "TODO" in "\n".join(lines)


def test_converted_header_assertion_references_a_csv_override():
    """Every converted expectation must stay data-driven, like the others.

    The framework's contract is that what a test asserts is editable in CSV
    without touching Java; a hard-coded assertion would break that.
    """
    a = ra_converter.Assertion(
        type="HTTP Header Exists", name="h", config={"Header": "content-range"})
    lines, _cov = _emitter()._render_assertion(a, "resVar", "myStep")
    joined = "\n".join(lines)
    assert "expected_myStep_header" in joined, joined


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as ex:
            failed += 1
            print(f"FAIL {fn.__name__}: {ex}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)} passed")
