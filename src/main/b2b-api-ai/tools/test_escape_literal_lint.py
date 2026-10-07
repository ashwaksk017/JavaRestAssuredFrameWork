"""A test must not check a REAL escape char while claiming an ESCAPED one.

THE SLIP THIS CATCHES
---------------------
A source literal meaning backslash-n loses a backslash somewhere between
being written and being saved -- a shell heredoc, a copy-paste, an editor
-- and becomes a REAL newline. The assertion then tests the opposite of
what its own message says, and it PASSES, so nothing catches it.

That shipped here. `WholeBodyPlaceholderTest` was written to prove a
request body is NOT double-escaped:

    assertFalse(out.contains(<backslash><backslash>n), "no escaped newlines")

and landed on disk as

    assertFalse(out.contains(<backslash>n), "no escaped newlines")

which asks whether the body contains a real newline. It does -- it is
pretty-printed JSON -- so the assertion was false for a reason that has
nothing to do with escaping. It was only noticed because it happened to
FAIL; had the body been single-line it would have passed for ever while
proving nothing.

THE RULE
--------
Inside a TEST, an assertion whose message talks about escaping must not
compare against a bare one-character escape. Build the two-character
sequence from chars instead -- `String.valueOf('\\') + 'n'` in Java,
`chr(92) + "n"` in Python -- which no amount of re-quoting can collapse.

Production code is NOT linted: `.replace("\n", " ")` in a normaliser is
ordinary and correct. Only assertions can silently invert.

    python tools/test_escape_literal_lint.py
"""

import glob
import io
import os
import re
import sys

BS = chr(92)
Q = chr(34)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# A one-character escape written in source as backslash + n/t/r/quote.
_ONE_CHAR_ESCAPE = Q + re.escape(BS) + "[ntr" + Q + "]" + Q

JAVA_ASSERT = re.compile(
    r'\b(assertTrue|assertFalse|assertEquals|assertNotEquals|'
    r'assertThat)\s*\(')
PY_ASSERT = re.compile(r'(^\s*assert\b|\bassertEqual|\bassertTrue|\bassertFalse)')
USES_ONE_CHAR_ESCAPE = re.compile(_ONE_CHAR_ESCAPE)
TALKS_ESCAPING = re.compile(
    r'escap|backslash|literal|verbatim|double-?escap|unescap', re.I)


def _test_sources():
    pats = [
        os.path.join(ROOT, "src", "test", "java", "**", "*.java"),
        os.path.join(HERE, "**", "test_*.py"),
        os.path.join(HERE, "test_*.py"),
    ]
    out = []
    for p in pats:
        for f in glob.glob(p, recursive=True):
            n = f.replace(BS, "/")
            if "/target/" in n:
                continue
            out.append(f)
    return sorted(set(out))


def _offenders():
    bad = []
    for f in _test_sources():
        is_java = f.endswith(".java")
        try:
            lines = io.open(f, encoding="utf-8", errors="ignore").read().split("\n")
        except OSError:
            continue
        for i, line in enumerate(lines):
            if not USES_ONE_CHAR_ESCAPE.search(line):
                continue
            is_assert = (JAVA_ASSERT.search(line) if is_java
                         else PY_ASSERT.search(line))
            if not is_assert:
                continue
            # The claim: this line plus the message that usually trails it.
            ctx = " ".join(lines[i:i + 3])
            if TALKS_ESCAPING.search(ctx):
                bad.append((f.replace(BS, "/"), i + 1, line.strip()[:130]))
    return bad


def test_no_assertion_checks_a_real_escape_while_claiming_an_escaped_one():
    bad = _offenders()
    if bad:
        msg = [
            "An assertion compares against a ONE-CHARACTER escape while its "
            "message talks about escaping. If the intent was the TWO-character "
            "sequence, build it from chars -- Java: String.valueOf('\\\\') + "
            "'n'; Python: chr(92) + 'n' -- so re-quoting cannot collapse it. "
            "If a real control char really was intended, reword the message so "
            "it does not claim otherwise.",
        ]
        for f, n, line in bad:
            msg.append("  %s:%d\n        %s" % (f, n, line))
        raise AssertionError("\n".join(msg))


def test_the_lint_actually_fires_on_the_shape_it_describes():
    """A lint nobody has seen fail is a lint nobody should trust."""
    sample_bad = '        Assert.assertFalse(out.contains("' + BS + 'n"), "no escaped newlines");'
    assert USES_ONE_CHAR_ESCAPE.search(sample_bad), sample_bad
    assert JAVA_ASSERT.search(sample_bad)
    assert TALKS_ESCAPING.search(sample_bad)

    # and does NOT fire on the char-built form that replaced it
    sample_good = '        Assert.assertFalse(out.contains(ESCAPED_NEWLINE), "no escaped newlines");'
    assert not USES_ONE_CHAR_ESCAPE.search(sample_good), sample_good

    # nor on a doubled escape, which already means the 2-char sequence
    sample_doubled = '        Assert.assertFalse(out.contains("' + BS + BS + 'n"), "no escaped newlines");'
    assert not USES_ONE_CHAR_ESCAPE.search(sample_doubled), sample_doubled


def test_production_code_is_not_linted():
    """`.replace("\\n", " ")` in a normaliser is ordinary and correct."""
    for f in _test_sources():
        n = f.replace(BS, "/")
        assert "/src/test/" in n or "/test_" in n or os.path.basename(n).startswith("test_"), n


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
