"""suite_impact reports a suite as moved when its output changed, and only then.

The tool exists to answer "what ELSE did my converter change move". It is
worth nothing if it cries wolf: spec and hook numbering is positional, so
adding one spec renumbers every later one in every file that names it. A
text diff would report the whole suite as changed on every run, and the
one real change would be lost in it.

So these pin both directions: renumbering alone is NOT a change, and every
kind of real change IS.

    python tools/test_suite_impact.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import suite_impact as si  # noqa: E402

SUPPORT = "src/main/java/com/hi/api/support"
CSV = "src/test/resources/csv"


def _spec(n, body):
    return ("    static PhaseSpec spec%d() {\n        return %s;\n    }\n" % (n, body))


def _hook(n, name, body):
    return ("    @SuppressWarnings(\"unused\")\n"
            "    static void hook%d_%s(Response res, PhaseContext c) throws Exception {\n"
            "        %s\n    }\n" % (n, name, body))


def _tree(specs, hooks, phases, csv_rows="id,x\n1,a\n", prefix="Alpha", extra=None):
    """A minimal converted module holding one suite, `alpha`."""
    root = tempfile.mkdtemp(prefix="sitest")
    cases = os.path.join(root, SUPPORT, "alpha", "cases")
    os.makedirs(cases)
    with open(os.path.join(cases, prefix + "Specs1.java"), "w", encoding="utf-8") as fh:
        fh.write("public final class %sSpecs1 {\n%s}\n" % (prefix, "".join(specs)))
    with open(os.path.join(cases, prefix + "Hooks1.java"), "w", encoding="utf-8") as fh:
        fh.write("public final class %sHooks1 {\n\n%s}\n" % (prefix, "".join(hooks)))
    with open(os.path.join(cases, "GetXTestPhases.java"), "w", encoding="utf-8") as fh:
        fh.write(phases)
    os.makedirs(os.path.join(root, CSV, "alpha"))
    with open(os.path.join(root, CSV, "alpha", "getX.csv"), "w", encoding="utf-8") as fh:
        fh.write(csv_rows)
    for rel, text in (extra or {}).items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return root


BASE_SPECS = [_spec(1, 'PhaseSpec.phase("enroll").post("/a").build()'),
              _spec(2, 'PhaseSpec.phase("read").get("/b").after(AlphaHooks1::hook1_read).build()')]
BASE_HOOKS = [_hook(1, "read", 'ctx.put("k", "v");')]
BASE_PHASES = ('CaseRegistry.register("alpha", "c1")\n'
               '    .phase("enrollGuest", "enroll", AlphaSpecs1::spec1)\n'
               '    .phase("readX", "read", AlphaSpecs1::spec2);\n')


def _compare(a, b):
    try:
        return si.compare_suite(a, b, "alpha")
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_identical_trees_are_unchanged():
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES),
                 _tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES))
    assert not r["moved"], r


def test_renumbering_alone_is_not_a_change():
    """One spec inserted in front shifts every number after it -- in the
    Specs file, in the hook it references, and in the Phases file."""
    specs = [_spec(1, 'PhaseSpec.phase("read").get("/b").after(AlphaHooks1::hook7_read).build()'),
             _spec(2, 'PhaseSpec.phase("enroll").post("/a").build()')]
    hooks = [_hook(7, "read", 'ctx.put("k", "v");')]
    phases = ('CaseRegistry.register("alpha", "c1")\n'
              '    .phase("enrollGuest", "enroll", AlphaSpecs1::spec2)\n'
              '    .phase("readX", "read", AlphaSpecs1::spec1);\n')
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES),
                 _tree(specs, hooks, phases))
    assert not r["moved"], "renumbering was reported as a change: %r" % (r,)


def test_a_changed_spec_body_is_a_change():
    specs = [BASE_SPECS[0],
             _spec(2, 'PhaseSpec.phase("read").get("/b").query("limit", "2")'
                      '.after(AlphaHooks1::hook1_read).build()')]
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES),
                 _tree(specs, BASE_HOOKS, BASE_PHASES))
    assert r["moved"] and r["specs"] == (1, 1), r


def test_a_changed_hook_body_is_a_change():
    hooks = [_hook(1, "read", 'ctx.put("k", "DIFFERENT");')]
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES),
                 _tree(BASE_SPECS, hooks, BASE_PHASES))
    assert r["moved"] and r["hooks"] == (1, 1), r


def test_an_added_spec_is_a_change_even_though_numbers_are_masked():
    specs = BASE_SPECS + [_spec(3, 'PhaseSpec.phase("extra").get("/c").build()')]
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES),
                 _tree(specs, BASE_HOOKS, BASE_PHASES))
    assert r["moved"] and r["specs"] == (0, 1), r


def test_losing_one_of_two_identical_bodies_is_a_change():
    """Bodies are a multiset. As a set, a duplicate that vanished would
    compare equal and a dropped step would go unreported."""
    twice = BASE_HOOKS + [_hook(2, "read", 'ctx.put("k", "v");')]
    r = _compare(_tree(BASE_SPECS, twice, BASE_PHASES),
                 _tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES))
    assert r["moved"] and r["hooks"] == (1, 0), r


def test_a_changed_registration_is_a_change():
    phases = BASE_PHASES.replace('"readX", "read"', '"readY", "read"')
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES),
                 _tree(BASE_SPECS, BASE_HOOKS, phases))
    assert r["moved"] and any("Phases" in f for f in r["changed"]), r


def test_a_changed_csv_cell_is_a_change():
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES),
                 _tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES, csv_rows="id,x\n1,b\n"))
    assert r["moved"] and r["by_kind"] == {"csv": 1}, r


def test_an_added_and_a_removed_file_are_reported():
    extra = {SUPPORT + "/alpha/SetupHelper.java": "class SetupHelper {}"}
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES),
                 _tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES, extra=extra))
    assert r["moved"] and len(r["added"]) == 1 and not r["removed"], r
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES, extra=extra),
                 _tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES))
    assert r["moved"] and len(r["removed"]) == 1 and not r["added"], r


def test_another_suite_in_the_tree_does_not_leak_into_this_one():
    extra = {SUPPORT + "/beta/cases/BetaSpecs1.java":
             "public final class BetaSpecs1 {\n" + _spec(1, "ONLY_IN_BETA") + "}\n",
             CSV + "/beta/x.csv": "id\n9\n"}
    r = _compare(_tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES),
                 _tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES, extra=extra))
    assert not r["moved"], r


def test_shared_generated_files_are_reported_separately():
    a = _tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES,
              extra={SUPPORT + "/scenario/ScenarioSteps.java": "class ScenarioSteps { }"})
    b = _tree(BASE_SPECS, BASE_HOOKS, BASE_PHASES,
              extra={SUPPORT + "/scenario/ScenarioSteps.java": "class ScenarioSteps { int x; }"})
    try:
        assert si.compare_shared(a, a) == []
        assert si.compare_shared(a, b) == [SUPPORT + "/scenario/ScenarioSteps.java"]
        assert not si.compare_suite(a, b, "alpha")["moved"], (
            "a shared file is not part of any one suite's result")
    finally:
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_the_mask_touches_numbering_and_nothing_else():
    assert si.mask("AlphaSpecs12::spec345") == "AlphaSpecs#::spec#"
    assert si.mask("Hooks2::hook19_read_x") == "Hooks#::hook#_read_x"
    # words that merely contain the tokens, and real data, are left alone
    for keep in ('"spec"', "specification", "hookup", "limit=345",
                 "inspect12", '"hook12"'):
        assert si.mask(keep) == keep, keep


def test_suite_names_follow_the_converter():
    assert si._suite_of("C:/x/TopicsProgramAccountsStgKafka-Events.xml") == \
        "topicsprogramaccountsstgkafka_events"
    assert si._suite_of("EADkafkaevents") == "eadkafkaevents"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
