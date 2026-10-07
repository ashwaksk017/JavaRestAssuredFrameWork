"""A Properties step must not overwrite a field an earlier Groovy wrote.

In ReadyAPI a Properties step is storage. A Groovy that runs before it
writes into it; the <con:value>s saved in the XML are that Groovy's
LAST-RUN OUTPUT. The emitter turned those saved values into CSV columns
and emitted `seedFromRow(ctx, row, "<step>.")` at the step's position
-- after the Groovy -- so the stale snapshot overwrote the live value
on every row.

Run 7, one thread, 0.1 s apart:

    [availability] -> LONME on 2026-11-09 (inventory 163 > 9)
    -> GET /props/MILHI/groups          <- the value saved in the XML
    <- {"propCode":"MILHI","roomRates":[]}

86 of 161 failures were that one overwrite, through every downstream
step: 0 of 75 POST_Confirm calls succeeded.

The rule is IF-ABSENT, not skip. The first version skipped the seed
outright, and the inventory across 29 suites showed that reaching
`Properties <- DataGenInput` (276 sites), `tokenId <- Token` (46) and
more -- including a Hooks file where the token seed was skipped with no
live publish in sight. When a writer's translation is STUBBED nothing
is published; if-absent then seeds the saved value exactly as before.

Two properties of the implementation are tested here because losing
them is silent: the seed logic reads its OWN case attribute (setting
`_current_case_obj` changed six other emitters' output: +93 bootstrap
hooks in a cold diff), and it refuses a case that does not contain the
step being rendered (a stale case would attribute a writer that never
ran). A patch that fails to land must fail a test.

    python tools/ra_converter/test_properties_seed_respects_groovy_writers.py
"""

import glob
import inspect
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402

_EMITTER = next(v for v in vars(rc).values()
                if isinstance(v, type) and hasattr(v, "_render_step"))


# ------------------------------------------------ the write parser

SPLIT_DEF = '''
def generatedDatesPropertyVal =
    testRunner.testCase.getTestStepByName("generatedDatesAndProps")
def testStep = testRunner.testCase.getTestStepByName("GET_Shop")
for (int i = 0; i < 4; i++) {
    generatedDatesPropertyVal.setPropertyValue("hcrs", hcrs[j])
    generatedDatesPropertyVal.setPropertyValue("arrivalDate", arrivalDates[i])
    testStep.setPropertyValue("hcrs", hcrs[j])
}
'''


def test_a_def_split_across_lines_is_attributed():
    """The real script breaks the line after `=`; that is the dominant
    shape and the one that has to work."""
    w = rc.fields_written_by_groovy(SPLIT_DEF)
    assert w.get("generatedDatesAndProps") == {"hcrs", "arrivalDate"}, w
    assert w.get("GET_Shop") == {"hcrs"}, w


def test_a_direct_chained_write_is_attributed():
    w = rc.fields_written_by_groovy(
        'testRunner.testCase.getTestStepByName("groupid")'
        '.setPropertyValue("groupId", json.groupId)')
    assert w == {"groupid": {"groupId"}}, w


def test_an_unattributable_write_is_NOT_counted():
    """Fail-safe direction: a write whose target cannot be named keeps
    the old behaviour (plain seed), so an unrecognised shape can only
    fail the way it already did."""
    w = rc.fields_written_by_groovy('mystery.setPropertyValue("hcrs", x)')
    assert w == {}, w


# ---------------------------------------------- the emitter decision

def _case(*steps):
    case = rc.TestCase.__new__(rc.TestCase)
    case.steps = list(steps)
    case.name = "synthetic"
    return case


def _groovy(name, script):
    s = rc.GroovyStep.__new__(rc.GroovyStep)
    s.step_name, s.script = name, script
    return s


def _props(name, **values):
    s = rc.PropertiesStep.__new__(rc.PropertiesStep)
    s.step_name, s.properties = name, dict(values)
    return s


def _emitter(case):
    e = _EMITTER.__new__(_EMITTER)
    e._props_seed_case = case
    e._current_case_obj = None
    e._current_prefix = "t"
    e._current_case = "synthetic"

    class _Ledger(object):
        """Accepts every recording call; the test is about the emitted
        Java, not the ledger."""
        def __getattr__(self, _name):
            return lambda *a, **k: None
    e.ledger = _Ledger()
    return e


WRITER = _groovy("search", SPLIT_DEF)


def test_written_fields_are_seeded_only_if_absent():
    p = _props("generatedDatesAndProps", hcrs="MILHI", arrivalDate="2026-11-01",
               other="keep-me")
    case = _case(WRITER, p)
    written, writers = _emitter(case)._fields_written_before(p)
    assert written == {"hcrs", "arrivalDate"}, written
    assert writers == ["search"], writers
    java = chr(10).join(_emitter(case)._render_step(p, "Svc"))
    absent = re.search(r'seedFromRowIfAbsent\(ctx, row, "generatedDatesAndProps\.", ([^)]*)\);', java)
    assert absent, java
    assert '"hcrs"' in absent.group(1) and '"arrivalDate"' in absent.group(1), java
    assert '"other"' not in absent.group(1), java
    assert 'seedFromRow(ctx, row, "generatedDatesAndProps.", "other");' in java, java
    assert "seeded only if absent" in java, java


def test_when_everything_is_written_nothing_is_seeded_unconditionally():
    p = _props("generatedDatesAndProps", hcrs="MILHI", arrivalDate="2026-11-01")
    java = chr(10).join(_emitter(_case(WRITER, p))._render_step(p, "Svc"))
    assert "seedFromRowIfAbsent(" in java, java
    assert re.search(r'\bseedFromRow\(', java) is None, (
        "a written field must never be seeded unconditionally: " + java)


def test_a_properties_step_BEFORE_the_groovy_is_seeded_in_full():
    """Order matters: a Properties step that runs first is input, and
    the Groovy overwriting it afterwards is the normal flow. The emitted
    bytes must equal the pre-fix output exactly."""
    p = _props("generatedDatesAndProps", hcrs="MILHI")
    java = chr(10).join(_emitter(_case(p, WRITER))._render_step(p, "Svc"))
    assert 'seedFromRow(ctx, row, "generatedDatesAndProps.");' in java, java
    assert "IfAbsent" not in java, java


def test_a_groovy_writing_a_DIFFERENT_step_does_not_change_the_seed():
    p = _props("somethingElse", hcrs="MILHI")
    java = chr(10).join(_emitter(_case(WRITER, p))._render_step(p, "Svc"))
    assert 'seedFromRow(ctx, row, "somethingElse.");' in java, java


def test_without_a_case_the_old_behaviour_is_kept():
    """No case in scope means no evidence; the plain seed is the default."""
    p = _props("generatedDatesAndProps", hcrs="MILHI")
    e = _emitter(None)
    java = chr(10).join(e._render_step(p, "Svc"))
    assert 'seedFromRow(ctx, row, "generatedDatesAndProps.");' in java, java


# ------------------------------------- the two silent-loss properties

def test_a_stale_case_that_does_not_contain_the_step_is_refused():
    """_render_step is also reached from emit_setup_helper, outside
    _fluent_render_groups, where the attribute still holds the previous
    case. That case would attribute a writer that never ran before this
    step and quietly demote its seed to if-absent."""
    p = _props("generatedDatesAndProps", hcrs="MILHI")
    stale = _case(WRITER, _props("generatedDatesAndProps", hcrs="X"))
    e = _emitter(stale)                 # same step NAME, different object
    written, writers = e._fields_written_before(p)
    assert written == set() and writers == [], (written, writers)
    java = chr(10).join(e._render_step(p, "Svc"))
    assert 'seedFromRow(ctx, row, "generatedDatesAndProps.");' in java, java


def test_the_seed_logic_does_not_share_the_case_attribute_other_emitters_gate_on():
    """Setting _current_case_obj in _fluent_render_groups changed the
    output of six other emitters (+93 bootstrap hooks, +94 hookOnly
    specs in a cold diff). The seed logic must use its own attribute."""
    src = inspect.getsource(_EMITTER._fluent_render_groups)
    assert "self._props_seed_case = case" in src, (
        "_fluent_render_groups must record the case on _props_seed_case")
    assert "self._current_case_obj = case" not in src, (
        "_fluent_render_groups must not assign _current_case_obj: other "
        "emitters gate on it and their output changes")
    body = inspect.getsource(_EMITTER._fields_written_before)
    assert "_props_seed_case" in body, body


# ------------------------------------------- the real generated tree

def test_no_search_site_is_followed_by_an_unconditional_seed_of_its_outputs():
    """Against every generated Hooks file present. The shape that
    shipped: `AvailabilitySearch.run(...)` then, within a few lines,
    `seedFromRow(ctx, row, "generatedDatesAndProps.")`. An IF-ABSENT
    seed of the same fields is the fix, not a violation."""
    hooks = glob.glob(os.path.join(
        ROOT, "src", "main", "java", "com", "hi", "api", "support",
        "*", "cases", "*Hooks*.java"))
    if not hooks:
        return
    bad = []
    outputs = ("hcrs", "pcrs", "arrivalDate", "departureDate")
    for h in hooks:
        suite = h.replace("\\", "/").split("/support/")[1].split("/")[0]
        lines = io.open(h, encoding="utf-8", errors="replace").read().split(chr(10))
        for i, ln in enumerate(lines):
            if "AvailabilitySearch.run(" not in ln:
                continue
            m = re.search(r'run\(ctx, "[^"]+", "([^"]+)"', ln)
            props = m.group(1) if m else "generatedDatesAndProps"
            for j in range(i, min(i + 25, len(lines))):
                s = lines[j]
                if "seedFromRowIfAbsent(" in s:
                    continue
                if "seedFromRow(ctx, row, " + chr(34) + props + "." not in s:
                    continue
                if s.rstrip().endswith('.");'):
                    bad.append("%s/%s:%d bare seed of %s after a search"
                               % (suite, os.path.basename(h), j + 1, props))
                elif any(chr(34) + o + chr(34) in s for o in outputs):
                    bad.append("%s/%s:%d unconditional seed of a search output: %s"
                               % (suite, os.path.basename(h), j + 1, s.strip()[:80]))
    assert not bad, (
        "a Properties-step seed after the availability search overwrites "
        "the winner with the saved XML snapshot:" + "".join(chr(10) + "  " + b for b in bad))


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
