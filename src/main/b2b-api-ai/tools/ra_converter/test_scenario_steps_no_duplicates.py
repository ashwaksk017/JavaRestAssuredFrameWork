"""ScenarioSteps must not declare the same method twice.

A vocabulary can be a PHASE in one case and a VERIFY in another --
`verifyProgramAccount` is both. ScenarioSteps handles that at RUNTIME:
`runPhase` asks the case which side it is

    boolean asVerify = !cs.has(vocab, false) && cs.has(vocab, true);

so ONE method covers both and the phase-emitted one is correct either
way. Emitting a second copy that calls `runVerify` is a straight
compile error.

The emitter deduped against the phase vocabularies of the suite being
converted, but the phase METHOD list is built from those plus the ones
a PRIOR suite left behind:

    vocab_methods_java(sorted(spec | RUN_VOCABS | set(prev_phase)), ...)
    _vv -= (spec | RUN_VOCABS)                 # prev_phase missing

So a name a previously-converted suite registered as a phase, used as a
verify here, landed in both lists. Converting a single XML into a tree
that already held other suites then failed with

    method verifyProgramAccount() is already defined in ScenarioSteps

A full --clean convert never showed it, because then there is no prior
suite -- which is why this went unnoticed until someone converted one
XML at a time.

    python tools/ra_converter/test_scenario_steps_no_duplicates.py
"""

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

sys.path.insert(0, HERE)

import phase_emit as pe  # noqa: E402

# `public S enrollGuest() throws Exception {`  /  `(String step)`
_SIG = re.compile(r'^\s*public\s+S\s+(\w+)\s*\(([^)]*)\)', re.M)


def _signatures(java: str):
    """(name, arity) for every declared method, in order."""
    out = []
    for m in _SIG.finditer(java):
        args = [a for a in m.group(2).split(",") if a.strip()]
        out.append((m.group(1), len(args)))
    return out


def _dupes(java: str):
    seen, dup = set(), []
    for sig in _signatures(java):
        if sig in seen:
            dup.append(sig)
        seen.add(sig)
    return dup


def test_a_name_emitted_as_both_phase_and_verify_is_declared_once():
    """The exact shape that broke the build."""
    both = "verifyProgramAccount"
    phase_side = pe.vocab_methods_java(sorted({both, "enrollGuest"}))
    # the emitter must NOT also put it on the verify side
    verify_side = pe.verify_vocab_methods_java(
        sorted({"verifyMemberInvite"}))          # `both` correctly excluded
    assert not _dupes(phase_side + verify_side), _dupes(phase_side + verify_side)

    # and if it IS left in, this test is what catches it
    leaky = pe.verify_vocab_methods_java(sorted({both, "verifyMemberInvite"}))
    assert _dupes(phase_side + leaky) == [(both, 0), (both, 1)], (
        "the guard below must notice a name on both sides")


def test_the_phase_side_wins_because_runPhase_resolves_at_runtime():
    """Not an arbitrary choice: runPhase asks the CASE which side it is,
    so the phase method is correct for a verify too. The reverse is not:
    runVerify always runs the verify side."""
    j = pe.vocab_methods_java(["verifyProgramAccount"])
    assert "runPhase(" in j and "runVerify(" not in j, j


def test_the_generated_file_has_no_duplicate_methods():
    """Against the real emitted ScenarioSteps, when one is present.

    Skipped on a checkout with no generated tree; the unit tests above
    still hold the rule.
    """
    hits = glob.glob(os.path.join(
        ROOT, "src", "main", "java", "com", "hi", "api", "support",
        "scenario", "ScenarioSteps.java"))
    if not hits:
        return
    java = open(hits[0], encoding="utf-8", errors="ignore").read()
    if not java.rstrip().endswith("}"):
        return                      # mid-write; a convert is running
    dup = _dupes(java)
    assert not dup, (
        "ScenarioSteps declares these twice, which will not compile: %s"
        % sorted({d[0] for d in dup}))


def test_no_suite_steps_base_has_duplicate_methods():
    """The vocabulary now lives on each suite's own steps base, beside that
    suite's text-path methods -- so that is where a name emitted twice
    would land. Same rule, every suite's file."""
    for path in glob.glob(os.path.join(
            ROOT, "src", "main", "java", "com", "hi", "api", "support",
            "*", "scenario", "*Steps.java")):
        java = open(path, encoding="utf-8", errors="ignore").read()
        if not java.rstrip().endswith("}"):
            continue                # mid-write; a convert is running
        dup = _dupes(java)
        assert not dup, (
            "%s declares these twice, which will not compile: %s"
            % (os.path.basename(path), sorted({d[0] for d in dup})))


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
