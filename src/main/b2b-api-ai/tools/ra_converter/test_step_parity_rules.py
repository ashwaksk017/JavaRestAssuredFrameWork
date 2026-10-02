#!/usr/bin/env python3
"""The step-parity checker resolves spec ids inside their own suite.

Every suite numbers its specs from 1, so the ids collide as soon as more
than one suite is converted. The checker used to hold ONE flat
{specNN: step name} map for the whole tree, so with 15 suites `spec67`
was POST_Confirm_2 in group360createstage, GET_Groups_SingleProp in
partialgoalregression and sf_token_Request in programaccountregression --
and whichever file glob() returned last won.

The consequence was not a crash. It was seven cases reported as "losing"
a step they actually make, including `goal58_Post_confirm_duplicate_404`
whose entire point is the second confirm. A checker that says a real
call is missing costs more than one that says nothing, because the hunt
that follows is for a bug that is not there.

These tests pin per-suite resolution and the multi-spec phase form that
carries a repeated call.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import check_step_parity as sp     # noqa: E402

PASSED, FAILED = [], []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        FAILED.append((name, str(e)))
        print("FAIL %s: %s" % (name, e))
    else:
        PASSED.append(name)
        print("ok  %s" % name)


def _spec_file(entries):
    """entries: [(spec id, step name)] -> a Specs1.java body."""
    out = ["class Specs1 {"]
    for sid, step in entries:
        out.append("    static PhaseSpec %s() {" % sid)
        out.append('        return PhaseSpec.phase("%s")' % step)
        out.append("                    .expect(200)")
        out.append("                    .build();")
        out.append("    }")
    out.append("}")
    return "\n".join(out) + "\n"


def _phases_file(case_id, vocab, step, spec_ids):
    refs = ", ".join("Specs1::%s" % s for s in spec_ids)
    return ('class P {\n'
            '    static void register() {\n'
            '        CaseRegistry.register("%s")\n'
            '            .phase("%s", "%s", %s);\n'
            '    }\n}\n' % (case_id, vocab, step, refs))


def _tree(suites):
    """{suite: {'specs': [(id, step)], 'phases': (case, vocab, step, [ids])}}"""
    root = tempfile.mkdtemp(prefix="stepparity_")
    for suite, data in suites.items():
        d = os.path.join(root, "src", "main", "java", "com", "hi", "api",
                         "support", suite, "cases")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "Specs1.java"), "w", encoding="utf-8") as fh:
            fh.write(_spec_file(data["specs"]))
        case, vocab, step, ids = data["phases"]
        with open(os.path.join(d, "XPhases.java"), "w", encoding="utf-8") as fh:
            fh.write(_phases_file(case, vocab, step, ids))
    return root


def test_the_same_spec_id_in_two_suites_does_not_cross_over():
    """spec67 is a different call in every suite; each must resolve locally."""
    root = _tree({
        "suite_a": {"specs": [("spec67", "POST_Confirm_2")],
                    "phases": ("case_a", "confirm", "POST_Confirm", ["spec67"])},
        "suite_b": {"specs": [("spec67", "sf_token_Request")],
                    "phases": ("case_b", "token", "tokenRequest", ["spec67"])},
    })
    specs = sp.spec_step_names(root)
    assert set(specs) == {"suite_a", "suite_b"}, list(specs)
    assert specs["suite_a"]["spec67"] == "post_confirm_2", specs["suite_a"]
    assert specs["suite_b"]["spec67"] == "sf_token_request", specs["suite_b"]

    covered = sp.covered_by_case(root, specs, set())
    assert "post_confirm_2" in covered["case_a"], covered["case_a"]
    assert "sf_token_request" not in covered["case_a"], (
        "suite_b's spec67 leaked into suite_a")
    assert "sf_token_request" in covered["case_b"], covered["case_b"]


def test_a_repeated_call_rides_in_as_an_extra_spec():
    """`.phase(vocab, step, specA, specB)` is how a second same-endpoint call
    is emitted; both must count as covered."""
    root = _tree({
        "only": {"specs": [("spec66", "POST_Confirm"),
                           ("spec67", "POST_Confirm_2")],
                 "phases": ("dup_case", "confirm", "POST_Confirm",
                            ["spec66", "spec67"])},
    })
    covered = sp.covered_by_case(root, sp.spec_step_names(root), set())
    got = covered["dup_case"]
    assert "post_confirm" in got, got
    assert "post_confirm_2" in got, (
        "the second confirm is the point of a duplicate-confirm case")


def test_a_readyapi_space_and_an_emitted_underscore_are_the_same_step():
    """ReadyAPI calls it `POST_Confirm 2`; the emitter names it
    `POST_Confirm_2`. They must fold together or the match never happens."""
    assert sp.norm("POST_Confirm 2") == sp.norm("POST_Confirm_2")
    assert sp.norm("GET_Shop Same Prop") == sp.norm("GET_Shop_Same_Prop")


def test_suite_is_read_from_the_path():
    p = os.path.join("x", "src", "main", "java", "com", "hi", "api",
                     "support", "group360createstage", "cases", "Specs1.java")
    assert sp.suite_of(p) == "group360createstage", sp.suite_of(p)
    assert sp.suite_of("nothing/useful/here.java") == ""


for _name, _fn in sorted(
        (n, f) for n, f in list(globals().items())
        if n.startswith("test_") and callable(f)):
    check(_name, _fn)

print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
