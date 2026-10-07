"""A fix to a GENERATED file belongs in the emitter, not the committed copy.

`FailureDigestListener.java` is tracked AND generated -- the converter
writes it from an inline f-string, and it is not in
`_AUTHOR_EDITABLE_BASENAMES`, so it has no SKIP-IF-EXISTS protection.

The skipped-check summary was added to the committed copy only. Every
convert then wrote the file back WITHOUT the method, so the `[digest]`
line never printed even though ResponseAsserts had counted 56 skips.
The absence read as a counter bug: resets, duplicate classes, an
untracked second copy and classloader isolation were each investigated
and ruled out, and the method was not in the file at all.

Nothing else would have caught this. The committed copy looked correct
in `git show`, the suite compiled, and the only symptom was a line that
did not appear -- which is indistinguishable from "there was nothing to
report".

    python tools/ra_converter/test_digest_reports_skipped_checks.py
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CONVERTER = os.path.join(HERE, "ra_converter.py")
GENERATED = os.path.join(
    ROOT, "src", "main", "java", "com", "hi", "api", "reporting",
    "FailureDigestListener.java")

BS = chr(92)


def _converter_source():
    return io.open(CONVERTER, encoding="utf-8", errors="replace").read()


def test_the_emitter_declares_the_skipped_check_summary():
    """In the CONVERTER, so a convert cannot remove it."""
    src = _converter_source()
    assert "private void reportSkippedChecks() {{" in src, (
        "the emitted FailureDigestListener must DECLARE "
        "reportSkippedChecks; a fix only in the committed copy is undone "
        "by the next convert")


def test_the_emitter_also_calls_it():
    """Declaring it is not enough -- an uncalled method prints nothing,
    which is the same symptom as a missing one."""
    src = _converter_source()
    assert re.search(r'^\s*reportSkippedChecks\(\);\s*$', src, re.M), (
        "the emitted onFinish must CALL reportSkippedChecks")


def test_the_call_is_after_the_write_once_guard():
    """onFinish can fire twice (suite XML + META-INF/services). Calling
    before the guard prints the summary twice."""
    src = _converter_source()
    guard = src.find("if (!WRITTEN.compareAndSet(false, true)) {{")
    call = src.find("reportSkippedChecks();")
    assert guard != -1 and call != -1, (guard, call)
    assert call > guard, (
        "reportSkippedChecks() must be called AFTER the write-once guard, "
        "or a dual-registered listener prints the summary twice")


def test_the_java_newline_is_a_LITERAL_backslash_n():
    """The emitter is an f-string in Python source, so a single
    backslash-n there becomes a REAL newline in the emitted Java and
    breaks the string literal it sits in.

    This exact mistake was made on the first attempt: the generated file
    had `sb.append("` with the quote unterminated. The source needs a
    DOUBLED backslash.
    """
    src = _converter_source()
    want = "sb.append(" + chr(34) + BS + BS + "n"
    assert want in src, (
        "the emitted Java newline must be written as a doubled backslash "
        "in the converter source, or the generated string literal is "
        "unterminated and nothing in that file compiles")


def test_the_generated_file_carries_it_and_its_literal_is_intact():
    """Against the real emitted file, when a convert has run here."""
    if not os.path.exists(GENERATED):
        return                       # nothing generated in this checkout
    java = io.open(GENERATED, encoding="utf-8", errors="replace").read()
    if not java.rstrip().endswith("}"):
        return                       # mid-write; a convert is running
    assert "reportSkippedChecks" in java, (
        "the generated FailureDigestListener has no skipped-check "
        "summary -- it was written by a converter that predates the fix")
    # The literal must be backslash-n ON ONE LINE, not a real break.
    assert ('sb.append("' + BS + 'n') in java, (
        "the generated Java has a REAL newline inside a string literal")
    # And doubled braces must never leak through the f-string.
    assert "{{" not in java and "}}" not in java, (
        "doubled f-string braces leaked into the generated Java")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
