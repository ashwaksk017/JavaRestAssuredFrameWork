#!/usr/bin/env python3
"""The duplication detector finds the collision that motivated it.

A checker that silently reads only SOME registration forms is worse than
no checker: it reports a clean tree while cases collide. That already
happened once -- the first version of check_case_duplication read only
`register("literal")` and missed the shared-spec array loop, so it found
5 collisions where there were 8, and looked authoritative doing it.

These tests pin both forms and the refusal to under-report.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import check_case_duplication as dup     # noqa: E402

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


def _tree(suites):
    """Build a throwaway support tree: {suite: {filename: java source}}."""
    root = tempfile.mkdtemp(prefix="dupcheck_")
    for suite, files in suites.items():
        d = os.path.join(root, suite, "cases")
        os.makedirs(d, exist_ok=True)
        for fname, src in files.items():
            with open(os.path.join(d, fname), "w", encoding="utf-8") as fh:
                fh.write(src)
    return root


def _collect_all(suites):
    root = _tree(suites)
    original = dup.SUPPORT
    dup.SUPPORT = root
    try:
        return dup.collect()
    finally:
        dup.SUPPORT = original


def _collect(suites):
    """The three values the duplication tests care about."""
    found, total, unparsed, _unqualified = _collect_all(suites)
    return found, total, unparsed


def _literal(case_id):
    return ('class P { static void register() {\n'
            '    CaseRegistry.register("%s")\n'
            '        .bootstrap(Specs1::spec1);\n} }\n' % case_id)


def _shared_spec(ids):
    arr = ", ".join('"%s"' % i for i in ids)
    return ('class P { static void register() {\n'
            '    for (String id : new String[] {%s}) {\n'
            '        CaseRegistry.register(id)\n'
            '            .bootstrap(Specs1::spec1);\n'
            '    }\n} }\n' % arr)


def test_a_literal_collision_across_suites_is_found():
    found, total, unparsed = _collect({
        "suite_a": {"APhases.java": _literal("goal3487_GET_groupEvents_200")},
        "suite_b": {"BPhases.java": _literal("goal3487_GET_groupEvents_200")},
    })
    assert not unparsed, unparsed
    assert total == 2, total
    owners = found["goal3487_GET_groupEvents_200"]
    assert sorted(owners) == ["suite_a", "suite_b"], sorted(owners)


def test_the_shared_spec_loop_form_is_read():
    """The form the first version missed, and why the count was wrong."""
    ids = ["Scenario 1 - peakRooms < 5", "Scenario 2 - x", "Scenario 4 - y"]
    found, total, unparsed = _collect({
        "suite_a": {"APhases.java": _shared_spec(ids)},
        "suite_b": {"BPhases.java": _shared_spec(ids)},
    })
    assert not unparsed, unparsed
    assert total == 6, "three ids per suite, both suites: %s" % total
    for i in ids:
        assert sorted(found[i]) == ["suite_a", "suite_b"], (i, sorted(found[i]))


def test_a_less_than_sign_in_a_case_name_survives():
    """`Scenario 1 - peakRooms < 5` is a real case name; `<` must not break parsing."""
    found, _total, unparsed = _collect({
        "suite_a": {"A.java": _literal("Scenario 1 - peakRooms < 5_SingleAndMulti")},
    })
    assert not unparsed, unparsed
    assert "Scenario 1 - peakRooms < 5_SingleAndMulti" in found, list(found)


def test_one_suite_alone_collides_with_nothing():
    found, total, unparsed = _collect({
        "only": {"A.java": _literal("goal1"), "B.java": _literal("goal2")},
    })
    assert not unparsed, unparsed
    assert total == 2, total
    assert not [c for c, per in found.items() if len(per) > 1]


def test_the_same_id_twice_in_ONE_suite_is_reported_separately():
    """A suite registering an id twice is its own bug, not a cross-suite clash."""
    found, _total, unparsed = _collect({
        "only": {"A.java": _literal("goal1"), "B.java": _literal("goal1")},
    })
    assert not unparsed, unparsed
    assert len(found["goal1"]["only"]) == 2, found["goal1"]
    assert len(found["goal1"]) == 1, "one suite, so not a cross-suite collision"


def test_an_unreadable_register_form_is_reported_not_ignored():
    """Under-reporting in silence is the failure this check replaces."""
    src = ('class P { static void register() {\n'
           '    String id = computeSomehow();\n'
           '    CaseRegistry.register(id).bootstrap(Specs1::spec1);\n} }\n')
    found, total, unparsed = _collect({"suite_a": {"A.java": src}})
    assert total == 0, total
    assert not found, dict(found)
    assert len(unparsed) == 1, unparsed
    assert "cannot read" in unparsed[0], unparsed[0]


def test_an_array_not_feeding_register_is_not_counted():
    """Only a loop whose variable actually reaches register() counts."""
    src = ('class P { static void register() {\n'
           '    for (String s : new String[] {"a", "b"}) { log(s); }\n'
           '    CaseRegistry.register("real_case")\n'
           '        .bootstrap(Specs1::spec1);\n} }\n')
    found, total, unparsed = _collect({"suite_a": {"A.java": src}})
    assert not unparsed, unparsed
    assert total == 1, "only the literal registration: %s" % total
    assert "real_case" in found and "a" not in found, list(found)


def test_a_suite_qualified_registration_is_read():
    """`register("<suite>", "<id>")` is the form the emitter writes now.

    A parser that only knew the one-argument form would read NO
    registrations at all and report a clean tree -- the worst possible
    answer from a check whose job is to find silent overwrites.
    """
    src = ('class P { static void register() {\n'
           '    CaseRegistry.register("suite_a", "B2B-317_attest")\n'
           '        .bootstrap(Specs1::spec1);\n} }\n')
    found, total, unparsed, unqualified = _collect_all(
        {"suite_a": {"A.java": src}})
    assert total == 1, total
    assert "B2B-317_attest" in found, dict(found)
    assert not unparsed, unparsed
    assert not unqualified, dict(unqualified)


def test_a_suite_qualified_shared_spec_loop_is_read():
    """The cluster form carries the suite too:
    `for (String id : ...) { CaseRegistry.register("<suite>", id)`."""
    src = ('class P { static void register() {\n'
           '    for (String id : new String[] {"c1", "c2"}) {\n'
           '        CaseRegistry.register("suite_a", id)\n'
           '            .bootstrap(Specs1::spec1);\n    }\n} }\n')
    found, total, unparsed, unqualified = _collect_all(
        {"suite_a": {"A.java": src}})
    assert total == 2, total
    assert set(found) == {"c1", "c2"}, dict(found)
    assert not unparsed, unparsed
    assert not unqualified, dict(unqualified)


def test_an_unqualified_registration_is_flagged():
    """Without a suite the key is the bare id again, and two projects
    holding it overwrite each other silently. That regression is the
    thing this check now fails on."""
    src = ('class P { static void register() {\n'
           '    CaseRegistry.register("B2B-317_attest")\n'
           '        .bootstrap(Specs1::spec1);\n} }\n')
    _found, _total, _unparsed, unqualified = _collect_all(
        {"suite_a": {"A.java": src}})
    assert unqualified, "a bare register() was not flagged"
    assert any("A.java" in k for k in unqualified), dict(unqualified)


def test_the_same_id_in_two_suites_stays_separate():
    """Allowed now -- each suite keeps its own chain -- but the checker
    must still show BOTH owners rather than collapsing them."""
    src_a = ('class P { static void register() {\n'
             '    CaseRegistry.register("suite_a", "shared_id")\n'
             '        .bootstrap(Specs1::spec1);\n} }\n')
    src_b = src_a.replace("suite_a", "suite_b")
    found, total, _unparsed, unqualified = _collect_all(
        {"suite_a": {"A.java": src_a}, "suite_b": {"B.java": src_b}})
    assert total == 2, total
    assert set(found["shared_id"]) == {"suite_a", "suite_b"}, dict(found)
    assert not unqualified, dict(unqualified)


for _name, _fn in sorted(
        (n, f) for n, f in list(globals().items())
        if n.startswith("test_") and callable(f)):
    check(_name, _fn)

print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
