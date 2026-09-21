"""Guards for the project / identity / heuristics configuration.

The contract that keeps the default tree byte-identical: the committed
converter.config.json, converter_config.DEFAULTS, the emitter's built-in
tables and the Java IdentityVocabulary defaults all say the same thing.
"""
from __future__ import annotations

import json
import os
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
    for section in ("project", "identity", "heuristics"):
        assert committed[section] == cc.DEFAULTS[section], section
    assert cc.validate(cc.load_config(None)) == []


def test_emitter_tables_equal_defaults_before_apply():
    ident = cc.DEFAULTS["identity"]
    proj = cc.DEFAULTS["project"]
    assert rc._REGEN_TRIGGER_KEYS == frozenset(ident["regen_trigger_keys"])
    assert tuple(rc._ID_HINTS) == tuple(ident["id_hint_fields"])
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
