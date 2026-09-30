"""The near-miss detector has to find the three bugs that motivated it.

A detector nobody has seen fire is not evidence of health. Each test below
is a real bug this converter shipped, reduced to its names.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_near_miss_producers as NM


def _rules(raw, candidate):
    """The rule NM would cite for reaching `candidate` from `raw`, or None."""
    return NM.variations(raw).get(candidate)


def test_the_rest_parameter_bug_is_reachable():
    """`${STEP#param}` -> `#STEP_param#`, captured as `qry_STEP_param`.

    Cost several runs against a real environment; the value was in the
    same CSV row, three cells away.
    """
    rule = _rules("GET_Groups_SingleProp_arrivalDate",
                  "qry_GET_Groups_SingleProp_arrivalDate")
    assert rule and "qry_" in rule, rule


def test_the_path_parameter_variant_is_reachable():
    assert _rules("GET_Shop_propCode", "path_GET_Shop_propCode")


def test_the_dot_underscore_bug_is_reachable():
    """`DataSource.propCode` written, `DataSource_propCode` read."""
    assert _rules("DataSource_propCode", "DataSource.propCode")
    assert _rules("DataSource.propCode", "DataSource_propCode")


def test_a_properties_step_value_is_reachable():
    assert _rules("propCode", "Properties.propCode")


def test_stripping_a_prefix_is_reachable_too():
    """The bridge runs both ways: a reference may carry the prefix while
    the producer does not."""
    assert _rules("qry_S_arrivalDate", "S_arrivalDate")


def test_snake_case_is_reachable():
    assert _rules("accountID", "account_id")


def test_a_variation_never_returns_the_name_itself():
    """`raw` is not its own near miss -- that would report every
    unresolvable placeholder as a hit and make the report worthless."""
    for name in ("plain", "a_b", "qry_x", "Properties.y", "accountID"):
        assert name not in NM.variations(name), name


def test_rules_are_one_step_not_two():
    """Stacking rules would match almost anything. `qry_a.b` must not be
    reachable from `a_b` -- that is a prefix AND a spelling change."""
    assert "qry_a.b" not in NM.variations("a_b")


def test_an_unrelated_name_produces_no_hit():
    """The report is only worth reading if a hit means something."""
    v = NM.variations("startDate")
    assert "endDate" not in v
    assert "arrivalDate" not in v


def test_the_checker_mirrors_every_resolver_prefix():
    """The static checker and the runtime resolver must agree.

    `check_substitutions.satisfiable` reproduces, in Python, the spellings
    `ImportedScenario.putWithAliases` publishes in Java. Nothing enforces
    that by construction, and the two drifting is not a hypothetical: the
    qry_/path_ bridge was added to the resolver first, and until the
    checker learned it, two placeholders that now resolve perfectly were
    still being reported as broken -- on their way to being triaged into a
    baseline as "known failures".

    Drift is harmful in both directions. A checker missing a resolver
    spelling cries wolf until someone silences it; a checker inventing a
    spelling the resolver lacks waves real breakage through.

    Crude on purpose -- it greps for the prefix literals rather than
    parsing either language. A cleverer check would be one more thing that
    can rot silently.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    java = os.path.join(root, "tools", "ra_converter", "framework",
                        "ImportedScenario.java")
    py = os.path.join(root, "tools", "check_substitutions.py")
    with open(java, encoding="utf-8") as fh:
        jsrc = fh.read()
    with open(py, encoding="utf-8") as fh:
        psrc = fh.read()

    # The prefixes putWithAliases de-prefixes, as a Java array literal.
    import re
    m = re.search(r'for \(String prefix : new String\[\] \{([^}]*)\}',
                  jsrc)
    assert m, ("ImportedScenario.putWithAliases no longer has the "
               "prefix-alias loop -- if it moved, move this guard with it")
    prefixes = re.findall(r'"([^"]+)"', m.group(1))
    assert prefixes, "the alias loop lists no prefixes"

    for p in prefixes:
        assert '"%s"' % p in psrc, (
            "ImportedScenario.putWithAliases bridges the `%s` prefix but "
            "check_substitutions.py does not know it, so placeholders that "
            "DO resolve will be reported as broken" % p)


def test_near_misses_runs_against_the_real_tree():
    """Smoke: the real scan must not crash on this tree, whatever it finds.

    It walks generated output, so a Windows long path or a stray encoding
    is a live risk -- and a detector that throws is a detector that is off.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = NM.near_misses(root)
    assert isinstance(found, list)
    for raw, count, src, hits in found:
        assert isinstance(raw, str) and count >= 1
        assert hits, "a reported near miss must name its producer"
