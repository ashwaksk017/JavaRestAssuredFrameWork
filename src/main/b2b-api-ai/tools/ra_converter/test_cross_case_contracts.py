"""Paired contracts so a converter fix for one ReadyAPI case cannot
silently invert another.

6801 (programaccountregression) member enroll already uses
``${Properties#generatedemailAddress1}``. Existence Match on the
post-update GET is ``content=false`` (path must be absent).

7816 (smbtohws) member enroll uses ``${Properties#Email}`` for the
*member* guest. Identity pack binds Email to the owner, so emit-time
remap + runtime overlay send ``generatedemailAddress1`` instead.

Both cases share: token skip-regen, first enroll owns the identity pack,
POST ``/remove``, owner vs member enroll bodies must not template-merge.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ra_converter  # noqa: E402


# ReadyAPI-shaped enroll bodies (stripped to identity leaves).
OWNER_ENROLL = {
    "username": "#Properties_username#",
    "email": {"emailAddress": "#Properties_generatedemailAddress#"},
}
MEMBER_ENROLL_6801 = {
    "username": "#Properties_usernamemember#",
    "email": {"emailAddress": "#Properties_generatedemailAddress1#"},
}
MEMBER_ENROLL_7816 = {
    "username": "#Properties_usernamemember#",
    "email": {"emailAddress": "#Properties_Email#"},
}

OWNER_SOAPUI = (
    '{"username":"${Properties#username}",'
    '"email":{"emailAddress":"${Properties#generatedemailAddress}"}}'
)
MEMBER_SOAPUI_6801 = (
    '{"username":"${Properties#usernamemember}",'
    '"email":{"emailAddress":"${Properties#generatedemailAddress1}"}}'
)
MEMBER_SOAPUI_7816 = (
    '{"username":"${Properties#usernamemember}",'
    '"email":{"emailAddress":"${Properties#Email}"}}'
)


def test_6801_member_enroll_generatedemailAddress1_is_not_rewritten():
    out = ra_converter._remap_member_enroll_email_placeholders(
        "MemberHHonorsEnroll", MEMBER_SOAPUI_6801)
    assert out == MEMBER_SOAPUI_6801, out
    assert "${Properties#generatedemailAddress1}" in out


def test_7816_member_enroll_email_rewritten_to_member_slot():
    out = ra_converter._remap_member_enroll_email_placeholders(
        "MemberHHonorsEnroll", MEMBER_SOAPUI_7816)
    assert "${Properties#generatedemailAddress1}" in out, out
    assert "${Properties#Email}" not in out, out


def test_owner_enroll_email_placeholders_untouched_by_member_remap():
    for name in ("HHonorsEnroll", "http_request_200_1", "tokenRequest"):
        out = ra_converter._remap_member_enroll_email_placeholders(
            name, OWNER_SOAPUI)
        assert out == OWNER_SOAPUI, (name, out)
        create = (
            '{"contactInfo":{"ownerEmailAddress":"${Properties#Email}"}}'
        )
        assert ra_converter._remap_member_enroll_email_placeholders(
            name, create) == create


def test_remap_does_not_eat_EmailMember_or_EmailAddress_suffixes():
    body = (
        '{"owner":"${Properties#EmailAddress}",'
        '"member":"${Properties#EmailMember}"}'
    )
    out = ra_converter._remap_member_enroll_email_placeholders(
        "MemberHHonorsEnroll", body)
    assert "${Properties#EmailMember}" in out, out
    assert "generatedemailAddress1Member" not in out, out


def test_owner_vs_member_enroll_do_not_share_a_tier2_template():
    """If these collapse, both guests get the same email/username → 409."""
    for member in (MEMBER_ENROLL_6801, MEMBER_ENROLL_7816):
        assert ra_converter._group_has_placeholder_divergence([
            {"tree": OWNER_ENROLL},
            {"tree": member},
        ]), member
    assert not ra_converter._group_has_placeholder_divergence([
        {"tree": MEMBER_ENROLL_6801},
        {"tree": MEMBER_ENROLL_6801},
    ])


def test_existence_match_polarity_is_per_assertion_not_global():
    """6801 GET-after-update is content=false; other cases stay exists."""
    emitter = ra_converter.Emitter(output_dir=".", package_root="com.hi.api")
    absent = ra_converter.Assertion(
        type="JsonPath Existence Match",
        name="Check for existence of [pendingEmailAddress]",
        config={"path": "$['pendingEmailAddress']", "content": "false"},
    )
    present = ra_converter.Assertion(
        type="JsonPath Existence Match",
        name="Check for existence of [pendingEmailAddress]",
        config={"path": "$['pendingEmailAddress']", "content": "true"},
    )
    absent_lines, _ = emitter._render_assertion(
        absent, "res", "http_request_200_getTravelAdvisorDetails_3")
    present_lines, _ = emitter._render_assertion(
        present, "res", "http_request_200_getTravelAdvisorDetails_3")
    absent_joined = "\n".join(absent_lines)
    present_joined = "\n".join(present_lines)
    assert "jsonAbsent" in absent_joined, absent_joined
    assert "jsonExists" not in absent_joined, absent_joined
    assert "jsonExists" in present_joined, present_joined
    assert "jsonAbsent" not in present_joined, present_joined
    assert ra_converter._assert_default_value(absent) == "false"
    assert ra_converter._assert_default_value(present) == "true"


def test_remove_post_does_not_flip_plain_member_delete():
    remove = ra_converter._infer_http_method(
        "RemoveProgramAccountMember",
        "/businesses/{accountId}/members/{memberId}/remove",
        '{"emailNotificationType":"accountClosed"}',
        "http_remove_member",
    )
    delete = ra_converter._infer_http_method(
        "DeleteProgramAccountMember",
        "/guests/{guestId}/businesses/{accountId}/members/{memberId}",
        "",
        "http_delete_account_member",
    )
    assert remove == "POST", remove
    assert delete == "DELETE", delete


def test_token_skip_regen_does_not_skip_enroll():
    token = type("S", (), {
        "step_name": "tokenRequest",
        "resource_path": "/realms/applications/token",
        "request_body":
            '{"username":"${#Project#Username}","password":"${#Project#Password}"}',
        "query_params": {},
        "headers": {},
    })()
    enroll = type("S", (), {
        "step_name": "HHonorsEnroll",
        "resource_path": "/realms/guests/enroll",
        "request_body":
            '{"username":"${Properties#Username}",'
            '"email":"${Properties#generatedemailAddress}"}',
        "query_params": {},
        "headers": {},
    })()
    member = type("S", (), {
        "step_name": "MemberHHonorsEnroll",
        "resource_path": "/realms/guests/enroll",
        "request_body": MEMBER_SOAPUI_7816,
        "query_params": {},
        "headers": {},
    })()
    assert ra_converter._step_needs_regen(token) is False
    assert ra_converter._step_needs_regen(enroll) is True
    assert ra_converter._step_needs_regen(member) is True


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as ex:
            failed += 1
            print(f"FAIL {fn.__name__}: {ex}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)} passed")
