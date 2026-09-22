"""Emitted @Test names stay short, camelCase, and unique.

The method name becomes the CSV file name. Before this cap, 63 of 604
emitted CSV paths exceeded the Windows 260-character limit, which fails
as a plain FileNotFound rather than as anything that names the cause.

The subtle property here is ORDER: truncate first, then number. Number
first and two names that differ only past the cut share a counter slot,
so two @Test methods resolve to one CSV file and one of them silently
reads the other's data.
"""
from __future__ import annotations

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ra_converter as rc  # noqa: E402

LONG = "createAndActivateInternationalPostalCodeTest"


def _case(name):
    """_cluster_method_name only reads `.name` and the cluster length."""
    return types.SimpleNamespace(name=name)


def test_a_name_within_budget_is_untouched():
    assert rc._shorten_method_name("shortTest", 40) == "shortTest"


def test_zero_disables_truncation():
    """Documented escape hatch for long-path-enabled or non-Windows runs."""
    assert rc._shorten_method_name(LONG, 0) == LONG
    assert rc._shorten_method_name(LONG, -1) == LONG


def test_truncation_fits_the_budget_and_keeps_the_suffix():
    out = rc._shorten_method_name(LONG, 40)
    assert len(out) <= 40, out
    assert out.endswith("Test"), out


def test_truncation_cuts_on_a_word_boundary():
    """A cut mid-word reads as a typo. `...International` beats `...Interna`."""
    assert rc._shorten_method_name(LONG, 40) == "createAndActivateInternationalTest"


def test_reserve_leaves_room_for_a_counter():
    out = rc._shorten_method_name(LONG, 40, reserve=3)
    assert len(out) <= 37, out


def test_two_long_names_stay_distinct_after_truncation():
    """THE correctness property. Both of these share their first 40 chars;
    if uniqueness were applied before the cut they would collapse onto one
    name, and therefore onto one CSV file."""
    seen: dict = {}
    a, _s, _v = rc._cluster_method_name(
        [_case("B2B-1_get_account_details_without_business_profile_model_alpha")], seen, 40)
    b, _s, _v = rc._cluster_method_name(
        [_case("B2B-1_get_account_details_without_business_profile_model_beta")], seen, 40)
    assert a != b, (a, b)
    assert len(a) <= 40 and len(b) <= 40, (a, b)


def test_disambiguators_are_camelcase_not_underscored():
    """The converter's only underscores came from these two suffixes."""
    seen: dict = {}
    first, _s, _v = rc._cluster_method_name([_case("B2B-2_post_create_thing")], seen, 0)
    second, _s, _v = rc._cluster_method_name([_case("B2B-2_post_create_thing")], seen, 0)
    assert first == "postCreateThingTest", first
    assert second == "postCreateThingC2Test", second
    assert "_" not in second

    variant, _s, _v = rc._cluster_method_name(
        [_case("B2B-9_post_activate_program_account_200_1")], {}, 0)
    assert variant == "postActivateProgramAccountV1Test", variant
    assert "_" not in variant


def test_the_business_name_builder_is_NOT_capped():
    """The cap belongs to the finalising step. _business_method_name is
    asserted verbatim by the contract suite, and shortening it there would
    break those assertions while changing nothing about path length."""
    m, _s, _v = rc._business_method_name(
        "B2B-1877_post_activate_account_lowConfidenceCompanyMatch_empty[Bug-B2B-2012]")
    assert m == "postActivateAccountLowConfidenceCompanyMatchEmptyTest", m
    assert len(m) == 53


def test_the_configured_cap_reaches_the_call_site():
    """A cap nothing passes in is a cap that never fires."""
    src = open(os.path.join(HERE, "ra_converter.py"), encoding="utf-8").read()
    assert "_cluster_method_name(\n                cluster, seen_bases, self.max_name_len)" in src


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
