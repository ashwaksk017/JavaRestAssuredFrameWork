"""A truncated chain must never look like a full pass.

A prefix-merged case carries `_stop_after`. Once the chain passes that
REST step, every remaining phase returns the builder unchanged. Before
this guard existed the skip was silent: the test went green having run
fewer steps than its name claims, which is the one failure mode here that
can hide a real defect.

Also pins the companion fix: the typed record from `complete()` is bound
to a local ONLY when a verify helper will read it.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SRC = open(os.path.join(HERE, "ra_converter.py"), encoding="utf-8").read()
# The notifier itself lives in the bundled framework file: the
# scenario base only delegates, because a --classic @Test does
# not extend that base and needs the same implementation.
FRAMEWORK = open(os.path.join(HERE, "framework",
                              "ImportedScenario.java"),
                 encoding="utf-8").read()

GUARD = "Integer.parseInt(__stopAfter))"
NOTIFY = "__noteStoppedEarly(__stopAfter, __restStepIdx)"


def test_the_notifier_is_defined_once_and_shared_by_both_modes():
    """One declaration on the scenario base, one implementation in the
    framework, and the base delegates. Two copies is the fault this
    guards: the phase path and classic must record the same thing the
    same way."""
    assert SRC.count("protected final void __noteStoppedEarly") == 1
    assert "ImportedScenario.noteStoppedEarly(" in SRC, (
        "the base must delegate, not carry its own copy")
    assert FRAMEWORK.count("public static void noteStoppedEarly") == 1


def test_every_emitted_stop_guard_reports_itself():
    """Each place that emits the early-return must also emit the notify
    call, or that code path skips steps in silence."""
    guards = SRC.count(GUARD)
    notifies = SRC.count(NOTIFY)
    assert guards >= 3, guards
    assert notifies == guards, (
        "%d guard site(s) but %d notify call(s)" % (guards, notifies))


def test_no_silent_early_return_survives():
    """The exact shape that used to ship: a guard returning with no trace."""
    assert "Integer.parseInt(__stopAfter)) { return this; }" not in SRC
    assert "Integer.parseInt(__stopAfter)) {{ {early_return} }}" not in SRC


def test_the_notifier_only_fires_once_per_chain():
    """The guard trips on EVERY remaining phase. Without the latch a
    25-phase chain would log the same warning 20 times.

    The latch is the ctx marker rather than a boolean field. It was a
    field, and that is precisely what put this out of reach of a @Test;
    the marker is written either way, so it latches both modes."""
    body = FRAMEWORK.split("public static void noteStoppedEarly", 1)[1][:700]
    assert 'ctx.containsKey("__stoppedEarly")' in body, body[:200]
    assert "return;" in body


def test_the_notifier_records_where_reporting_can_see_it():
    body = FRAMEWORK.split("public static void noteStoppedEarly", 1)[1][:900]
    assert "LOG.warn" in body, "a skipped run must show in the log"
    assert "__stoppedEarly" in body, "and in ctx for the reporting layer"
    assert "Allure.step" in body, "and in the report"
    assert "Allure.step" in body, "and in the report tree"


def test_notifier_takes_its_values_as_arguments():
    """Per-case Support classes redeclare __restStepIdx / __stopAfter,
    shadowing the base fields. Reading them inside the base frame would
    see empty values, so they must be passed in."""
    sig = re.search(r"protected final void __noteStoppedEarly\(([^)]*)\)", SRC)
    assert sig, "notifier signature not found"
    assert "String stopAfter" in sig.group(1), sig.group(1)
    assert "int idx" in sig.group(1), sig.group(1)


def test_scenario_is_bound_only_when_something_reads_it():
    """274 of 599 tests pass the record to a verify helper; the other 325
    bound a local and never touched it."""
    # Window widened and the anchor is now `elif`: a fully-disabled case
    # (every business step disabled upstream) branches first and throws
    # SkipException instead of emitting a chain that would pass empty.
    # To the END OF THE BLOCK, not 3000 characters in. The count was a
    # stand-in for the block and it stopped being one as soon as a branch
    # was added: --classic pushed `else:` past it and this failed on code
    # that still satisfied every assertion below.
    seg = SRC.split("Expected expected = expected(row);", 1)[1]
    seg = seg.split("if not fully_disabled:", 1)[0]
    assert "elif verify_calls:" in seg
    assert "var scenario =" in seg
    assert "else:" in seg
    # the else-branch calls the chain as a statement, with no binding
    else_part = seg.split("else:", 1)[1][:400]
    assert "var scenario" not in else_part, else_part[:200]
    assert ".complete();" in else_part


if __name__ == "__main__":
    failed = 0
    names = [n for n in globals() if n.startswith("test_")]
    for name in names:
        try:
            globals()[name]()
            print("ok  ", name)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", name, "->", e)
    print(f"{len(names) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
