"""Guards for the project / identity / heuristics configuration.

The contract that keeps the default tree byte-identical: the committed
converter.config.json, cc.DEFAULTS, the emitter's built-in
tables and the Java IdentityVocabulary defaults all say the same thing.
"""
from __future__ import annotations

import json
import os
import types
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import converter_config as cc  # noqa: E402
import groovy_translator as gt  # noqa: E402
import ra_converter as rc  # noqa: E402


def test_committed_file_equals_code_defaults():
    """Editing the JSON without DEFAULTS (or vice versa) is a drift."""
    committed = cc._read(cc.DEFAULT_PATH)
    for section in ("project", "identity", "heuristics", "placeholder_aliases"):
        assert committed[section] == cc.DEFAULTS[section], section
    assert cc.validate(cc.load_config(None)) == []


def test_every_rebound_name_is_checked_for_drift():
    """The drift test has to cover everything apply_to_modules rebinds.

    It did not. `apply_to_modules` rebinds ten names and the test below
    asserted nine: `_ENTRY_CLASS_NAME` was missing, so changing the
    emitter's built-in literal to something other than
    DEFAULTS["scenario"]["entry_class"] passed. That matters more than one
    value, because "a run with no config changes is byte-identical" rests
    entirely on defaults equalling literals -- and entry_class was the
    first override that ever DIFFERED from its default, which is how the
    __main__ double-import bug stayed hidden.

    Checking the list against the source rather than maintaining it by
    hand means the next rebind cannot be added without a drift assertion.
    """
    import ast
    import re as _re

    with open(os.path.join(HERE, "converter_config.py"),
              encoding="utf-8") as fh:
        cfg_src = fh.read()
    apply_src = cfg_src[cfg_src.index("def apply_to_modules"):]
    rebound = set(_re.findall(r"\b(?:rc|gt)\.(?:Emitter\.)?(_[A-Za-z0-9_]+)\s*=",
                              apply_src))

    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        this_src = fh.read()
    tree = ast.parse(this_src)
    drift = next(n for n in tree.body
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "test_emitter_tables_equal_defaults_before_apply")
    drift_src = ast.get_source_segment(this_src, drift) or ""

    missing = sorted(n for n in rebound if n not in drift_src)
    assert not missing, (
        "apply_to_modules rebinds these with no drift assertion: %s. A "
        "rebound value whose default differs from the built-in literal "
        "makes a no-config run change output." % ", ".join(missing))


def test_emitter_tables_equal_defaults_before_apply():
    ident = cc.DEFAULTS["identity"]
    proj = cc.DEFAULTS["project"]
    assert rc._ENTRY_CLASS_NAME == cc.DEFAULTS["scenario"]["entry_class"]
    # Per-suite overrides default to EMPTY, so a tree with no
    # config emits exactly the project-wide name -- the whole
    # "a no-config run is byte-identical" claim rests on this.
    assert rc._ENTRY_CLASS_BY_SUITE == cc.DEFAULTS["scenario"][
        "entry_class_by_suite"]
    assert rc._REGEN_TRIGGER_KEYS == frozenset(ident["regen_trigger_keys"])
    assert tuple(rc._ID_HINTS) == tuple(ident["id_hint_fields"])
    assert rc._PATH_ID_PARAM_NAMES == frozenset(ident["id_param_names"])
    assert tuple(rc.Emitter._HARDCODED_ID_FIELDS) == tuple(ident["id_param_names"])
    assert tuple(rc._MEMBER_ENROLL_PATTERNS) == tuple(ident["member_enroll_step_patterns"])
    assert rc._FIXTURE_LITERAL_FIELDS == {k.lower(): v for k, v in ident["fixture_literal_fields"].items()}
    assert rc._PRODUCT_LINE_FLOW_TOKENS == frozenset(proj["product_line_tokens"])
    assert tuple(rc._PARTNER_TOKENS) == tuple((p["token"], p["partner"]) for p in proj["partners"])
    assert rc._JIRA_TICKET_RX.pattern == proj["ticket_regex"]
    assert tuple(gt._IDENTITY_HINTS) == tuple(ident["identity_hints"])
    assert list(gt._STANDARD_FIELDS) == list(ident["standard_fields"])
    otp = cc.DEFAULTS["heuristics"]["otp"]
    assert gt._OTP_PAD_WIDTH == otp["pad_width"]
    assert tuple(gt._OTP_CODE_LIKE) == tuple(otp["code_like"])
    assert tuple(gt._OTP_CODE_EXCLUSIONS) == tuple(otp["code_exclusions"])


def test_java_defaults_equal_config_defaults():
    """IdentityVocabulary's D_* literals must match the JSON (a tree without
    the resource must behave as the configured one)."""
    src = open(os.path.join(HERE, "framework", "IdentityVocabulary.java"), encoding="utf-8").read()

    def java_list(name):
        m = re.search(r"List<String> " + name + r" = List\.of\((.*?)\);", src, re.S)
        assert m, name
        return re.findall(r'"([^"]+)"', m.group(1))

    ident = cc.DEFAULTS["identity"]
    assert java_list("D_STANDARD_FIELDS") == ident["standard_fields"]
    assert java_list("D_FREEMAIL_DOMAINS") == ident["freemail_domains"]
    assert java_list("D_BINDABLE_EMAILS") == ident["bindable_emails"]
    assert java_list("D_BINDABLE_DOMAINS") == ident["bindable_domains"]
    assert java_list("D_NAMED_IDENTITY_KEYS") == ident["named_identity_keys"]
    assert java_list("D_MEMBER_ENROLL_PATTERNS") == ident["member_enroll_step_patterns"]
    assert f'D_FROZEN_DOMAIN_KEY = "{ident["frozen_domain_key"]}"' in src
    assert f'D_ALLOWED_DOMAINS_KEY = "{ident["allowed_domains_config_key"]}"' in src
    sf = cc.DEFAULTS["heuristics"]["salesforce"]
    assert f'D_SALESFORCE_ID_SHAPE = "{sf["id_shape"]}"' in src
    assert f'D_SALESFORCE_SESSION_KEY = "{sf["session_key_fragment"]}"' in src


def test_placeholder_aliases_agree_with_java_config():
    """Each pair must resolve to ONE key in Config.LEGACY_ALIASES.

    The converter folds `#c_id#` to `#client_id#` before hashing a template
    body, which is sound only while Java maps both spellings to the same
    config key. If either is ever repointed, this table would start merging
    two DIFFERENT values into a single template -- silently, because the
    merged body still looks correct.
    """
    java_path = os.path.join(HERE, "..", "..", "src", "main", "java", "com",
                             "hi", "api", "config", "Config.java")
    src = open(java_path, encoding="utf-8").read()
    resolved = dict(re.findall(
        r'LEGACY_ALIASES\.put\(\s*"([^"]+)"\s*,\s*"([^"]+)"', src))
    aliases = cc.DEFAULTS["placeholder_aliases"]
    assert aliases, "no placeholder aliases configured"
    for alias, canonical in aliases.items():
        assert alias in resolved, "Config.java has no LEGACY_ALIAS %r" % alias
        assert canonical in resolved, "Config.java has no LEGACY_ALIAS %r" % canonical
        assert resolved[alias] == resolved[canonical], (
            "%s -> %s but %s -> %s; folding them would merge two different "
            "values into one template"
            % (alias, resolved[alias], canonical, resolved[canonical]))


def test_folding_makes_the_two_token_bodies_one():
    """The duplicate this table exists for.

    Same request, two spellings, so exact-body dedup kept two files and the
    ordinal made REALMS_TOKENREQUEST the 1-case outlier while _2 served 690.
    """
    a = '{"client_id": "#c_id#", "client_secret": "#c_sec#"}'
    b = '{"client_id": "#client_id#", "client_secret": "#client_secret#"}'
    assert a != b
    assert rc._fold_placeholder_aliases(a) == rc._fold_placeholder_aliases(b)
    # and a body with no alias in it is returned untouched
    plain = '{"guestId": "@Properties_memberGuestID@"}'
    assert rc._fold_placeholder_aliases(plain) == plain


def test_apply_rebinds_emitter_tables_and_restores():
    saved = (rc._REGEN_TRIGGER_KEYS, rc._MEMBER_ENROLL_PATTERNS, rc._PARTNER_TOKENS,
             rc._JIRA_TICKET_RX, rc._FIXTURE_LITERAL_FIELDS, gt._STANDARD_FIELDS, gt._OTP_PAD_WIDTH)
    try:
        cfg = cc.deep_merge(cc.DEFAULTS, {
            "project": {"ticket_regex": r"^(ABC-\d+)_(.+)$",
                        "partners": [{"token": "acme", "partner": "acme"}]},
            "identity": {"regen_trigger_keys": ["login", "mail"],
                         "member_enroll_step_patterns": ["addstaff"],
                         "fixture_literal_fields": {"staffNo": r"\d{4}"},
                         "standard_fields": ["Login", "Mail"]},
            "heuristics": {"otp": {"pad_width": 4}},
        })
        cc.apply_to_modules(cfg)
        assert rc._split_case_jira("ABC-12_create_thing") == ("ABC-12", "create_thing")
        assert rc._split_case_jira("B2B-12_create_thing") == ("", "B2B-12_create_thing")
        assert rc._infer_partner("acme_flow") == "acme" and rc._infer_partner("amex_flow") == ""
        assert rc._is_member_enroll_step("AddStaff_2") and not rc._is_member_enroll_step("MemberHHonorsEnroll")
        assert rc._REGEN_TRIGGER_KEYS == frozenset({"login", "mail"})
        assert rc._csv_cell("123456", "Properties.staffNo") == "123456", "fixture literal is not rewritten"
        assert gt._STANDARD_FIELDS == ["Login", "Mail"] and gt._OTP_PAD_WIDTH == 4
        out = "\n".join(gt.translate(
            'def sql = Sql.newInstance(dbUrl, dbUser, dbPassword, driver)\n'
            'sql.eachRow("SELECT email_otp, totp_code FROM account_member") { row -> emailOtp = row.email_otp; totpCode = row.totp_code }',
            {}, "jdbcStep")[0])
        assert "%04d" in out and "length() < 4" in out, out
    finally:
        cc.apply_to_modules(cc.load_config(None))
        assert (rc._REGEN_TRIGGER_KEYS, rc._MEMBER_ENROLL_PATTERNS, rc._PARTNER_TOKENS,
                rc._JIRA_TICKET_RX.pattern, rc._FIXTURE_LITERAL_FIELDS, gt._STANDARD_FIELDS, gt._OTP_PAD_WIDTH) == \
            (saved[0], saved[1], saved[2], saved[3].pattern, saved[4], saved[5], saved[6])


def test_identity_resource_carries_identity_and_heuristics():
    cfg = cc.load_config(None)
    res = cc.identity_resource(cfg)
    assert res["identity"] == cfg["identity"]
    assert res["heuristics"] == cfg["heuristics"]
    assert "diagrams" not in res
    json.dumps(res)  # serialisable



def test_entry_class_defaults_to_onboarding_and_is_overridable():
    """`Onboarding.start(row, "<case>")` is what a hand-written test types.

    Right word for the B2B projects it was named after, wrong one for
    GOAL. Default must stay Onboarding so every existing project
    converts unchanged.
    """
    before = rc._ENTRY_CLASS_NAME
    try:
        cfg = cc.load_config()
        assert (cfg.get("scenario") or {}).get("entry_class") == "Onboarding", (
            "the committed default must stay Onboarding")

        cc.apply_to_modules(cfg)
        assert rc._ENTRY_CLASS_NAME == "Onboarding", rc._ENTRY_CLASS_NAME

        goal = cc.deep_merge(
            cfg, {"scenario": {"entry_class": "GoalJourney"}})
        cc.apply_to_modules(goal)
        assert rc._ENTRY_CLASS_NAME == "GoalJourney", rc._ENTRY_CLASS_NAME

        # the name must reach the emitter, not just the module global
        stub = types.SimpleNamespace(phase_specs_enabled=True,
                                     _entry_class_names=[])
        got = rc.Emitter.shared_entry_class(stub, 0)
        assert got == "GoalJourney", got
    finally:
        rc._ENTRY_CLASS_NAME = before


def test_entry_class_must_be_a_java_identifier():
    """A bad value does not fail the convert -- it emits a class that
    will not compile, minutes later and far from the cause."""
    cfg = cc.load_config()
    for bad in ("Goal Journey", "9Goal", "", "class", "goal-journey"):
        merged = cc.deep_merge(
            cfg, {"scenario": {"entry_class": bad}})
        problems = [p for p in cc.validate(merged)
                    if "entry_class" in p]
        assert problems, "%r was accepted" % (bad,)
    ok = cc.deep_merge(
        cfg, {"scenario": {"entry_class": "GoalJourney"}})
    assert not [p for p in cc.validate(ok) if "entry_class" in p]



def test_config_reaches_the_module_even_when_it_is_main():
    """Run as a script, ra_converter IS __main__.

    apply_to_modules rebinds the tables through `import ra_converter as
    rc`. Without an alias that import loads a SECOND copy from disk,
    sets the tables on it and discards it, so the running module keeps
    its literals and every converter.config.json override silently does
    nothing. Invisible for as long as the committed defaults equalled
    the literals.

    This is a property of being __main__, so it is simulated here
    rather than caught by the ordinary-import tests above.
    """
    saved_main = sys.modules.get("__main__")
    saved_rc = sys.modules.get("ra_converter")
    try:
        # __main__ IS this file -> the running module must be aliased
        fake_main = types.ModuleType("__main__")
        fake_main.__file__ = rc.__file__
        sys.modules["__main__"] = fake_main
        sys.modules.pop("ra_converter", None)
        rc._alias_self_for_config()
        assert sys.modules.get("ra_converter") is fake_main, (
            "the running module was not aliased, so apply_to_modules would "
            "rebind a throwaway second copy")

        # __main__ is some OTHER script -> aliasing it would point the
        # config at the wrong module, so it must not happen
        other = types.ModuleType("__main__")
        other.__file__ = os.path.join(os.path.dirname(rc.__file__),
                                      "converter_config.py")
        sys.modules["__main__"] = other
        sys.modules.pop("ra_converter", None)
        rc._alias_self_for_config()
        assert sys.modules.get("ra_converter") is not other, (
            "aliased an unrelated __main__")
    finally:
        if saved_main is not None:
            sys.modules["__main__"] = saved_main
        if saved_rc is not None:
            sys.modules["ra_converter"] = saved_rc
        else:
            sys.modules.pop("ra_converter", None)


def test_validate_reports_bad_identity_values():
    bad = cc.deep_merge(cc.DEFAULTS, {"project": {"ticket_regex": "(oops"},
                                      "identity": {"standard_fields": [], "frozen_domain_key": ""},
                                      "heuristics": {"otp": {"pad_width": 0}}})
    problems = cc.validate(bad)
    assert any("ticket_regex" in p for p in problems)
    assert any("standard_fields" in p for p in problems)
    assert any("frozen_domain_key" in p for p in problems)
    assert any("pad_width" in p for p in problems)


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok ", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, "->", e)
    total = len([n for n in globals() if n.startswith("test_")])
    print(f"{total - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
