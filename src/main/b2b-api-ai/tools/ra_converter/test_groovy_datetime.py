"""java.time date computation must compute AND publish.

Found by sweeping all 7,111 Groovy steps through the translator looking for
scripts that emit almost nothing while reporting FULL coverage. One did: a
step named `TimeStamp` computed four dates and published them via
setPropertyValue, and translated to ZERO Java. Coverage still said FULL, so
`currentTimestamp` (37 consuming steps), `yesterdayDate` (36), `priorMonth`
(31) and `futureMonth` (11) were never set.

Two independent single-line assumptions caused it, and each is pinned below:

  1. `_DATE_ARITH_RX` had no multi-line handling, but authors break the
     fluent chain across lines. Nothing matched, so nothing was emitted.
  2. `_find_setproperty_targets` bound a Properties step only via
     `testRunner.testCase.getTestStepByName(...)`. SoapUI exposes the same
     object as `context.testCase`, which this script used, so every
     published property was dropped.

The first fix attempt added a NEW recognizer for these scripts. That was
wrong: it duplicated the existing one, typed a DateTimeFormatter local as
String, and re-declared variables the original recognizer had already
declared -- three compile errors per affected file. Fixing the two
single-line assumptions instead means one recognizer handles both shapes.

    python tools/ra_converter/test_groovy_datetime.py
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import groovy_translator  # noqa: E402

MULTILINE = """
import java.time.LocalDate
import java.time.format.DateTimeFormatter

def yesterday = LocalDate.now()
        .minusDays(1)
        .format(DateTimeFormatter.ofPattern("yyyy-MM-dd"))

def priorMonth = LocalDate.now()
        .minusMonths(1)
        .format(DateTimeFormatter.ofPattern("yyyy-MM"))

def props = context.testCase.getTestStepByName("TimestampDetails")
props.setPropertyValue("yesterdayDate", yesterday)
props.setPropertyValue("priorMonth", priorMonth)
"""

# Single line, and the formatter is a named local rather than inline.
FORMATTER_REF = """
def formatter = DateTimeFormatter.ofPattern("yyyy-MM-dd")
def arrivalDate = LocalDate.now().plusDays(2).format(formatter)
def props = testRunner.testCase.getTestStepByName("Properties")
props.setPropertyValue("ArrivalDate", arrivalDate)
"""


def _emit(script):
    lines, meta = groovy_translator.translate(script, {}, "dateStep")
    return "\n".join(lines), meta


def _published(java):
    return sorted(set(re.findall(r'putExtracted\(ctx, "([^"]+)"', java)))


def test_multiline_chain_is_translated():
    """A chain broken across lines must still produce Java."""
    java, _ = _emit(MULTILINE)
    assert "String yesterday" in java, java
    assert "java.time.LocalDate.now().minusDays(1)" in java, java
    assert "String priorMonth" in java, java


def test_context_testcase_binding_publishes():
    """`context.testCase` binds a Properties step, same as `testRunner`."""
    java, _ = _emit(MULTILINE)
    pub = _published(java)
    assert "TimestampDetails.yesterdayDate" in pub, pub
    assert "TimestampDetails.priorMonth" in pub, pub


def test_testrunner_binding_still_publishes():
    """The receiver that already worked must keep working."""
    java, _ = _emit(FORMATTER_REF)
    assert "Properties.ArrivalDate" in _published(java), java


def test_formatter_local_is_not_typed_as_string():
    """A DateTimeFormatter local must never be declared `String`.

    The abandoned recognizer emitted `String formatter = ...ofPattern(...)`,
    which does not compile.
    """
    java, _ = _emit(FORMATTER_REF)
    assert "String formatter =" not in java, java


def test_no_duplicate_variable_declarations():
    """Two recognizers emitting the same variable is a compile error."""
    for script in (MULTILINE, FORMATTER_REF):
        java, _ = _emit(script)
        for var in re.findall(r"String (\w+) =", java):
            n = len(re.findall(r"String " + var + r" =", java))
            assert n == 1, f"{var} declared {n} times\n{java}"


def test_emit_is_brace_balanced():
    for script in (MULTILINE, FORMATTER_REF):
        java, _ = _emit(script)
        assert java.count("{") == java.count("}"), java


def test_unrelated_setproperty_script_is_untouched():
    """No date maths -> the date recognizer must not fire at all."""
    other = ('def props = context.testCase.getTestStepByName("Properties")\n'
             'props.setPropertyValue("accountId", "12345")\n')
    java, _ = _emit(other)
    assert "LocalDate arithmetic" not in java, java


INSTANT = """
import java.time.Instant
import java.time.format.DateTimeFormatter

def timestamp = DateTimeFormatter.ISO_INSTANT.format(Instant.now())

def props = context.testCase.getTestStepByName("TimestampDetails")
props.setPropertyValue("currentTimestamp", timestamp)
"""


def test_instant_timestamp_computes_and_publishes():
    """`currentTimestamp` has 37 consumers and used to compute nothing.

    _DATE_ARITH_RX matches only LocalDate.now(); this value comes from
    Instant.now() with the formatter FIRST, so that pattern cannot cover it.
    """
    java, _ = _emit(INSTANT)
    assert "java.time.Instant.now()" in java, java
    assert "DateTimeFormatter.ISO_INSTANT" in java, java
    assert "TimestampDetails.currentTimestamp" in _published(java), java


def test_instant_with_inline_pattern_gets_a_zone():
    """ofPattern cannot format an Instant without a zone -- UTC keeps the
    value stable wherever the suite runs."""
    script = ('def t = DateTimeFormatter.ofPattern("yyyy-MM-dd").format(Instant.now())'
              + "\n"
              + 'def props = context.testCase.getTestStepByName("P")'
              + "\n"
              + 'props.setPropertyValue("stamp", t)')
    java, _ = _emit(script)
    assert "withZone(java.time.ZoneOffset.UTC)" in java, java
    assert "P.stamp" in _published(java), java


def test_instant_and_localdate_share_one_block():
    """Both kinds must land in the SAME scope block, or the publish lines
    referencing them will not compile."""
    combined = MULTILINE.replace(
        "def props =",
        'def stamp = DateTimeFormatter.ISO_INSTANT.format(Instant.now())'
        + "\ndef props =")
    java, _ = _emit(combined)
    assert java.count("{ // [groovy] LocalDate arithmetic") == 1, java
    assert java.count("{") == java.count("}"), java


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
