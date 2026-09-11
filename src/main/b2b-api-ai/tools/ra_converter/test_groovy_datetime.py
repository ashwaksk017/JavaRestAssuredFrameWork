"""java.time date computation must reach ctx, not vanish.

Found by sweeping all 7,111 Groovy steps through the translator and looking
for scripts that emit almost nothing while reporting FULL coverage. One did:
a step named `TimeStamp` in accountdashboardregression computed four dates
with java.time and published them via setPropertyValue -- and translated to
ZERO Java.

Nothing failed. Coverage said FULL. But `currentTimestamp` (37 consuming
steps), `yesterdayDate` (36), `priorMonth` (31) and `futureMonth` (11) were
never set, so 115 steps ran with unresolved date placeholders.

That is the shape worth guarding: not a crash, not a TODO, just a quiet
absence behind a green number.

    python tools/ra_converter/test_groovy_datetime.py
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import groovy_translator  # noqa: E402

SCRIPT = """
import java.time.Instant
import java.time.LocalDate
import java.time.format.DateTimeFormatter

def timestamp = DateTimeFormatter.ISO_INSTANT.format(Instant.now())

def yesterday = LocalDate.now()
        .minusDays(1)
        .format(DateTimeFormatter.ofPattern("yyyy-MM-dd"))

def priorMonth = LocalDate.now()
        .minusMonths(1)
        .format(DateTimeFormatter.ofPattern("yyyy-MM"))

def props = context.testCase.getTestStepByName("TimestampDetails")
props.setPropertyValue("currentTimestamp", timestamp)
props.setPropertyValue("yesterdayDate", yesterday)
props.setPropertyValue("priorMonth", priorMonth)
log.info("currentTimestamp set: " + timestamp)
"""


def _emit(script=SCRIPT):
    lines, meta = groovy_translator.translate(script, {}, "TimeStamp")
    return "\n".join(lines), meta


def test_every_published_property_reaches_ctx():
    java, _meta = _emit()
    for prop in ("currentTimestamp", "yesterdayDate", "priorMonth"):
        assert f'"TimestampDetails.{prop}"' in java, (
            f"{prop} never published to ctx\n{java}")
        # Consumers reference the bare name as often as the namespaced one.
        assert f'"{prop}"' in java, f"bare {prop} not published\n{java}"


def test_date_expressions_are_valid_java():
    """Pass java.time through with qualified names rather than reimplement it."""
    java, _meta = _emit()
    assert "java.time.LocalDate.now()" in java, java
    assert "java.time.format.DateTimeFormatter.ofPattern" in java, java
    assert "java.time.Instant.now()" in java, java
    # Unqualified names would not compile in the generated file.
    assert not re.search(r"(?<![\w.])LocalDate\s*\.", java), java
    assert not re.search(r"(?<![\w.])DateTimeFormatter\s*\.", java), java


def test_multiline_fluent_chain_is_joined():
    """The chain spans lines with leading dots; each must be one statement."""
    java, _meta = _emit()
    m = re.search(r"String yesterday = (.+?);", java)
    assert m, java
    assert ".minusDays(1)" in m.group(1), m.group(1)
    assert ".format(" in m.group(1), m.group(1)


def test_commented_out_lines_do_not_publish_twice():
    """These scripts keep an older attempt commented above the live one."""
    script = SCRIPT.replace(
        'props.setPropertyValue("currentTimestamp", timestamp)',
        '//props.setPropertyValue("currentTimestamp", timestamp)\n'
        'props.setPropertyValue("currentTimestamp", timestamp)')
    java, _meta = _emit(script)
    assert java.count('"TimestampDetails.currentTimestamp"') == 1, java


def test_recognizer_is_named_so_coverage_is_honest():
    """A FULL score with no named recognizer is how this hid in the first place."""
    _java, meta = _emit()
    assert "java_time_properties" in (meta.get("patterns_matched") or []), meta
    assert meta.get("coverage") == "FULL", meta


def test_unrelated_setproperty_script_is_untouched():
    """The recognizer must not swallow scripts that have nothing to do with dates."""
    other = ('def props = context.testCase.getTestStepByName("Properties")\n'
             'props.setPropertyValue("accountId", "12345")\n')
    _java, meta = _emit(other)
    assert "java_time_properties" not in (meta.get("patterns_matched") or []), meta


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
