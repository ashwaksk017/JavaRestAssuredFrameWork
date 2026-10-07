"""A suite's vocabulary lives on the suite's own class, so converting one
suite cannot take a method away from another.

`enrollGuest()`, `createProgramAccount()` and the other chain methods used
to be emitted on the shared `support/scenario/ScenarioSteps`, built from
whichever suites were in the run. Every suite extends that class, so
converting ONE XML rewrote it from one suite's names and the other suites'
tests stopped compiling -- or, where a union had been added to prevent
that, kept compiling only as long as the union was complete. Three such
unions existed and each was written after a build broke.

What this pins:

* the shared base carries NO vocabulary of its own any more;
* a suite's steps base carries exactly that suite's, and says so with a
  marker;
* a tree converted before this change keeps working: while an unmarked
  suite is on disk and outside the run, the shared base carries its
  methods forward;
* a name registered after the steps base was written is reported, not
  left for javac.

    python tools/ra_converter/test_suite_vocab_isolation.py
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import phase_model as pm  # noqa: E402
import ra_converter as rc  # noqa: E402

_PHASE = re.compile(r"public S (\w+)\(\) throws Exception \{\s*return runPhase\(")
_PHASE_STEP = re.compile(
    r"public S (\w+)\(String step\) throws Exception \{\s*return runPhase\(")
_VERIFY = re.compile(r"public S (\w+)\(\) throws Exception \{\s*runVerify\(")


def _fresh(suite, out):
    """An Emitter in phase-spec mode with nothing rendered yet."""
    rc._SUITES_THIS_RUN.clear()
    rc._SUITE_VOCAB_EMITTED.clear()
    pm.reset_run()
    em = rc.Emitter(output_dir=out, package_root="com.hi.api",
                    ledger=rc.AuditLedger(), suite_name=suite)
    em.phase_specs_enabled = True
    em.classic_enabled = False
    rc._SUITES_THIS_RUN.add(suite)
    return em


def _read(out, *rel):
    path = os.path.join(out, "src", "main", "java", "com", "hi", "api",
                        "support", *rel)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _legacy_suite(out, suite, vocab_on_shared_base):
    """A suite as the converter emitted it BEFORE per-suite vocabulary:
    an unmarked steps base, and its methods on the shared class."""
    base = os.path.join(out, "src", "main", "java", "com", "hi", "api", "support")
    os.makedirs(os.path.join(base, suite, "scenario"), exist_ok=True)
    with open(os.path.join(base, suite, "scenario",
                           suite.capitalize() + "Steps.java"), "w",
              encoding="utf-8") as fh:
        fh.write("public abstract class %sSteps<S> extends ScenarioSteps<S> {}\n"
                 % suite.capitalize())
    os.makedirs(os.path.join(base, "scenario"), exist_ok=True)
    body = "".join(
        "    public S %s() throws Exception {\n        return runPhase(\"%s\", null);\n    }\n"
        % (v, v) for v in vocab_on_shared_base)
    with open(os.path.join(base, "scenario", "ScenarioSteps.java"), "w",
              encoding="utf-8") as fh:
        fh.write("public abstract class ScenarioSteps<S> {\n" + body + "}\n")


# --- the shared base -----------------------------------------------------

def test_the_shared_base_carries_no_vocabulary():
    out = tempfile.mkdtemp(prefix="vocab")
    try:
        em = _fresh("alpha", out)
        em._spec_vocabs = {"enrollGuest", "createProgramAccount"}
        em._spec_verify_vocabs = {"Insights": {"verifyProgramAccount"}}
        pm.RUN_VOCABS.update({"enrollGuest", "fromAnotherSuite"})
        rc._ALL_VERIFY_VOCABS.add("verifyFromAnotherSuite")
        em._emit_scenario_steps("AlphaClient")
        java = _read(out, "scenario", "ScenarioSteps.java")
        assert not _PHASE.findall(java), _PHASE.findall(java)
        assert not _PHASE_STEP.findall(java), _PHASE_STEP.findall(java)
        assert not _VERIFY.findall(java), _VERIFY.findall(java)
        # still the engine every suite needs
        for member in ("protected S runPhase(String vocab, String step)",
                       "public void runVerify(String vocab, String step)",
                       "protected io.restassured.response.Response dispatch("):
            assert member in java, "engine member missing: " + member
    finally:
        rc._ALL_VERIFY_VOCABS.discard("verifyFromAnotherSuite")
        shutil.rmtree(out, ignore_errors=True)


def test_the_shared_base_is_the_same_whichever_suite_wrote_it():
    """The point of the change, stated as bytes: nothing in the shared
    class may depend on which suite's names were in the run."""
    outs = []
    try:
        for suite, vocabs in (("alpha", {"enrollGuest"}),
                              ("beta", {"shopProperty", "createReservation"})):
            out = tempfile.mkdtemp(prefix="vocab")
            outs.append(out)
            em = _fresh(suite, out)
            em._spec_vocabs = set(vocabs)
            pm.RUN_VOCABS.update(vocabs)
            em._emit_scenario_steps(suite.capitalize() + "Client")
        a = _read(outs[0], "scenario", "ScenarioSteps.java")
        b = _read(outs[1], "scenario", "ScenarioSteps.java")
        assert a == b, "ScenarioSteps differs between a run of alpha and a run of beta"
    finally:
        for out in outs:
            shutil.rmtree(out, ignore_errors=True)


# --- the suite's own class -----------------------------------------------

def test_a_suite_gets_exactly_its_own_vocabulary():
    out = tempfile.mkdtemp(prefix="vocab")
    try:
        em = _fresh("alpha", out)
        em._spec_vocabs = {"enrollGuest", "verifyProgramAccount"}
        em._spec_verify_vocabs = {"Insights": {"verifyProgramAccount",
                                               "verifyMemberInvite"}}
        # another suite in the same process must not leak in
        pm.RUN_VOCABS.update({"shopProperty"})
        rc._ALL_VERIFY_VOCABS.add("verifyRatePlan")
        rel = em._emit_suite_steps_base("AlphaClient")
        java = open(os.path.join(out, rel), encoding="utf-8").read()
        assert rc.SUITE_VOCAB_MARKER in java
        assert sorted(_PHASE.findall(java)) == ["enrollGuest", "verifyProgramAccount"]
        assert sorted(_PHASE_STEP.findall(java)) == ["enrollGuest", "verifyProgramAccount"]
        # both-phase-and-verify keeps ONE method, the phase one
        assert _VERIFY.findall(java) == ["verifyMemberInvite"], _VERIFY.findall(java)
        assert "shopProperty" not in java and "verifyRatePlan" not in java
        assert rc._SUITE_VOCAB_EMITTED["alpha"][0] == {"enrollGuest", "verifyProgramAccount"}
    finally:
        rc._ALL_VERIFY_VOCABS.discard("verifyRatePlan")
        shutil.rmtree(out, ignore_errors=True)


def test_a_name_with_a_text_path_method_keeps_only_the_step_overload():
    """`taken` is the rule the shared base applied; it moved with the
    methods. A no-arg vocabulary method beside a text-path method of the
    same name would be a duplicate declaration."""
    out = tempfile.mkdtemp(prefix="vocab")
    try:
        em = _fresh("alpha", out)
        em._spec_vocabs = {"enrollGuest", "createProgramAccount"}
        pm.RUN_TAKEN.add("createProgramAccount")
        rel = em._emit_suite_steps_base("AlphaClient")
        java = open(os.path.join(out, rel), encoding="utf-8").read()
        assert _PHASE.findall(java) == ["enrollGuest"], _PHASE.findall(java)
        assert sorted(_PHASE_STEP.findall(java)) == ["createProgramAccount", "enrollGuest"]
    finally:
        shutil.rmtree(out, ignore_errors=True)


# --- a tree converted before the change ----------------------------------

def test_an_unmarked_suite_outside_the_run_is_legacy():
    out = tempfile.mkdtemp(prefix="vocab")
    try:
        _legacy_suite(out, "oldsuite", ["enrollGuest"])
        rc._SUITES_THIS_RUN.clear()
        assert rc._legacy_vocab_suites(out, "com.hi.api") == ["oldsuite"]
        # being converted in this run: its file is about to be replaced
        rc._SUITES_THIS_RUN.add("oldsuite")
        assert rc._legacy_vocab_suites(out, "com.hi.api") == []
    finally:
        rc._SUITES_THIS_RUN.clear()
        shutil.rmtree(out, ignore_errors=True)


def test_a_marked_suite_and_a_classic_suite_are_not_legacy():
    out = tempfile.mkdtemp(prefix="vocab")
    try:
        em = _fresh("alpha", out)
        em._spec_vocabs = {"enrollGuest"}
        em._emit_suite_steps_base("AlphaClient")
        # a --classic suite has support/<suite>/ but no scenario package
        os.makedirs(os.path.join(out, "src", "main", "java", "com", "hi", "api",
                                 "support", "classicsuite"))
        rc._SUITES_THIS_RUN.clear()
        assert rc._legacy_vocab_suites(out, "com.hi.api") == []
        assert rc._legacy_vocab_suites(os.path.join(out, "nowhere"), "com.hi.api") == []
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_legacy_methods_are_carried_forward_then_dropped():
    out = tempfile.mkdtemp(prefix="vocab")
    try:
        _legacy_suite(out, "oldsuite", ["enrollGuest", "shopProperty"])
        em = _fresh("alpha", out)
        em._spec_vocabs = {"createReservation"}
        em._emit_scenario_steps("AlphaClient")
        java = _read(out, "scenario", "ScenarioSteps.java")
        assert sorted(_PHASE.findall(java)) == ["enrollGuest", "shopProperty"], (
            "the legacy suite's methods must survive a convert it was not part of")
        assert "createReservation" not in java, (
            "the suite being converted carries its own; it adds nothing here")

        # reconvert the legacy suite: nothing is left to carry
        rc._SUITES_THIS_RUN.add("oldsuite")
        em._emit_scenario_steps("AlphaClient")
        java = _read(out, "scenario", "ScenarioSteps.java")
        assert not _PHASE.findall(java), _PHASE.findall(java)
    finally:
        rc._SUITES_THIS_RUN.clear()
        shutil.rmtree(out, ignore_errors=True)


# --- the completeness check ----------------------------------------------

class _Em:
    def __init__(self, phases, verifies):
        self._spec_vocabs = set(phases)
        self._spec_verify_vocabs = {"Insights": set(verifies)}


def test_a_complete_vocabulary_passes():
    rc._SUITE_VOCAB_EMITTED.clear()
    rc._SUITE_VOCAB_EMITTED["alpha"] = (frozenset({"enrollGuest", "verifyX"}),
                                        frozenset({"enrollGuest", "verifyX", "verifyY"}))
    rc._assert_suite_vocab_is_complete(_Em({"enrollGuest", "verifyX"},
                                           {"verifyX", "verifyY"}), "alpha")
    # a suite that emitted no steps base has nothing to check
    rc._assert_suite_vocab_is_complete(_Em({"anything"}, set()), "unknown")


def test_a_name_registered_late_is_reported_by_name():
    rc._SUITE_VOCAB_EMITTED.clear()
    rc._SUITE_VOCAB_EMITTED["alpha"] = (frozenset({"enrollGuest"}),
                                        frozenset({"enrollGuest"}))
    for em, late in ((_Em({"enrollGuest", "shopProperty"}, set()), "shopProperty"),
                     (_Em({"enrollGuest"}, {"verifyLate"}), "verifyLate")):
        try:
            rc._assert_suite_vocab_is_complete(em, "alpha")
        except SystemExit as exc:
            assert late in str(exc) and "alpha" in str(exc), str(exc)
        else:
            raise AssertionError("a late %s must stop the convert" % late)


def test_the_record_is_per_suite():
    """One emitter writes every suite's base in a multi-suite run. Kept on
    the emitter, the record held only the LAST suite's answer and the first
    suite's 45 names were all reported as late."""
    out = tempfile.mkdtemp(prefix="vocab")
    try:
        em = _fresh("alpha", out)
        em._spec_vocabs = {"enrollGuest"}
        em._emit_suite_steps_base("AlphaClient")
        em.suite_name = "beta"
        em._spec_vocabs = {"shopProperty"}
        em._emit_suite_steps_base("BetaClient")
        assert rc._SUITE_VOCAB_EMITTED["alpha"][0] == {"enrollGuest"}
        assert rc._SUITE_VOCAB_EMITTED["beta"][0] == {"shopProperty"}
        rc._assert_suite_vocab_is_complete(_Em({"enrollGuest"}, set()), "alpha")
    finally:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
