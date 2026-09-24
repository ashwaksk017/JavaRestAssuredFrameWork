"""Unit checks for 6801 post-fix gaps: /remove POST, token regen skip,
groovy hiltonmemberid putExtracted, and Existence Match content=false."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import groovy_translator  # noqa: E402
import ra_converter  # noqa: E402


def test_infer_remove_is_post():
    verb = ra_converter._infer_http_method(
        "RemoveProgramAccountMember",
        "/businesses/{accountId}/members/{memberId}/remove",
        '{"emailNotificationType":"accountClosed"}',
        "http_remove_member",
    )
    assert verb == "POST", verb


def test_infer_delete_member_stays_delete():
    verb = ra_converter._infer_http_method(
        "DeleteProgramAccountMember",
        "/guests/{guestId}/businesses/{accountId}/members/{memberId}",
        "",
        "http_delete_account_member",
    )
    assert verb == "DELETE", verb


def test_confirm_validation_is_post():
    verb = ra_converter._infer_http_method(
        "ValidateMemberTOTP",
        "/guests/{guestId}/businesses/{accountId}/members/{memberId}/confirmValidation",
        '{"totpCode":"${Properties#totpCodeDB}"}',
        "http_confirmValidation_200_2",
    )
    assert verb == "POST", verb


def test_infer_invitation_link_is_post():
    """OpenAPI SendInvitationLink is POST /guests/{guestId}/businesses/{accountId}/invitationLink.

    Suite-only exports (or a WADL GET) would emit GET; stg returns 405.
    """
    verb = ra_converter._infer_http_method(
        "SendInvitationLink",
        "/guests/{guestId}/businesses/{accountId}/invitationLink",
        "",
        "SendInvitationLink",
    )
    assert verb == "POST", verb
    assert "invitationlink" in ra_converter._POST_ACTION_SEGMENTS


def test_auto_extract_scans_jsonpath_assertion_content():
    """JsonPath Match content `${REST Request#Response#$['sourceId']}` is a
    cross-step response ref. Scanning only later request bodies missed it,
    so CSV `#REST_Request_Response_sourceId#` stayed literal (B2B-2147).
    """
    src = ra_converter.RestStep(
        step_name="REST Request",
        service="",
        method_name="AttestProgramAccount",
        resource_path="/businesses/smb/attest",
        http_method="GET",
        endpoint="",
        original_uri="",
        media_type="application/json",
        request_body="",
        headers={},
        path_params={},
        query_params={},
    )
    later = ra_converter.RestStep(
        step_name="http_request_200_2",
        service="",
        method_name="ReadProgramAccount",
        resource_path="/businesses/{accountId}",
        http_method="GET",
        endpoint="",
        original_uri="",
        media_type="application/json",
        request_body="",
        headers={},
        path_params={},
        query_params={},
        assertions=[ra_converter.Assertion(
            type="JsonPath Match",
            name="leadspaceId",
            config={
                "path": "$.alternateAccounts.leadspaceId",
                "content": "${REST Request#Response#$['sourceId']}",
            },
        )],
    )
    case = ra_converter.TestCase(
        id="1", name="B2B-2147_HighConfidence_Account_200",
        description="", steps=[src, later],
    )
    fields = ra_converter._needed_response_extracts("REST Request", case)
    assert fields.get("REST_Request_Response_sourceId") == "sourceId", fields


def test_auto_extract_scans_rawrequest_assertion_content():
    """DataAndMetadata expected ${create#RawRequest#$['contactInfo']['name']}
    is a cross-step request-body ref. Scanning only later request bodies
    missed it, so CSV #..._RawRequest_contactInfo_name# stayed literal
    (B2B-450 reject).
    """
    src = ra_converter.RestStep(
        step_name="http_request_200_createAccount",
        service="",
        method_name="CreateProgramAccount",
        resource_path="/guests/{guestId}/businesses",
        http_method="POST",
        endpoint="",
        original_uri="",
        media_type="application/json",
        request_body='{"contactInfo":{"name":"${Properties#name}"}}',
        headers={},
        path_params={},
        query_params={},
    )
    later = ra_converter.RestStep(
        step_name="http_request_200_get_account",
        service="",
        method_name="ReadProgramAccount",
        resource_path="/businesses/{accountId}",
        http_method="GET",
        endpoint="",
        original_uri="",
        media_type="application/json",
        request_body="",
        headers={},
        path_params={},
        query_params={},
        assertions=[ra_converter.Assertion(
            type="DataAndMetadataAssertion",
            name="verify_create_echo",
            config={},
            elements=[{
                "element": "name",
                "expectedValue":
                    "${http_request_200_createAccount#RawRequest#$['contactInfo']['name']}",
            }],
        )],
    )
    case = ra_converter.TestCase(
        id="1", name="B2B450_Reject_Limited_account_200",
        description="", steps=[src, later],
    )
    fields = ra_converter._needed_rawrequest_extracts(
        "http_request_200_createAccount", case)
    assert fields.get(
        "http_request_200_createAccount_RawRequest_contactInfo_name"
    ) == "contactInfo.name", fields


def test_4xx_hardcoded_path_id_is_not_rewritten_to_live_guest():
    """B2B-2264 GuestRead with guestId=123456789 expects 403.

    Rewriting that literal to Properties.guestId made stg return 200.
    """
    def _step(codes: str, guest: str) -> ra_converter.RestStep:
        return ra_converter.RestStep(
            step_name="REST Request 2",
            service="",
            method_name="GuestReadProgramAccount",
            resource_path="/guests/{guestId}/businesses/{accountId}",
            http_method="GET",
            endpoint="",
            original_uri="",
            media_type="application/json",
            request_body="",
            headers={},
            path_params={"guestId": guest, "accountId": "${PropertiesaccountID#accountID}"},
            query_params={},
            assertions=[ra_converter.Assertion(
                type="Valid HTTP Status Codes",
                name="Valid HTTP Status Codes",
                config={"codes": codes},
            )],
        )

    negative = _step("403", "123456789")
    assert not ra_converter._should_rewrite_hardcoded_path_id(
        "guestId", "123456789", negative)
    live = _step("200", "246898901")
    assert ra_converter._should_rewrite_hardcoded_path_id(
        "guestId", "246898901", live)
    repeating = _step("200", "888888888")
    assert not ra_converter._should_rewrite_hardcoded_path_id(
        "guestId", "888888888", repeating)


def test_csv_cell_keeps_path_param_fake_ids():
    assert ra_converter._csv_cell(
        "123456789", "path_REST_Request_2_guestId") == "123456789"
    assert ra_converter._csv_cell(
        "123456789", "path_REST_Request_accountId") == "123456789"
    rewritten = ra_converter._csv_cell("2000016128", "PropertiesDetails.accountID")
    assert rewritten.startswith("@Properties_"), rewritten


def test_csv_cell_keeps_expected_assertion_ids():
    assert ra_converter._csv_cell(
        "1234567891",
        "expected_http_request_200_read_program_account_add_jsonpath_honorsMembership_hhonorsNumber",
    ) == "1234567891"
    rewritten = ra_converter._csv_cell("2000016128", "PropertiesDetails.accountID")
    assert rewritten.startswith("@Properties_"), rewritten


def test_infer_compare_is_get_despite_leftover_body():
    """OpenAPI CompareProgramAccounts is GET /businesses/compare.

    Suite-only ReadyAPI exports have no interface verb; a leftover JSON
    body (or Compare* op name) must not emit POST (stg returns 405).
    """
    verb = ra_converter._infer_http_method(
        "CompareProgramAccounts",
        "/businesses/compare",
        '{"name":"${Properties#name}"}',
        "http_request_200_business_compare_name_only",
    )
    assert verb == "GET", verb


def test_groovy_expand_uses_put_extracted():
    script = (
        "def hiltonmemberid = context.expand("
        "'${http_request_200-getTravelAdvisorDetails#Response#$[\\'memberId\\']}')\n"
    )
    lines, _meta = groovy_translator.translate(
        script,
        {"http_request_200-getTravelAdvisorDetails":
         "http_request_200_getTravelAdvisorDetailsRes"},
        step_name_hint="GroovyScript_TOTPcode_member",
    )
    joined = "\n".join(lines)
    assert 'putExtracted(ctx, "hiltonmemberid"' in joined, joined
    assert 'putIfNonEmpty(ctx, "hiltonmemberid"' not in joined, joined


def test_last_nonempty_setproperty_readyapi_last_write():
    script = (
        'def P = testRunner.testCase.getTestStepByName("PropertiesaccountID")\n'
        'P.setPropertyValue("hilton-member-id", "")\n'
        'P.setPropertyValue("hilton-member-id", hilton_member_id.toString().trim())\n'
        'P.setPropertyValue("hilton-member-id", resp_obj.memberId.toString().trim())\n'
    )
    last = groovy_translator._last_nonempty_setproperty(script)
    expr = last[("PropertiesaccountID", "hilton-member-id")]
    assert expr.startswith("resp_obj.memberId"), expr
    assert groovy_translator._should_emit_setproperty(
        last, "PropertiesaccountID", "hilton-member-id", expr)
    assert not groovy_translator._should_emit_setproperty(
        last, "PropertiesaccountID", "hilton-member-id",
        "hilton_member_id.toString().trim()")


def _translate_accountid_groovy(script: str) -> str:
    lines, _meta = groovy_translator.translate(
        script,
        {"http_request_200_createAccount": "http_request_200_createAccountRes",
         "http_request_200_1": "http_request_200_1Res"},
        step_name_hint="Groovy Script for accountID",
    )
    return "\n".join(lines)


def test_groovy_last_write_json_member_id_wins_over_location_guest_slice():
    """B2B-6604: split('/')[0] then resp_obj.memberId -- JSON last-write."""
    script = r'''
def hilton_member_location_header = testRunner.testCase.testSteps["http_request_200_createAccount"].testRequest.response.responseHeaders["hilton-member-location"]
def hilton_member_id = hilton_member_location_header[0].replace("/guests/","").split('/')[0];
def jsonSlurper = new groovy.json.JsonSlurper();
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesaccountID")
PropertiesPropertyVal.setPropertyValue("accountID", "")
PropertiesPropertyVal.setPropertyValue("hilton-member-id", "")
def resp = testRunner.testCase.getTestStepByName("http_request_200_createAccount").getPropertyValue('response');
def resp_obj = jsonSlurper.parseText(resp)
PropertiesPropertyVal.setPropertyValue("accountID", resp_obj.accountId.toString().trim())
PropertiesPropertyVal.setPropertyValue("hilton-member-id", hilton_member_id.toString().trim())
PropertiesPropertyVal.setPropertyValue("hilton-member-id", resp_obj.memberId.toString().trim())
'''
    joined = _translate_accountid_groovy(script)
    assert 'safeJsonExtract(http_request_200_createAccountRes, "memberId")' in joined, joined
    assert 'parts_hilton_member_location[0]' not in joined, joined
    assert "later setPropertyValue wins" in joined, joined


def test_groovy_location_split4_still_published_when_last_write():
    """PropertiesDetails template: last write is location split('/')[4]."""
    script = r'''
def hilton_member_location_header = testRunner.testCase.testSteps["http_request_200_1"].testRequest.response.responseHeaders["hilton-member-location"]
def hilton_member_id = hilton_member_location_header[0].replace("/guests/","").split('/') [4];
def jsonSlurper = new groovy.json.JsonSlurper();
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesDetails")
PropertiesPropertyVal.setPropertyValue("accountID", "")
PropertiesPropertyVal.setPropertyValue("hilton-member-id", "")
def resp = testRunner.testCase.getTestStepByName("http_request_200_1").getPropertyValue('response');
def resp_obj = jsonSlurper.parseText(resp)
PropertiesPropertyVal.setPropertyValue("accountID", resp_obj.accountId.toString().trim())
PropertiesPropertyVal.setPropertyValue("hilton-member-id", hilton_member_id.toString().trim())
'''
    joined = _translate_accountid_groovy(script)
    assert 'parts_hilton_member_location[4]' in joined, joined
    assert 'safeJsonExtract(http_request_200_1Res, "memberId")' not in joined, joined
    assert 'safeJsonExtract(http_request_200_1Res, "accountId")' in joined, joined


def test_groovy_location_split0_only_still_published_when_last_write():
    """Guest-id slice is last write -- keep parts[0], no JSON memberId."""
    script = r'''
def hilton_member_location_header = testRunner.testCase.testSteps["http_request_200_createAccount"].testRequest.response.responseHeaders["hilton-member-location"]
def hilton_member_id = hilton_member_location_header[0].replace("/guests/","").split('/')[0];
def jsonSlurper = new groovy.json.JsonSlurper();
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesaccountID")
PropertiesPropertyVal.setPropertyValue("hilton-member-id", "")
def resp = testRunner.testCase.getTestStepByName("http_request_200_createAccount").getPropertyValue('response');
def resp_obj = jsonSlurper.parseText(resp)
PropertiesPropertyVal.setPropertyValue("accountID", resp_obj.accountId.toString().trim())
PropertiesPropertyVal.setPropertyValue("hilton-member-id", hilton_member_id.toString().trim())
'''
    joined = _translate_accountid_groovy(script)
    assert 'parts_hilton_member_location[0]' in joined, joined
    assert 'safeJsonExtract(http_request_200_createAccountRes, "memberId")' not in joined, joined


def test_token_request_does_not_need_regen():
    step = SimpleNamespace(
        step_name="tokenRequest",
        resource_path="/realms/applications/token",
        request_body='{"username":"${#Project#Username}","password":"${#Project#Password}"}',
        query_params={},
        headers={},
    )
    assert ra_converter._step_needs_regen(step) is False


def test_enroll_still_needs_regen():
    step = SimpleNamespace(
        step_name="HHonorsEnroll",
        resource_path="/realms/guests/enroll",
        request_body='{"username":"${Properties#Username}","email":"${Properties#generatedemailAddress}"}',
        query_params={},
        headers={},
    )
    assert ra_converter._step_needs_regen(step) is True


def test_project_username_alone_does_not_need_regen():
    step = SimpleNamespace(
        step_name="http_request_200_1",
        resource_path="/guests/{guestId}/businesses",
        request_body='{"operator":"${#Project#Username}"}',
        query_params={},
        headers={},
    )
    assert ra_converter._step_needs_regen(step) is False


def test_member_enroll_email_remapped_to_generatedemailAddress1():
    body = (
        '{"username":"${Properties#usernamemember}",'
        '"email":{"emailAddress":"${Properties#Email}"}}'
    )
    out = ra_converter._remap_member_enroll_email_placeholders(
        "MemberHHonorsEnroll", body)
    assert "${Properties#generatedemailAddress1}" in out, out
    assert "${Properties#Email}" not in out, out
    owner = ra_converter._remap_member_enroll_email_placeholders(
        "HHonorsEnroll",
        '{"email":{"emailAddress":"${Properties#Email}"}}')
    assert "${Properties#Email}" in owner


def test_existence_match_content_false():
    assert ra_converter._existence_match_content({"content": "false"}) == "false"
    assert ra_converter._existence_match_content({"content": '"false"'}) == "false"
    assert ra_converter._existence_match_content({"content": "true"}) == "true"
    assert ra_converter._existence_match_content({}) == "true"


def test_assert_default_value_existence_false():
    a = ra_converter.Assertion(
        type="JsonPath Existence Match",
        name="Check for existence of [pendingEmailAddress]",
        config={"path": "$['pendingEmailAddress']", "content": "false"},
    )
    assert ra_converter._assert_default_value(a) == "false"


def test_render_existence_false_emits_json_absent():
    emitter = ra_converter.Emitter(output_dir=".", package_root="com.ak.api")
    a = ra_converter.Assertion(
        type="JsonPath Existence Match",
        name="Check for existence of [pendingEmailAddress]",
        config={"path": "$['pendingEmailAddress']", "content": "false"},
    )
    lines, cov = emitter._render_assertion(
        a, "http_request_200_getTravelAdvisorDetails_3Res",
        "http_request_200_getTravelAdvisorDetails_3")
    joined = "\n".join(lines)
    assert "jsonAbsent" in joined, joined
    assert "pendingEmailAddress" in joined, joined
    assert "jsonExists" not in joined, joined
    assert cov == "FULL"


def test_render_existence_true_emits_json_exists():
    emitter = ra_converter.Emitter(output_dir=".", package_root="com.ak.api")
    a = ra_converter.Assertion(
        type="JsonPath Existence Match",
        name="Check for existence of [pendingEmailAddress]",
        config={"path": "$['pendingEmailAddress']", "content": "true"},
    )
    lines, cov = emitter._render_assertion(
        a, "res", "getTravelAdvisorDetails")
    joined = "\n".join(lines)
    assert "jsonExists" in joined, joined
    assert "jsonAbsent" not in joined, joined
    assert cov == "FULL"


def test_render_datameta_equals_uses_value_in_response():
    emitter = ra_converter.Emitter(output_dir=".", package_root="com.ak.api")
    a = ra_converter.Assertion(
        type="DataAndMetadataAssertion",
        name="dm",
        elements=[{"path": "$.accountStatus", "expectedValue": "active",
                   "operatorId": "1", "element": "accountStatus", "enabled": "true"}],
    )
    lines, cov = emitter._render_assertion(a, "res", "confirm")
    joined = "\n".join(lines)
    assert "valueInResponse" in joined, joined
    assert "assertEquals(actual_" not in joined, joined
    assert cov == "FULL"


def test_datameta_duplicate_element_names_get_unique_csv_cols():
    """Root code=999 and notifications[0].code=16 must not share a CSV cell."""
    a = ra_converter.Assertion(
        type="DataAndMetadataAssertion",
        name="verifyErrorMessage",
        elements=[
            {"path": "code", "expectedValue": "999", "operatorId": "1",
             "element": "code", "enabled": "true"},
            {"path": "notifications[0].code", "expectedValue": "16",
             "operatorId": "1", "element": "code", "enabled": "true"},
            {"path": "message", "expectedValue": "Constraint Violation",
             "operatorId": "1", "element": "message", "enabled": "true"},
            {"path": "notifications[0].message",
             "expectedValue": "Value is required.", "operatorId": "1",
             "element": "message", "enabled": "true"},
        ],
    )
    cols = ra_converter._assert_element_cols(a, "get_attest_readProgamAccount")
    names = [c[0] for c in cols]
    assert len(names) == len(set(names)), names
    assert names[0].endswith("_datameta_0_code"), names[0]
    assert names[1].endswith("_datameta_1_code"), names[1]
    by_col = dict(cols)
    assert by_col[names[0]] == "999"
    assert by_col[names[1]] == "16"
    emitter = ra_converter.Emitter(output_dir=".", package_root="com.ak.api")
    lines, cov = emitter._render_assertion(
        a, "res", "get_attest_readProgamAccount")
    joined = "\n".join(lines)
    assert names[0] in joined, joined
    assert names[1] in joined, joined
    assert '"999"' in joined and '"16"' in joined
    assert cov == "FULL"


def test_empty_query_params_are_not_emitted():
    """ReadyAPI does not send unused empty resource query params."""
    assert ra_converter._soapui_query_value_on_wire("ride.com")
    assert ra_converter._soapui_query_value_on_wire("microsoft")
    assert not ra_converter._soapui_query_value_on_wire("")
    assert not ra_converter._soapui_query_value_on_wire("   ")
    assert not ra_converter._soapui_query_value_on_wire(None)
    src = open(os.path.join(HERE, "ra_converter.py"), encoding="utf-8").read()
    assert "_soapui_query_value_on_wire(v)" in src
    assert "_soapui_query_value_on_wire(raw_q)" in src


def test_render_datameta_contains_uses_substring_in_response():
    emitter = ra_converter.Emitter(output_dir=".", package_root="com.ak.api")
    a = ra_converter.Assertion(
        type="DataAndMetadataAssertion",
        name="dm",
        elements=[{"path": "$.message", "expectedValue": "H4LE",
                   "operatorId": "3", "element": "message", "enabled": "true"}],
    )
    lines, cov = emitter._render_assertion(a, "res", "step")
    joined = "\n".join(lines)
    assert "substringInResponse" in joined, joined
    assert cov == "FULL"


def _datagen_prefix() -> str:
    return r'''
def generatedEmail = "x@y.com"
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("Properties")
def generatedUser2 = generator( (('a'..'z')+('a'..'z')).join(), 8)
def generatedUsername2 = generatedUser2
def generatedUser3 = generator( (('a'..'z')+('a'..'z')).join(), 8)
def generatedUser4 = generator( (('a'..'z')+('a'..'z')).join(), 8)
def generatedUser5 = generator( (('A'..'Z')).join(), 8)
'''


def test_space_concat_idents_two_words():
    expr = 'generatedUsername2 + " "+generatedUser3'
    assert groovy_translator._space_concat_idents(expr) == [
        "generatedUsername2", "generatedUser3"]
    assert groovy_translator._space_concat_idents("generatedUsername2") is None


def test_groovy_datagen_two_words_name_equals_name2():
    """B2B-6604 two_words: name and name2 share the same two words."""
    script = _datagen_prefix() + (
        'PropertiesPropertyVal.setPropertyValue("name", '
        'generatedUsername2 + " "+generatedUser3)\n'
        'PropertiesPropertyVal.setPropertyValue("name2", '
        'generatedUsername2 + " "+generatedUser3)\n'
    )
    joined = "\n".join(groovy_translator.translate(script, {}, "DataGenInput")[0])
    assert '__gc_generatedUser2 + " " + __gc_generatedUser3' in joined, joined
    assert joined.count(
        'putExtracted(ctx, "Properties.name", __gc_generatedUser2 + " " + __gc_generatedUser3)'
    ) == 1
    assert joined.count(
        'putExtracted(ctx, "Properties.name2", __gc_generatedUser2 + " " + __gc_generatedUser3)'
    ) == 1
    assert "String __gc_generatedUsername2" not in joined, joined


def test_groovy_datagen_other_word_name2_differs():
    """B2B-6604 other_word: name2 uses a different second word."""
    script = _datagen_prefix() + (
        'PropertiesPropertyVal.setPropertyValue("name", '
        'generatedUsername2 + " "+generatedUser3)\n'
        'PropertiesPropertyVal.setPropertyValue("name2", '
        'generatedUsername2 + " "+generatedUser4)\n'
    )
    joined = "\n".join(groovy_translator.translate(script, {}, "DataGenInput")[0])
    assert (
        'putExtracted(ctx, "Properties.name", '
        '__gc_generatedUser2 + " " + __gc_generatedUser3)'
    ) in joined, joined
    assert (
        'putExtracted(ctx, "Properties.name2", '
        '__gc_generatedUser2 + " " + __gc_generatedUser4)'
    ) in joined, joined


def test_groovy_datagen_systemname_three_words_shares_user2():
    """B2B-6604 systemname: name2 is three words ending with generatedUser2."""
    script = _datagen_prefix() + (
        'PropertiesPropertyVal.setPropertyValue("name", '
        'generatedUsername2 + " "+generatedUser3)\n'
        'PropertiesPropertyVal.setPropertyValue("name2", '
        'generatedUser5 + " "+generatedUser4 + " "+generatedUser2)\n'
    )
    joined = "\n".join(groovy_translator.translate(script, {}, "DataGenInput")[0])
    assert 'regexify("[A-Z]{8}")' in joined, joined
    assert (
        'putExtracted(ctx, "Properties.name", '
        '__gc_generatedUser2 + " " + __gc_generatedUser3)'
    ) in joined, joined
    assert (
        'putExtracted(ctx, "Properties.name2", '
        '__gc_generatedUser5 + " " + __gc_generatedUser4 + " " + __gc_generatedUser2)'
    ) in joined, joined


def test_cross_tc_token_header_translates():
    ref = "${#[SmokeTest_Test_Stg#token_generation#tokenId]#GeneratedTokenID}"
    java = ra_converter.soapui_expr_to_java(ref)
    assert "tokenId.GeneratedTokenID" in java, java
    assert "${" not in java, java


def test_groovy_cleanupdb_hardcoded_domain_delete():
    script = (
        'def websiteDomains =  "\'www.motorola.com\'";\n'
        'def result1 = sql.execute("DELETE FROM account_member WHERE account_id IN '
        '(SELECT a.account_id FROM account a WHERE a.web_site IN (" + websiteDomains +"))")\n'
    )
    joined = "\n".join(
        groovy_translator.translate(script, {}, "CleanupDB")[0])
    assert "SkipException" not in joined, joined
    assert "Db.execute" in joined, joined


def test_groovy_cleanupdb_properties_quoted_sql_literal():
    script = (
        'def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("Properties")\n'
        'def EmailDomain =  "\'"+ PropertiesPropertyVal.getPropertyValue(\'EmailDomain\') +"\'";\n'
        'def result2 = sql.execute("DELETE FROM ACCOUNT_EMAIL_DOMAIN WHERE EMAIL_DOMAIN = " '
        '+ EmailDomain )\n'
    )
    joined = "\n".join(
        groovy_translator.translate(script, {}, "CleanupDB")[0])
    assert "SkipException" not in joined, joined
    assert "Properties.EmailDomain" in joined, joined


def test_groovy_update_second_account_info_sql_var():
    script = (
        'def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesaccountID_2")\n'
        'def accountID = PropertiesPropertyVal.getPropertyValue("accountID2") as Integer\n'
        "def newAddressLine1 = context.expand("
        "'${http_get_account_details_200#Response#$['contactInfo']['address']['addressLine1']"
        "]}').replace(\"'\", \"''\")\n"
        'def sql_addressline1 = "update account set address1 = \'${newAddressLine1}\' '
        'where account_id = ${accountID}"\n'
        'sql.execute(sql_addressline1)\n'
    )
    rv = {"http_get_account_details_200": "http_get_account_details_200Res"}
    joined = "\n".join(
        groovy_translator.translate(
            script, rv, "Update_second_Account_info")[0])
    assert "SkipException" not in joined, joined
    assert "Db.execute" in joined, joined
    assert "safeJsonExtract(http_get_account_details_200Res" in joined, joined


def test_groovy_commented_sql_execute_not_mutation_skip():
    """ReadyAPI keeps dead //sql.execute(sql_query) beside live GString updates."""
    script = (
        'def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesaccountID_2")\n'
        'def accountID = PropertiesPropertyVal.getPropertyValue("accountID2") as Integer\n'
        "def newAddressLine1 = context.expand("
        "'${http_get_account_details_200#Response#$['contactInfo']['address']['addressLine1']"
        "]}').replace(\"'\", \"''\")\n"
        '//def sql_query = "update account_member set status = \'A\' where account_id = ${accountID}"\n'
        'def sql_addressline1 = "update account set address1 = \'${newAddressLine1}\' '
        'where account_id = ${accountID}"\n'
        '//sql.execute(sql_query)\n'
        'sql.execute(sql_addressline1)\n'
    )
    rv = {"http_get_account_details_200": "http_get_account_details_200Res"}
    joined = "\n".join(
        groovy_translator.translate(
            script, rv, "Update_second_Account_info")[0])
    assert "SkipException" not in joined, joined
    assert "Db.execute" in joined, joined
    assert "sql_query" not in joined or "MUTATION query" not in joined, joined


def test_groovy_commented_def_live_sql_execute_not_mutation_skip():
    """B2B-5269 diff_address: //def sql_* commented but sql.execute(sql_*) live."""
    script = (
        'def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesaccountID_2")\n'
        'def accountID = PropertiesPropertyVal.getPropertyValue("accountID2") as Integer\n'
        "def newAddressLine1 = context.expand("
        "'${http_get_account_details_200#Response#$['contactInfo']['address']['addressLine1']"
        "]}').replace(\"'\", \"''\")\n"
        "def newPhoneNumber = context.expand("
        "'${http_get_account_details_200#Response#$['contactInfo']['phone']['phoneNumber']"
        "]}').replace(\"'\", \"''\")\n"
        '//def sql_addressline1 = "update account set address1 = \'${newAddressLine1}\' '
        'where account_id = ${accountID}"\n'
        'def sql_phoneNumber = "update account set phone_number = \'${newPhoneNumber}\' '
        'where account_id = ${accountID}"\n'
        'sql.execute(sql_addressline1)\n'
        'sql.execute(sql_phoneNumber)\n'
    )
    rv = {"http_get_account_details_200": "http_get_account_details_200Res"}
    joined = "\n".join(
        groovy_translator.translate(
            script, rv, "Update_second_Account_info")[0])
    assert "SkipException" not in joined, joined
    assert "sql_addressline1" in joined and "was not defined" in joined, joined
    assert "Db.execute" in joined, joined


def test_jdbc_step_query_response_ref_to_placeholder():
    q = (
        "SELECT email_otp FROM segment.account_member_internal_security "
        "WHERE account_member_id =${post_create_program_account_member_with_prop_code"
        "#Response#$['memberId']} limit 1;"
    )
    translated, _ph = ra_converter.soapui_body_to_placeholders(q)
    assert "${" not in translated, translated
    assert "post_create_program_account_member_with_prop_code_Response_memberId" in translated, translated


def test_groovy_incomplete_guestid_script_infers_extract():
    """B2B-4878/B2B-3503: XML drops final setPropertyValue guestId line."""
    script = r'''
def jsonSlurper = new groovy.json.JsonSlurper();

def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesGuestId")
PropertiesPropertyVal.setPropertyValue("guestId", "")

def resp = testRunner.testCase.getTestStepByName("http_request_200_enroll_guest").getPropertyValue('response');

def resp_obj = jsonSlurper.parseText(resp)
// log.info("acc: "+resp_obj.accountId);
'''
    lines, meta = groovy_translator.translate(
        script,
        {"http_request_200_enroll_guest": "http_request_200_enroll_guestRes"},
        step_name_hint="Groovy Script for guestId",
    )
    joined = "\n".join(lines)
    assert meta["coverage"] == "FULL", meta
    assert "incomplete_guestid_extract" in meta["patterns_matched"], meta
    assert 'PropertiesGuestId.guestId' in joined, joined
    assert 'safeJsonExtract(http_request_200_enroll_guestRes, "guestId")' in joined, joined
    assert "NO PATTERN MATCHED" not in joined, joined


def test_groovy_complete_guestid_script_still_uses_setproperty_extract():
    """Complete guestId script must not double-emit incomplete fallback."""
    script = r'''
def jsonSlurper = new groovy.json.JsonSlurper();
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesGuestId")
PropertiesPropertyVal.setPropertyValue("guestId", "")
def resp = testRunner.testCase.getTestStepByName("http_request_200_enroll_guest").getPropertyValue('response');
def resp_obj = jsonSlurper.parseText(resp)
PropertiesPropertyVal.setPropertyValue("guestId", resp_obj.guestId.toString().trim())
'''
    lines, meta = groovy_translator.translate(
        script,
        {"http_request_200_enroll_guest": "http_request_200_enroll_guestRes"},
        step_name_hint="Groovy Script for guestId",
    )
    joined = "\n".join(lines)
    assert meta["coverage"] == "FULL", meta
    assert "setproperty_extract" in meta["patterns_matched"], meta
    assert "incomplete_guestid_extract" not in meta["patterns_matched"], meta
    assert joined.count("PropertiesGuestId.guestId") == 1, joined


def test_dbupdate_executeupdate_not_preflight_skip():
    script = (
        'def domain = context.expand( \'${Properties#websiteDomain}\' )\n'
        'def accountUpdate = sql.executeUpdate("UPDATE account SET status=\'L\' '
        'WHERE web_site=\'${domain}\'")\n'
    )
    joined = "\n".join(
        groovy_translator.translate(script, {}, "DBupdate")[0])
    assert "SkipException" not in joined, joined
    assert "mapSqlValues" in joined or "Db.execute" in joined, joined


def test_metadata_helpers_from_case_name():
    assert ra_converter._infer_partner(
        "B2B-9300_H4L_reject_account_when_owner_guest_profile_is_deleted") == "h4l"
    assert ra_converter._infer_partner("B2B-8984_Silhouette_Add_Member") == "silhouette"
    assert ra_converter._jira_issue_from_case("B2B-9300_H4L_reject") == "B2B-9300"
    assert ra_converter._infer_account_status("B2B-9300_H4L_reject_account") == "rejected"
    assert ra_converter._infer_account_status("B2B-8398_LTA_create_account_active") == "active"


def test_domain_api_rewrite_and_fingerprint():
    import fluent_scenario as fs
    assert fs.domain_receiver("hHonorsEnroll") == "guests"
    assert fs.domain_receiver("createProgramAccount") == "accounts"
    assert fs.domain_receiver("validateMemberTOTP") == "members"
    assert fs.domain_receiver("tokenRequest") == "client"
    src = '(body, q, h) -> client.hHonorsEnroll(tok, body));'
    assert "guests.hHonorsEnroll" in fs.rewrite_client_to_domain(src)
    assert fs.phase_body_key(
        ["(body, q, h) -> client.hHonorsEnroll(t, body);"]) == fs.phase_body_key(
        ["(body, q, h) -> guests.hHonorsEnroll(t, body);"])
    found = ra_converter._find_client_calls(
        "(body, q, h) -> guests.hHonorsEnroll(tok, body));")
    assert found and found[0][0] == "hHonorsEnroll", found
    agnostic = fs.suite_agnostic_body(
        ["(body, q, h) -> client.createProgramAccount(t, g, q, body);"])
    assert "accounts.createProgramAccount" in agnostic[0], agnostic


def test_method_body_reuse_same_body_keeps_readyapi_name():
    import method_body_reuse as mbr
    reuse = mbr.FluentMethodReuse()
    body = [
        'this.taRes = RestStep.exec(ctx, row, softAssert, holder, testCaseId)',
        '        .name("GroovyScript_travelAgentId")',
        '        .get("/travel-agents");',
    ]
    first = reuse.flow.reuse_or_allocate("createTravelAgency", body)
    second = reuse.flow.reuse_or_allocate("createTravelAgency2", body)
    assert first == "createTravelAgency", first
    assert second == "createTravelAgency", second
    other_body = [
        'this.otherRes = RestStep.exec(ctx, row, softAssert, holder, testCaseId)',
        '        .name("different_step");',
    ]
    other = reuse.flow.reuse_or_allocate("createTravelAgency", other_body)
    assert other == "createTravelAgency2", other
    assigned = reuse.assign_flow([
        ("createTravelAgency", body),
        ("createTravelAgency", body),
        ("createTravelAgency", other_body),
    ])
    assert [n for n, _ in assigned] == [
        "createTravelAgency", "createTravelAgency", "createTravelAgency2"]
    defs = mbr.unique_method_defs(assigned)
    assert [n for n, _ in defs] == ["createTravelAgency", "createTravelAgency2"]


def test_method_body_reuse_seeds_catalog_and_collapses_suffix():
    import method_body_reuse as mbr
    import fluent_scenario as fs
    body = ['LOG.info("groovy");', 'TestSupport.putExtracted(ctx, "k", "v");']
    key = fs.phase_body_key(body)
    reuse = mbr.FluentMethodReuse()
    reuse.seed_catalog({
        "phases": {
            "createTravelAgency2": {"key": key, "body": body, "cases": ["b"]},
            "createTravelAgency": {"key": key, "body": body, "cases": ["a"]},
        }
    })
    assert reuse.flow.lookup(body) == "createTravelAgency"
    collapsed = mbr.collapse_named_entries({
        "createTravelAgency2": {"key": key, "body": body, "cases": ["b"]},
        "createTravelAgency": {"key": key, "body": body, "cases": ["a"]},
        "enrollGuest": {"key": "other", "body": ["x"], "cases": ["c"]},
    })
    assert set(collapsed) == {"createTravelAgency", "enrollGuest"}
    assert collapsed["createTravelAgency"]["cases"] == ["a", "b"]


def test_compare_reuse_apply_before_emit_collapses_vote_names():
    import tempfile
    from collections import defaultdict
    import compare_reuse
    import fluent_scenario as fs

    body = ['LOG.info("ta");', 'TestSupport.putExtracted(ctx, "id", "1");']
    key = fs.phase_body_key(body)
    votes = defaultdict(lambda: defaultdict(list))
    votes["createTravelAgency"][key].append(
        {"case": "a", "body": body, "resp": []})
    votes["createTravelAgency2"][key].append(
        {"case": "b", "body": body, "resp": []})
    empty = tempfile.mkdtemp()
    em = SimpleNamespace(
        output_dir=empty,
        package_root="com.ak.api",
        _fluent_phase_votes=votes,
        _fluent_verify_votes=defaultdict(lambda: defaultdict(list)),
        _fluent_method_reuse=None,
        _compare_reuse_applied=False,
    )
    orig = compare_reuse.load_fluent_catalog
    compare_reuse.load_fluent_catalog = lambda: {
        "phases": {}, "verifies": {}, "bootstrap": None, "suites": {},
        "clientMethods": [],
    }
    try:
        compare_reuse.apply_before_emit(em)
        assert em._compare_reuse_applied is True
        assert list(em._fluent_phase_votes) == ["createTravelAgency"]
        compare_reuse.apply_before_emit(em)
        assert list(em._fluent_phase_votes) == ["createTravelAgency"]
    finally:
        compare_reuse.load_fluent_catalog = orig


def test_converter_hooks_compare_reuse_before_java():
    assert hasattr(ra_converter.Emitter, "_compare_reuse_before_java")
    src = open(os.path.join(HERE, "ra_converter.py"), encoding="utf-8").read()
    assert "self._compare_reuse_before_java()" in src
    assert src.count("self._compare_reuse_before_java()") >= 3


def test_delay_step_reason_is_java_string_literal():
    emitter = ra_converter.Emitter(output_dir=".", package_root="com.ak.api")
    short = "\n".join(emitter._render_step(
        ra_converter.DelayStep("wait 2", 2000), "ProgramAccount"))
    assert 'Poller.delay(2000L, "wait 2")' in short, short
    long_wait = "\n".join(emitter._render_step(
        ra_converter.DelayStep("10Sec", 10000), "ProgramAccount"))
    assert 'Poller.delay(10000L, "10Sec")' in long_wait, long_wait
    quoted = "\n".join(emitter._render_step(
        ra_converter.DelayStep('say "hi"', 5000), "ProgramAccount"))
    assert r'Poller.delay(5000L, "say \"hi\"")' in quoted, quoted


def test_converter_runs_self_tests_in_sequence():
    assert ra_converter.CONVERTER_SELF_TESTS == (
        "test_converter_fixes.py",
        "test_cross_case_contracts.py",
    )
    src = open(os.path.join(HERE, "ra_converter.py"), encoding="utf-8").read()
    assert "_run_converter_self_tests()" in src
    assert "skip_self_test" in src


def _rest_step(path="/businesses/{accountId}", body='{"status":"A"}',
               asserts=None, query_params=None, path_params=None,
               http_method="PUT"):
    return ra_converter.RestStep(
        step_name="http_request",
        service="api",
        method_name="UpdateProgramAccount",
        resource_path=path,
        http_method=http_method,
        endpoint="http://localhost",
        original_uri="",
        media_type="application/json",
        request_body=body,
        headers={},
        path_params=dict(path_params or {}),
        query_params=dict(query_params or {}),
        assertions=list(asserts or []),
    )


def _case(name, steps):
    return ra_converter.TestCase(id=name, name=name, description="", steps=steps)


def test_product_line_tokens_stripped_from_flow_stem():
    h4b = ra_converter._flow_stem_tokens(
        "H4B_active_account_active_owner_package_codes")
    lta = ra_converter._flow_stem_tokens(
        "LTA_active_account_active_owner_package_codes")
    h4l = ra_converter._flow_stem_tokens(
        "H4L_reject_account_when_owner_guest_profile_is_deleted")
    h4b_mid = ra_converter._flow_stem_tokens("reject_H4B_account")
    assert [t.lower() for t in h4b] == [t.lower() for t in lta], (h4b, lta)
    assert h4b[0].lower() == "active"
    assert "h4l" not in [t.lower() for t in h4l]
    assert [t.lower() for t in h4b_mid] == ["reject", "account"]


def test_h4b_lta_same_ticket_share_class_and_cluster_key():
    h4b = _case(
        "B2B-6383_H4B_active_account_active_owner_package_codes_200",
        [_rest_step(), ra_converter.DelayStep("wait", 2000)],
    )
    lta = _case(
        "B2B-6383_LTA_active_account_active_owner_package_codes_200",
        [_rest_step(), ra_converter.DelayStep("wait", 2000)],
    )
    assigned = ra_converter._flow_class_assignment([h4b, lta])
    assert assigned[id(h4b)] == assigned[id(lta)], (
        assigned[id(h4b)], assigned[id(lta)])
    clusters = ra_converter._cluster_cases_by_shape([h4b, lta])
    assert len(clusters) == 1, [[c.name for c in cl] for cl in clusters]
    assert {c.name for c in clusters[0]} == {h4b.name, lta.name}


def test_literal_param_value_and_csv_col():
    assert ra_converter._is_literal_param_value("owner")
    assert ra_converter._is_literal_param_value("")
    assert not ra_converter._is_literal_param_value("${Properties#include}")
    assert not ra_converter._is_literal_param_value("#Properties_include#")
    assert ra_converter._param_names({"b": "1", "a": "2"}) == ("a", "b")
    assert ra_converter._rest_param_csv_col(
        "qry", "http_request", "include") == "qry_http_request_include"


def test_same_rest_different_groovy_clusters():
    """Same request contract → one @Test + two CSV rows even when Groovy differs."""
    h4b = _case(
        "B2B-6383_H4B_active_account_active_owner_package_codes_200",
        [_rest_step(), ra_converter.GroovyStep("g", 'log.info("h4b")')],
    )
    lta = _case(
        "B2B-6383_LTA_active_account_active_owner_package_codes_200",
        [_rest_step(), ra_converter.GroovyStep(
            "g", 'def id = context.expand("${Properties#travelAgentId}")')],
    )
    clusters = ra_converter._cluster_cases_by_shape([h4b, lta])
    assert len(clusters) == 1, [[c.name for c in cl] for cl in clusters]
    assert {c.name for c in clusters[0]} == {h4b.name, lta.name}


def test_same_body_path_query_keys_different_values_cluster():
    a = _case(
        "B2B-1000_H4B_get_account_200",
        [_rest_step(
            path="/businesses/{accountId}",
            body='{"status":"active"}',
            path_params={"accountId": "${Properties#accountID}"},
            query_params={"include": "owner"},
        )],
    )
    b = _case(
        "B2B-1000_LTA_get_account_200",
        [_rest_step(
            path="/businesses/{accountId}",
            body='{"status":"limited"}',
            path_params={"accountId": "${Properties#ltaAccountId}"},
            query_params={"include": "members"},
        )],
    )
    clusters = ra_converter._cluster_cases_by_shape([a, b])
    assert len(clusters) == 1, [[c.name for c in cl] for cl in clusters]


def test_different_query_param_keys_do_not_cluster():
    a = _case("B2B-1_H4B_get_200", [_rest_step(
        query_params={"include": "owner"})])
    b = _case("B2B-1_LTA_get_200", [_rest_step(
        query_params={"force": "true"})])
    clusters = ra_converter._cluster_cases_by_shape([a, b])
    assert len(clusters) == 2, [[c.name for c in cl] for cl in clusters]


def test_different_path_param_keys_do_not_cluster():
    a = _case("B2B-2_H4B_get_200", [_rest_step(
        path="/businesses/{accountId}",
        path_params={"accountId": "1"})])
    b = _case("B2B-2_LTA_get_200", [_rest_step(
        path="/businesses/{accountId}",
        path_params={"accountId": "1", "guestId": "2"})])
    clusters = ra_converter._cluster_cases_by_shape([a, b])
    assert len(clusters) == 2, [[c.name for c in cl] for cl in clusters]


def test_different_body_shape_does_not_cluster():
    a = _case("B2B-3_H4B_patch_200", [_rest_step(body='{"status":"A"}')])
    b = _case("B2B-3_LTA_patch_200", [_rest_step(
        body='{"status":"A","reason":"x"}')])
    clusters = ra_converter._cluster_cases_by_shape([a, b])
    assert len(clusters) == 2, [[c.name for c in cl] for cl in clusters]


def test_extra_rest_step_does_not_equal_length_cluster():
    a = _case("B2B-4_H4B_create_200", [_rest_step()])
    b = _case("B2B-4_LTA_create_200", [
        _rest_step(),
        _rest_step(path="/travel-agencies", body='{"name":"x"}'),
    ])
    clusters = ra_converter._cluster_cases_by_shape([a, b])
    assert len(clusters) == 2, [[c.name for c in cl] for cl in clusters]


def test_same_rest_different_assert_paths_do_not_cluster():
    a = _case("B2B-5_H4B_get_200", [_rest_step(asserts=[
        ra_converter.Assertion(
            type="JsonPath Match", name="status",
            config={"path": "$.status", "content": "active"}),
    ])])
    b = _case("B2B-5_LTA_get_400", [_rest_step(asserts=[
        ra_converter.Assertion(
            type="JsonPath Match", name="error",
            config={"path": "$.errorCode", "content": "INVALID"}),
    ])])
    clusters = ra_converter._cluster_cases_by_shape([a, b])
    assert len(clusters) == 2, [[c.name for c in cl] for cl in clusters]


def test_add_employee_does_not_prefix_merge_travelcoordinator():
    employee = _case(
        "B2B-3627_patch_pending_account_add_employee",
        [_rest_step("/members/{memberId}", '{"role":"employee"}')],
    )
    travel = _case(
        "B2B-3627_patch_pending_account_add_travelcoordinator",
        [
            _rest_step("/members/{memberId}", '{"role":"employee"}'),
            _rest_step("/members/{memberId}/activate", '{"ok":true}'),
        ],
    )
    shape = ra_converter._cluster_cases_by_shape([employee, travel])
    merged = ra_converter._merge_prefix_clusters(shape)
    assert len(merged) == 2, [
        ([c.name for c in cl], sm) for cl, sm in merged]


def test_salesforce_jwt_client_emit_uses_requestToken():
    src_path = os.path.join(HERE, "ra_converter.py")
    with open(src_path, encoding="utf-8") as fh:
        src = fh.read()
    assert "SalesforceAuth.requestToken(queryParams)" in src
    assert "SalesforceAuth.resolveAccountId()" in src
    assert "_salesforce_empty_id_resolve_java" in src


def test_salesforce_oauth_query_not_csv_wrapped():
    step = ra_converter.RestStep(
        step_name="sf-token-Request",
        service="Salesforce",
        method_name="Auth",
        resource_path="/oauth2/token",
        http_method="POST",
        endpoint="https://test.salesforce.com",
        original_uri="",
        media_type="application/json",
        request_body="",
        headers={},
        path_params={},
        query_params={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "${#Project#salesforce_assertion}",
        },
        assertions=[],
    )
    assert ra_converter._is_salesforce_oauth_step(step)
    assert ra_converter._salesforce_oauth_query_emit(
        "grant_type", "urn:ietf:params:oauth:grant-type:jwt-bearer"
    ) == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assert ra_converter._salesforce_oauth_query_emit(
        "assertion", "${#Project#salesforce_assertion}"
    ) == "#salesforce_assertion#"
    assert ra_converter._skip_salesforce_oauth_csv_query(step, "grant_type")
    assert ra_converter._skip_salesforce_oauth_csv_query(step, "assertion")
    hilton = _rest_step(query_params={"include": "true"})
    assert not ra_converter._is_salesforce_oauth_step(hilton)
    assert not ra_converter._skip_salesforce_oauth_csv_query(hilton, "include")


def test_catalog_delay_reason_is_quoted_in_scenario_steps():
    from fluent_scenario import suite_agnostic_body
    body = suite_agnostic_body([
        "com.ak.api.retry.Poller.delay(10000L, 10Sec);",
        'com.ak.api.retry.Poller.delay(1000L, "already");',
        "com.ak.api.retry.Poller.delay(1000L, Delay 4);",
    ])
    assert 'Poller.delay(10000L, "10Sec")' in body[0], body[0]
    assert 'Poller.delay(1000L, "already")' in body[1], body[1]
    assert 'Poller.delay(1000L, "Delay 4")' in body[2], body[2]


def test_catalog_only_fluent_phase_is_kept():
    catalog = {"k": [{"case": "catalog", "body": ["x"]}]}
    assert ra_converter._pick_shared_fluent_variant(catalog)[0] == "k"
    solo = {"k": [{"case": "suite/a", "body": ["x"]}]}
    assert ra_converter._pick_shared_fluent_variant(solo) is None
    shared = {"k": [{"case": "a", "body": ["x"]}, {"case": "b", "body": ["x"]}]}
    assert ra_converter._pick_shared_fluent_variant(shared)[0] == "k"


def test_setup_helper_rest_calls_stay_on_client():
    assert ra_converter._rest_call_receiver("hHonorsEnroll", True) == "client"
    assert ra_converter._rest_call_receiver("createProgramAccount", True) == "client"
    assert ra_converter._rest_call_receiver("hHonorsEnroll", False) == "guests"
    assert ra_converter._rest_call_receiver("createProgramAccount", False) == "accounts"
    assert ra_converter._rest_call_receiver("tokenRequest", False) == "client"


def test_existing_scenario_steps_resp_fields_merged():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pkg = os.path.join(tmp, "src", "main", "java", "com", "ak", "api",
                           "support", "scenario")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "ScenarioSteps.java"), "w", encoding="utf-8") as fh:
            fh.write("protected Response http_request_200_1Res;\n"
                     "protected Response SendInvitationLinkRes;\n")
        got = ra_converter._existing_scenario_steps_resp_fields(tmp, "com.ak.api")
        assert got == ["http_request_200_1Res", "SendInvitationLinkRes"], got
        assert ra_converter._existing_scenario_steps_resp_fields(
            tmp, "com.other") == []


def test_class_names_differing_only_by_case_are_disambiguated():
    """Two SoapUI cases whose class names differ only in case must NOT map
    to the same file.

    The file path is derived from the class name, so on a case-insensitive
    filesystem (Windows, default macOS) the second emit reopens the FIRST
    file: one class body is silently destroyed and the surviving file's
    name no longer matches the class inside it. Real occurrence:
    B2B7389MemebrInviteTest vs B2B7389MemebrinviteTest.
    """
    em = ra_converter.Emitter(output_dir=".", package_root="com.ak.api",
                              suite_name="s", max_name_len=40)
    pkg = "com.ak.api.tests.imported.s.memberinvites"

    def claim(class_name):
        """Mirror the guard in emit_test_class_per_suite."""
        fqn = f"{pkg}.{class_name}"
        if fqn.lower() in em._emitted_class_fqns:
            import hashlib as _hl
            stem = class_name[:-4] if class_name.endswith("Test") else class_name
            attempt = 0
            while True:
                seed = "s" if attempt == 0 else f"s#{attempt}"
                suffix = _hl.sha1(seed.encode("utf-8")).hexdigest()[:6].upper()
                class_name = f"{stem}_{suffix}Test"
                fqn = f"{pkg}.{class_name}"
                if fqn.lower() not in em._emitted_class_fqns:
                    break
                attempt += 1
        em._emitted_class_fqns.add(fqn.lower())
        return class_name

    first = claim("B2B7389MemebrInviteTest")
    second = claim("B2B7389MemebrinviteTest")
    assert first == "B2B7389MemebrInviteTest", first
    assert second != first, (first, second)
    # The decisive assertion: distinct even when case is ignored, because
    # that is what the filesystem compares.
    assert first.lower() != second.lower(), (first, second)
    assert second.endswith("Test"), second

    # A third, also case-colliding, must get its own name too.
    third = claim("b2b7389memebrinvitetest".upper()[:1]
                  + "2B7389MemebrINVITETest")
    assert len({first.lower(), second.lower(), third.lower()}) == 3, (
        first, second, third)




# ---------------------------------------------------------------------------
# Mutating-SQL clustering guard (_mutation_shape_sig)
#
# Regression: ReadyAPI cases `B2B-1877_post_activate_account_
# lowConfidenceCompanyMatch_empty` (DBupdate sets status='L') and
# `..._rejected` (sets status='R') have an identical REST contract, so they
# clustered into one @Test + 2 CSV rows. The emitted method ran cluster[0]'s
# script body for BOTH rows, so row 2 set the account to *limited* and then
# asserted `status == "rejected"` -> "expected [rejected] ... found [false]".
# ---------------------------------------------------------------------------

def _groovy_update(status, extra=""):
    return ra_converter.GroovyStep("DBupdate", (
        'def websiteDomain = context.expand( "${Properties#websiteDomain}" )\n'
        'def accountUpdate = sql.executeUpdate("UPDATE account SET status='
        + repr(status).replace('"', "'") + extra
        + ' WHERE web_site=${websiteDomain}")\n'
        'log.info(accountUpdate)'))


def test_different_mutating_sql_splits_cluster():
    """status='L' and status='R' must not share a @Test method."""
    empty = _case(
        "B2B-1877_post_activate_account_lowConfidenceCompanyMatch_empty",
        [_rest_step(), _groovy_update("L", " ,attestation_failure_reason = ''")])
    rejected = _case(
        "B2B-1877_post_activate_account_lowConfidenceCompanyMatch_rejected",
        [_rest_step(), _groovy_update("R", " ,reject_Reason = 'x'")])
    clusters = ra_converter._cluster_cases_by_shape([empty, rejected])
    assert len(clusters) == 2, [[c.name for c in cl] for cl in clusters]


def test_same_mutating_sql_still_clusters():
    """Identical mutation -> still one @Test + 2 CSV rows."""
    a = _case("B2B-1_H4B_x_200", [_rest_step(), _groovy_update("L")])
    b = _case("B2B-1_LTA_x_200", [_rest_step(), _groovy_update("L")])
    clusters = ra_converter._cluster_cases_by_shape([a, b])
    assert len(clusters) == 1, [[c.name for c in cl] for cl in clusters]


def test_mutation_differing_only_in_expansion_still_clusters():
    """A value that becomes a CSV cell must not split the cluster."""
    a = _case("B2B-2_H4B_x_200", [_rest_step(), ra_converter.GroovyStep(
        "DBupdate", 'sql.executeUpdate("UPDATE account SET s=1 '
                    'WHERE web_site=${Properties#domainA}")')])
    b = _case("B2B-2_LTA_x_200", [_rest_step(), ra_converter.GroovyStep(
        "DBupdate", 'sql.executeUpdate("UPDATE account SET s=1 '
                    'WHERE web_site=${Properties#domainB}")')])
    assert (ra_converter._mutation_shape_sig(a)
            == ra_converter._mutation_shape_sig(b))
    assert len(ra_converter._cluster_cases_by_shape([a, b])) == 1


def test_commented_out_mutation_does_not_split():
    """A commented-out UPDATE changes nothing at runtime."""
    a = _case("B2B-3_H4B_x_200", [_rest_step(), ra_converter.GroovyStep(
        "g", 'log.info("hi")')])
    b = _case("B2B-3_LTA_x_200", [_rest_step(), ra_converter.GroovyStep(
        "g", '// sql.executeUpdate("UPDATE account SET s=1")\nlog.info("hi")')])
    assert ra_converter._mutation_shape_sig(b) == ()
    assert len(ra_converter._cluster_cases_by_shape([a, b])) == 1


def test_mutation_present_vs_absent_splits():
    """One case wiping a table must not share a @Test with one that doesn't."""
    a = _case("B2B-4_H4B_x_200", [_rest_step(), ra_converter.GroovyStep(
        "g", 'sql.execute("DELETE FROM account")')])
    b = _case("B2B-4_LTA_x_200", [_rest_step(), ra_converter.GroovyStep(
        "g", 'log.info("no db work")')])
    assert len(ra_converter._cluster_cases_by_shape([a, b])) == 2


def test_select_only_groovy_is_not_a_mutation():
    """Reads never split a cluster."""
    c = _case("B2B-5_x_200", [_rest_step(), ra_converter.GroovyStep(
        "g", 'def r = sql.rows("SELECT * FROM account WHERE id=1")')])
    assert ra_converter._mutation_shape_sig(c) == ()


def test_prefix_merge_refuses_conflicting_mutation():
    """A shorter case whose mutation differs is not folded into a longer one.

    The merged method runs the LONGER case's body, so folding would leave
    the DB in the longer case's state before the shorter's assertions.
    """
    long_case = _case("B2B-6_post_account_200_H4B", [
        _rest_step(path="/businesses"), _groovy_update("L"),
        _rest_step(path="/businesses/{accountId}")])
    short_case = _case("B2B-6_post_account_200_LTA", [
        _rest_step(path="/businesses"), _groovy_update("R")])
    merged = ra_converter._merge_prefix_clusters([[long_case], [short_case]])
    assert len(merged) == 2, [[c.name for c in cl] for cl, _ in merged]


def test_prefix_merge_still_folds_matching_mutation():
    long_case = _case("B2B-7_post_account_200_H4B", [
        _rest_step(path="/businesses"), _groovy_update("L"),
        _rest_step(path="/businesses/{accountId}")])
    short_case = _case("B2B-7_post_account_200_LTA", [
        _rest_step(path="/businesses"), _groovy_update("L")])
    merged = ra_converter._merge_prefix_clusters([[long_case], [short_case]])
    assert len(merged) == 1, [[c.name for c in cl] for cl, _ in merged]


# ---------------------------------------------------------------------------
# setPropertyValue(key, "string-literal") publication
#
# The translator published `setPropertyValue("k", someVar)` but not
# `setPropertyValue("k", "literal")`. Literal-valued fields fell through to
# CtxFields.generateStandard, which filled them with RANDOM data -- so
# `topicenv` became a fake username instead of "programaccounts-stg", and
# `Domain` became a fake domain instead of the author's "explorer.de".
# ---------------------------------------------------------------------------

_ENV_SCRIPT = '''
def p = testRunner.testCase.getTestStepByName("Properties")
p.setPropertyValue("Domain", "explorer.de")
p.setPropertyValue("Email", generatedEmail)
p.setPropertyValue("GeneratedTokenID", "")
def env = context.testCase.testSuite.project.getActiveEnvironment ().getName ()
if(env  ==  "EKS_TST")
{ log.info("x")
    p.setPropertyValue("topicenv", "programaccounts-test")
} else if (env  ==  "EKS_STG")
{ log.info("x")
    p.setPropertyValue("topicenv", "programaccounts-stg")
}
'''


def test_literal_setproperty_published_unconditionally():
    uncond, envcond = groovy_translator._env_literal_publications(_ENV_SCRIPT)
    assert uncond[("Properties", "Domain")] == "explorer.de"


def test_literal_setproperty_skips_empty_clears():
    """setPropertyValue("X", "") is ReadyAPI's 'clear' -- not a value."""
    uncond, _ = groovy_translator._env_literal_publications(_ENV_SCRIPT)
    assert ("Properties", "GeneratedTokenID") not in uncond


def test_literal_setproperty_skips_variable_values():
    """Variable-valued writes stay with the existing def-publication path."""
    uncond, _ = groovy_translator._env_literal_publications(_ENV_SCRIPT)
    assert ("Properties", "Email") not in uncond


def test_env_conditional_literals_collected_per_label():
    _uncond, envcond = groovy_translator._env_literal_publications(_ENV_SCRIPT)
    assert envcond[("Properties", "topicenv")] == {
        "EKS_TST": "programaccounts-test",
        "EKS_STG": "programaccounts-stg",
    }


def test_env_conditional_keeps_readyapi_branch_order():
    """putEnvScoped's no-match fallback uses the FIRST entry."""
    _u, envcond = groovy_translator._env_literal_publications(_ENV_SCRIPT)
    assert list(envcond[("Properties", "topicenv")]) == ["EKS_TST", "EKS_STG"]
    java = groovy_translator._emit_literal_publication_lines({}, envcond)[0]
    assert java.index("EKS_TST") < java.index("EKS_STG"), java
    assert "TestSupport.envMap(" in java


def test_non_env_conditional_literal_is_not_published():
    """A literal in some OTHER branch must not be hoisted out of it."""
    script = '''
def p = testRunner.testCase.getTestStepByName("Properties")
if (resp.status == 400) { p.setPropertyValue("Result", "FAILED") }
p.setPropertyValue("Domain", "always.com")
'''
    uncond, envcond = groovy_translator._env_literal_publications(script)
    assert ("Properties", "Result") not in uncond
    assert ("Properties", "Result") not in envcond
    assert uncond[("Properties", "Domain")] == "always.com"


def test_brace_blocks_ignores_braces_inside_strings():
    """A '{' in a GString/SQL literal must not open a phantom block."""
    script = 'def q = "SELECT {not a block} FROM t"\nif (a == "X") { y() }'
    kinds = [k for _o, _c, k, _cond in
             groovy_translator._brace_blocks(script)]
    assert kinds == ["if"], kinds


def test_literal_fields_excluded_from_random_generation():
    """The generator must not overwrite an author-set literal."""
    lines, _meta = groovy_translator.translate(
        _ENV_SCRIPT, {}, {}, "DataGenInput")
    java = chr(10).join(lines)
    gen = [l for l in lines if "generateStandard" in l]
    assert gen, java
    # Literal-valued keys must not be handed to the random generator.
    assert "topicenv" not in gen[0], gen[0]
    assert '"Domain"' not in gen[0], gen[0]
    # ...they are published explicitly instead, AFTER the generator runs.
    assert 'putEnvScoped(ctx, "Properties.topicenv"' in java, java
    assert 'putExtracted(ctx, "Properties.Domain", "explorer.de")' in java, java
    assert java.index("generateStandard") < java.index("putEnvScoped"), java


# ---------------------------------------------------------------------------
# Stale author-editable framework files
#
# `_AUTHOR_EDITABLE_BASENAMES` are SKIP-IF-EXISTS so author edits survive a
# reconvert. That silently shipped a broken tree: adding `putEnvScoped` /
# `envMap` to ImportedScenario meant every EXISTING checkout kept its old
# copy, the emitted ScenarioSteps called a method that was not there, and
# `mvn compile` failed -- surfacing as "cannot find symbol" on every
# OnboardingFlow* entry class, because they all extend it.
# ---------------------------------------------------------------------------

def test_rev_marker_detects_stale_file(tmp_path):
    """A bumped framework-rev means refresh, whatever changed inside."""
    old = tmp_path / "CtxFields.java"
    old.write_text("package p;\n// ra_converter-framework-rev: 1\nclass X {}\n",
                   encoding="utf8")
    bundled = "package p;\n// ra_converter-framework-rev: 2\nclass X {}\n"
    assert ra_converter._staleness_reason(str(old), bundled)


def test_rev_marker_absent_on_disk_is_stale(tmp_path):
    """A copy from before markers existed reads as rev 0."""
    old = tmp_path / "CtxFields.java"
    old.write_text("package p;\nclass X {}\n", encoding="utf8")
    bundled = "package p;\n// ra_converter-framework-rev: 2\nclass X {}\n"
    reason = ra_converter._staleness_reason(str(old), bundled)
    assert reason and "0" in reason


def test_current_rev_is_left_alone(tmp_path):
    """Author edits at the current rev must NOT be clobbered."""
    cur = tmp_path / "CtxFields.java"
    cur.write_text("package p;\n// ra_converter-framework-rev: 2\n"
                   "class X { void myOwnEdit() {} }\n", encoding="utf8")
    bundled = "package p;\n// ra_converter-framework-rev: 2\nclass X {}\n"
    assert ra_converter._staleness_reason(str(cur), bundled) is None


def test_missing_declaration_detected_when_no_rev_marker():
    """Fallback for author-editable files emitted as strings (no marker)."""
    import tempfile, os as _os
    fd, path = tempfile.mkstemp(suffix=".java")
    with _os.fdopen(fd, "w", encoding="utf8") as fh:
        fh.write("package p;\nclass X { public void kept() {} }\n")
    try:
        bundled = ("package p;\nclass X { public void kept() {} "
                   "public static void putEnvScoped(Map a) {} }\n")
        reason = ra_converter._staleness_reason(path, bundled)
        assert reason and "putEnvScoped" in reason
    finally:
        _os.remove(path)


def test_every_bundled_framework_file_carries_a_rev():
    """A bundled file with no rev cannot signal staleness to existing trees."""
    import os as _os
    fw = _os.path.join(_os.path.dirname(_os.path.abspath(ra_converter.__file__)),
                       "framework")
    for name in _os.listdir(fw):
        if not name.endswith(".java"):
            continue
        with open(_os.path.join(fw, name), encoding="utf8") as fh:
            assert ra_converter._FRAMEWORK_REV_RX.search(fh.read()), name


_B2B5530_GUESTID_SCRIPT = r'''
def jsonSlurper = new groovy.json.JsonSlurper();
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("PropertiesGuestId")
PropertiesPropertyVal.setPropertyValue("guestId", "")
def resp = testRunner.testCase.getTestStepByName("http_request_200_enroll_guest").getPropertyValue('response');
def resp2 = testRunner.testCase.getTestStepByName("http_request_200_enroll_guest2").getPropertyValue('response');
def resp3 = testRunner.testCase.getTestStepByName("http_request_200_enroll_guest3").getPropertyValue('response');
def resp_obj = jsonSlurper.parseText(resp)
PropertiesPropertyVal.setPropertyValue("guestId", resp_obj.guestId.toString().trim())
PropertiesPropertyVal.setPropertyValue("hhonorsNumber", resp_obj.hhonorsNumber.toString().trim())
//for guest 2
def resp_obj2 = jsonSlurper.parseText(resp2)
PropertiesPropertyVal.setPropertyValue("guestId2", resp_obj2.guestId.toString().trim())
PropertiesPropertyVal.setPropertyValue("hhonorsNumber2", resp_obj2.hhonorsNumber.toString().trim())
def resp_obj3 = jsonSlurper.parseText(resp3)
PropertiesPropertyVal.setPropertyValue("guestId3", resp_obj3.guestId.toString().trim())
'''


def _translate_guestid_groovy(script: str) -> str:
    lines, _meta = groovy_translator.translate(
        script,
        {"http_request_200_enroll_guest": "http_request_200_enroll_guestRes",
         "http_request_200_enroll_guest2": "http_request_200_enroll_guest2Res",
         "http_request_200_enroll_guest3": "http_request_200_enroll_guest3Res"},
        step_name_hint="Groovy Script for guestId",
    )
    return "\n".join(lines)


def test_groovy_multi_response_script_publishes_each_id_from_its_own_response():
    """B2B-5530: guestId2/guestId3 were extracted from guest 1's response."""
    joined = _translate_guestid_groovy(_B2B5530_GUESTID_SCRIPT)
    assert ('"PropertiesGuestId.guestId", com.ak.api.rest.utilities.RestUtilities'
            '.safeJsonExtract(http_request_200_enroll_guestRes, "guestId")') in joined, joined
    assert ('"PropertiesGuestId.guestId2", com.ak.api.rest.utilities.RestUtilities'
            '.safeJsonExtract(http_request_200_enroll_guest2Res, "guestId")') in joined, joined
    assert ('"PropertiesGuestId.hhonorsNumber2", com.ak.api.rest.utilities.RestUtilities'
            '.safeJsonExtract(http_request_200_enroll_guest2Res, "hhonorsNumber")') in joined, joined
    assert ('"PropertiesGuestId.guestId3", com.ak.api.rest.utilities.RestUtilities'
            '.safeJsonExtract(http_request_200_enroll_guest3Res, "guestId")') in joined, joined


def test_groovy_multi_response_two_line_step_ref_form_is_resolved():
    script = r'''
def jsonSlurper = new groovy.json.JsonSlurper();
def P = testRunner.testCase.getTestStepByName("PropertiesaccountID")
def s1 = testRunner.testCase.getTestStepByName("http_request_200_createAccount")
def s2 = testRunner.testCase.getTestStepByName("http_request_200_createAccount2")
def r1 = testRunner.testCase.getTestStepByName("http_request_200_createAccount").getPropertyValue('response');
def r2 = s2.getPropertyValue("response")
def o1 = jsonSlurper.parseText(r1)
def o2 = jsonSlurper.parseText(r2)
P.setPropertyValue("accountID", o1.accountId.toString().trim())
P.setPropertyValue("accountID2", o2.accountId.toString().trim())
'''
    lines, _meta = groovy_translator.translate(
        script,
        {"http_request_200_createAccount": "http_request_200_createAccountRes",
         "http_request_200_createAccount2": "http_request_200_createAccount2Res"},
        step_name_hint="Groovy Script for accountID",
    )
    joined = "\n".join(lines)
    assert 'safeJsonExtract(http_request_200_createAccount2Res, "accountId")' in joined, joined
    assert ('"PropertiesaccountID.accountID", com.ak.api.rest.utilities.RestUtilities'
            '.safeJsonExtract(http_request_200_createAccountRes, "accountId")') in joined, joined


def test_testsupport_ctxget_consults_declared_aliases_before_name_walk():
    """B2B-6860: the first-match walk returned DataGenInput's random
    Properties.accountID ahead of the real PropertiesDetails.accountID."""
    src = open(os.path.join(os.path.dirname(__file__), "ra_converter.py"),
               encoding="utf8").read()
    # The resolution steps live in ctxGetResolved; ctxGetRaw is now the thin
    # wrapper that Bearer-normalises what it returns.
    start = src.index("private static String ctxGetResolved(Map<String, String> ctx, String primaryKey) {{\n"
                      "        if (ctx == null || primaryKey == null) return \"\";")
    body = src[start:src.index("private static String expandPlaceholders", start)]
    known_empty = body.index("if (ctx.containsKey(primaryKey))")
    declared = body.index("ScenarioContext.resolveDeclared(ctx, primaryKey)")
    walk = body.index("// Walk every ctx key -- match by suffix on the field name.")
    assert known_empty < declared < walk


def test_testsupport_ctxget_tries_same_key_other_case_before_aliases():
    """B2B-3216: ReadyAPI resolves ${PropertiesGuestId#guestID} to guestId."""
    src = open(os.path.join(os.path.dirname(__file__), "ra_converter.py"),
               encoding="utf8").read()
    # The resolution steps live in ctxGetResolved; ctxGetRaw is now the thin
    # wrapper that Bearer-normalises what it returns.
    start = src.index("private static String ctxGetResolved(Map<String, String> ctx, String primaryKey) {{\n"
                      "        if (ctx == null || primaryKey == null) return \"\";")
    body = src[start:src.index("private static String expandPlaceholders", start)]
    known_empty = body.index("if (ctx.containsKey(primaryKey))")
    same_key = body.index("!k.equalsIgnoreCase(primaryKey)")
    declared = body.index("ScenarioContext.resolveDeclared(ctx, primaryKey)")
    assert known_empty < same_key < declared


def _b2b6830_case():
    pre = SimpleNamespace(step_name="put_rolePremission_200", path_params={
        "accountId": "${PropertiesaccountID#accountID}",
        "guestId": "${PropertiesGuestId#guestId1}",
        "memberId": "${get_request_200_member_details#Response#$[0]['memberId']}"})
    older = SimpleNamespace(step_name="post_confirm_validation_limited", path_params={
        "guestId": "${PropertiesGuestId#guestId}"})
    prefs = SimpleNamespace(step_name="get_preferences", path_params={
        "accountId": "2000222783", "guestId": "1900718863", "memberId": "219827"})
    return SimpleNamespace(steps=[older, pre, prefs]), prefs


def test_hardcoded_path_id_takes_nearest_earlier_sibling_placeholder():
    """B2B-6830: get_preferences must reuse the case's own ids, not random Properties.*."""
    case, prefs = _b2b6830_case()
    known = {"get_request_200_member_details": "get_request_200_member_detailsRes"}
    assert ra_converter._sibling_path_param_expr(case, prefs, "guestId", known) == \
        "${PropertiesGuestId#guestId1}"
    assert ra_converter._sibling_path_param_expr(case, prefs, "accountId", known) == \
        "${PropertiesaccountID#accountID}"
    assert "get_request_200_member_details#Response" in \
        ra_converter._sibling_path_param_expr(case, prefs, "memberId", known)


def test_hardcoded_path_id_sibling_skips_unknown_response_and_foreign_step():
    case, prefs = _b2b6830_case()
    assert ra_converter._sibling_path_param_expr(case, prefs, "memberId", {}) is None
    assert ra_converter._sibling_path_param_expr(case, prefs, "travelAgentId", {}) is None
    stranger = SimpleNamespace(step_name="x", path_params={})
    assert ra_converter._sibling_path_param_expr(case, stranger, "guestId", {}) is None


_STAY_PERMISSION_SCRIPT = r'''
def generator = { String alphabet, int n -> "x" * n }
def generatedUser = generator( ('a'..'z').join(), 5 )
def generatedEmail = generatedUser + "@example.com"
def randomizer = new Random();
def randomstayPermissionList = ["viewEdit","view","private"]
def randomstayPermission = randomstayPermissionList[randomizer.nextInt(randomstayPermissionList.size())];
def country = "US"
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("Properties")
PropertiesPropertyVal.setPropertyValue("randomstayPermission", randomstayPermission)
PropertiesPropertyVal.setPropertyValue("country", country)
PropertiesPropertyVal.setPropertyValue("Firstname", firstname)
'''


def test_groovy_list_pick_publishes_one_of_the_list_not_a_random_word():
    """B2B-6832: stayPermission must be viewEdit/view/private, not an invented word."""
    lines, _meta = groovy_translator.translate(
        _STAY_PERMISSION_SCRIPT, {}, step_name_hint="DataGenInput")
    java = "\n".join(lines)
    assert ('putExtracted(ctx, "Properties.randomstayPermission", '
            'com.ak.api.data.FakeData.oneOf("viewEdit", "view", "private"))') in java, java
    gen = [l for l in lines if "generateStandard" in l]
    assert gen and '"randomstayPermission"' not in gen[0], gen
    assert java.index("generateStandard") < java.index("randomstayPermission"), java


def test_groovy_variable_held_literal_is_published_not_generated():
    lines, _meta = groovy_translator.translate(
        _STAY_PERMISSION_SCRIPT, {}, step_name_hint="DataGenInput")
    java = "\n".join(lines)
    assert 'putExtracted(ctx, "Properties.country", "US")' in java, java
    gen = [l for l in lines if "generateStandard" in l]
    assert gen and '"country"' not in gen[0], gen
    # a field with no literal/pick binding still gets generated
    assert '"Firstname"' in gen[0], gen[0]

def test_groovy_var_publication_respects_last_write_wins():
    """A later setPropertyValue owns the field -- do not publish the earlier pick."""
    script = chr(10).join([
        'def generator = { String alphabet, int n -> alphabet }',
        'def generatedEmail = "a@example.com"',
        'def p = ["a","b"]',
        'def pick = p[new Random().nextInt(p.size())]',
        'def keep = ["y","z"]',
        'def kept = keep[new Random().nextInt(keep.size())]',
        'def P = testRunner.testCase.getTestStepByName("Properties")',
        'P.setPropertyValue("role", pick)',
        'P.setPropertyValue("role", someOtherVar)',
        'P.setPropertyValue("mode", kept)',
    ])
    lines, _meta = groovy_translator.translate(
        script, {}, step_name_hint="DataGenInput")
    java = chr(10).join(lines)
    # the field whose LAST write is the pick IS published
    assert ('putExtracted(ctx, "Properties.mode", '
            'com.ak.api.data.FakeData.oneOf("y", "z"))') in java, java
    # the field a later write owns is NOT published from the earlier pick
    assert 'putExtracted(ctx, "Properties.role"' not in java, java


_TWO_NAMESPACE_SCRIPT = chr(10).join([
    'def generator = { String alphabet, int n -> alphabet }',
    'def generatedUser = generator( 5 )',
    'def generatedEmail = generatedUser + "@example.com"',
    'def P = testRunner.testCase.getTestStepByName("Properties")',
    'def P2 = testRunner.testCase.getTestStepByName("Properties_2")',
    'def E = testRunner.testCase.getTestStepByName("employeeProp")',
    'P.setPropertyValue("name", generatedName)',
    'P2.setPropertyValue("username2", generatedUsername2)',
    'P2.setPropertyValue("generatedemailAddress2", generatedEmail2)',
    'E.setPropertyValue("hilton-member-id", extractedMemberId)',
])


def test_second_properties_step_gets_its_own_generated_pack():
    """B2B-5269/6274: guest 2 enrolls from Properties_2, which was never generated."""
    lines, _meta = groovy_translator.translate(
        _TWO_NAMESPACE_SCRIPT, {}, step_name_hint="DataGenInput")
    java = chr(10).join(lines)
    gen = [l for l in lines if "generateStandard" in l]
    assert any('generateStandard(ctx, "Properties_2"' in l for l in gen), gen
    assert any('"username2"' in l and 'Properties_2' in l for l in gen), gen
    assert any('"generatedemailAddress2"' in l and 'Properties_2' in l for l in gen), gen


def test_properties_namespace_line_is_unchanged_by_second_namespace():
    lines, _meta = groovy_translator.translate(
        _TWO_NAMESPACE_SCRIPT, {}, step_name_hint="DataGenInput")
    props = [l for l in lines if 'generateStandard(ctx, "Properties"' in l]
    assert len(props) == 1, props
    # positive control: the second pack must exist, or this test passes by
    # doing nothing (it did, against HEAD, until this line was added)
    gen = [l for l in lines if "generateStandard" in l]
    assert any('generateStandard(ctx, "Properties_2"' in l for l in gen), gen
    # the first pack still carries the field the script set on Properties
    assert '"name"' in props[0], props[0]
    # and the second pack's fields are NOT removed from it by relocation
    assert 'Properties_2' not in props[0], props[0]


def test_id_only_second_namespace_gets_no_generated_pack():
    """employeeProp holds ids an extract publishes -- a fake id would mask a failure."""
    lines, _meta = groovy_translator.translate(
        _TWO_NAMESPACE_SCRIPT, {}, step_name_hint="DataGenInput")
    gen = [l for l in lines if "generateStandard" in l]
    assert not any('"employeeProp"' in l for l in gen), gen
    # positive control: the identity namespace in the same script IS generated
    assert any('generateStandard(ctx, "Properties_2"' in l for l in gen), gen


def test_template_index_maps_readyapi_names_to_paths(tmp_path):
    """(case, step) is the handle that survives a reconvert.

    Templates.<NAME> does not: the ordinal follows content-hash order.
    """
    import csv as _csv
    import io as _io
    em = ra_converter.Emitter(output_dir=str(tmp_path),
                              package_root="com.ak.api", suite_name="demo")
    em._template_path_by_step = {
        ("Reject_Limited_account_200", "Reject_Account"):
            "templates/demo/businesses/reject_aaaa.json",
        ("B2B-2065_create, activate", "createAccount"):
            "templates/demo/businesses/createaccount_bbbb.json",
    }
    em._template_const_by_path = {
        "templates/demo/businesses/reject_aaaa.json": "BUSINESSES_REJECT",
    }
    rel = em.emit_template_index()
    assert rel == "src/main/resources/templates/demo/_index.csv", rel

    text = (tmp_path / rel).read_text(encoding="utf-8")
    rows = list(_csv.reader(_io.StringIO(text)))
    assert rows[0] == ["case", "step", "template", "constant"], rows[0]
    body = {(r[0], r[1]): (r[2], r[3]) for r in rows[1:]}
    assert body[("Reject_Limited_account_200", "Reject_Account")] == (
        "templates/demo/businesses/reject_aaaa.json", "BUSINESSES_REJECT")
    # A comma inside a ReadyAPI case name must not split the row.
    assert body[("B2B-2065_create, activate", "createAccount")][0].endswith(
        "createaccount_bbbb.json")
    # A path with no constant still gets a row -- the whole point is that
    # the constant is the unstable half.
    assert body[("B2B-2065_create, activate", "createAccount")][1] == ""

    audit = tmp_path / "_audit" / "demo" / "templates.csv"
    assert audit.is_file(), "audit copy missing"
    assert audit.read_text(encoding="utf-8") == text


def test_template_index_skipped_when_suite_has_no_templates(tmp_path):
    """An empty suite must not leave a stray header-only index behind."""
    em = ra_converter.Emitter(output_dir=str(tmp_path),
                              package_root="com.ak.api", suite_name="empty")
    em._template_path_by_step = {}
    assert em.emit_template_index() is None
    assert not (tmp_path / "src").exists()
    assert not (tmp_path / "_audit").exists()


def test_gpath_drops_the_jsonpath_wildcard():
    """`[*]` resolves to null under GPath, so it must never be emitted.

    Measured on RestAssured with a two-element root array:
      [*]                      -> null
      [*].centralBillAuthAmount -> null
      $                        -> the root list
      centralBillAuthAmount    -> [10, 20]   (GPath spreads implicitly)
    """
    # root wildcard -> the root node
    assert ra_converter._jsonpath_to_gpath("$[*]") == "$"
    # wildcard + field -> the bare field, which GPath spreads
    assert ra_converter._jsonpath_to_gpath(
        "$[*]['centralBillAuthAmount']") == "centralBillAuthAmount"
    # mid-path wildcard
    assert ra_converter._jsonpath_to_gpath("$.items[*].a") == "items.a"
    # idempotent: an already-translated wildcard path still normalises
    assert ra_converter._jsonpath_to_gpath("[*]") == "$"


def test_gpath_translation_leaves_ordinary_paths_alone():
    """Positive control -- the wildcard fix must not disturb normal paths."""
    assert ra_converter._jsonpath_to_gpath(
        "$.notifications[0].message") == "notifications[0].message"
    assert ra_converter._jsonpath_to_gpath("$['a']['b'].c") == "a.b.c"
    assert ra_converter._jsonpath_to_gpath(
        "notifications[0].fields[0]") == "notifications[0].fields[0]"


def test_gpath_still_bails_on_unsupported_syntax():
    """Recursive descent and filters stay untouched, as before the fix."""
    assert ra_converter._jsonpath_to_gpath("$..field") == "$..field"
    assert ra_converter._jsonpath_to_gpath(
        "$[?(@.x==1)]") == "$[?(@.x==1)]"


def test_soapui_e_wrapper_becomes_an_index():
    """SoapUI shows a JSON array as repeated <e> elements.

    `e` is the array wrapper, not a field. Emitting it as a name gave
    `Fault.notifications.e.code`, which GPath cannot resolve; the correct
    shape is the one the JsonPath-style translator already produces.
    """
    x = ra_converter._soapui_xpath_to_jsonpath
    assert x("//ns1:Response[1]/ns1:Fault[1]/ns1:notifications[1]"
             "/ns1:e[1]/ns1:code[1]") == "Fault.notifications[0].code"
    assert x("//ns1:Response[1]/ns1:Fault[1]/ns1:notifications[1]"
             "/ns1:e[1]/ns1:fields[1]/ns1:e[1]") == "Fault.notifications[0].fields[0]"
    # SoapUI is 1-indexed: e[2] is the SECOND element
    assert x("//ns1:Response[1]/ns1:programAccountTypes[1]"
             "/ns1:e[2]") == "programAccountTypes[1]"
    # a leading wrapper means the ROOT array
    assert x("//ns1:Response[1]/ns1:e[1]/ns1:accountId[1]") == "[0].accountId"


def test_soapui_xpath_leaves_ordinary_steps_alone():
    """Positive control -- the <e> change must not disturb normal paths."""
    x = ra_converter._soapui_xpath_to_jsonpath
    assert x("//ns1:Response[1]/ns1:accountId[1]") == "accountId"
    assert x("declare namespace ns1='urn:x'; "
             "//ns1:Response[1]/ns1:members[3]/ns1:role[1]") == "members[2].role"
    assert x("") == ""


def test_emitted_test_support_has_the_hilton_token_fallback(tmp_path):
    """The ctxGet copy that generated code actually calls must recover a
    cached token.

    ImportedScenario.ctxGetRaw has this fallback; the per-suite TestSupport
    did not -- and generated call sites use TestSupport 5152 times against
    ImportedScenario's 2573, so the weaker resolver served two thirds of
    every ctx lookup, including every token lookup.

    Asserted on the EMITTED JAVA rather than on the template source, so the
    test still fails if the template stops being reached.
    """
    em = ra_converter.Emitter(output_dir=str(tmp_path),
                              package_root="com.ak.api", suite_name="probe")
    rel = em.emit_test_support()
    java = (tmp_path / rel).read_text(encoding="utf-8")

    assert "static boolean isHiltonTokenKey(" in java
    assert "static String hiltonTokenFallback(" in java
    assert "com.ak.api.auth.TokenCache.getAccessToken()" in java
    # BOTH call sites: present-but-empty key, and after the trailing-field walk
    assert java.count("hiltonTokenFallback(primaryKey, ctx)") == 2, java.count(
        "hiltonTokenFallback(primaryKey, ctx)")
    # the Bearer prefix is normalised per key, not blindly prepended
    assert 'contains("generatedtoken")' in java


def test_emitted_test_support_still_returns_empty_for_a_nontoken_key(tmp_path):
    """Positive control -- the fallback must be scoped to token keys only."""
    em = ra_converter.Emitter(output_dir=str(tmp_path),
                              package_root="com.ak.api", suite_name="probe")
    java = (tmp_path / em.emit_test_support()).read_text(encoding="utf-8")
    # the guard is a predicate, not an unconditional recovery
    assert "if (isHiltonTokenKey(primaryKey))" in java
    assert java.count("if (isHiltonTokenKey(primaryKey))") == 2


def test_bundled_framework_offers_live_domains_for_random_domain_placeholders():
    """Properties.RandomDomain / RandomDomain2 must consult DomainRules.

    Guards the WIRING, not the behaviour -- DomainRulesTest covers the logic.
    This exists because the generated copy under support/ is gitignored and
    the fallback is silent: if the call were dropped from the template, every
    row would quietly keep using the frozen CSV snapshot again and no test
    would fail.
    """
    import io
    import os
    import re as _re

    path = os.path.join(os.path.dirname(ra_converter.__file__),
                        "framework", "ImportedScenario.java")
    src = io.open(path, encoding="utf-8").read()

    assert "com.ak.api.db.repo.DomainRules.overrideOrCsv(row, d1, d2)" in src
    # Both placeholders resolved from the one call, so the pair stays distinct.
    assert "d1 = resolved[0];" in src
    assert "d2 = resolved[1];" in src

    m = _re.search(r"ra_converter-framework-rev:\s*(\d+)", src)
    assert m, "framework-rev header missing"
    assert int(m.group(1)) >= 11, (
        "framework-rev must be bumped past 10, else existing trees keep the "
        "old ImportedScenario and never receive this hook")


def test_bundled_normalize_domain_never_returns_null():
    """normalizeDomain must return "" for null/blank, never null.

    Every caller dereferences the result (.isEmpty / .equalsIgnoreCase).
    The template once carried an unreachable second null branch and a
    `isEmpty() ? null : d` tail, which NPE'd on a saved email of `user@`
    or a domain of `www.`. Guards the single-contract form.
    """
    import io
    import os
    import re as _re
    path = os.path.join(os.path.dirname(ra_converter.__file__),
                        "framework", "ImportedScenario.java")
    src = io.open(path, encoding="utf-8").read()
    body = src[src.index("private static String normalizeDomain"):]
    body = body[:body.index("\n    }\n") + 7]
    assert body.count("if (raw == null)") == 1, "unreachable duplicate null-check is back"
    assert "? null : d" not in body, "null-returning tail is back"
    assert 'return "";' in body
    m = _re.search(r"ra_converter-framework-rev:\s*(\d+)", src)
    assert m and int(m.group(1)) >= 12


def test_emitted_digest_listener_is_v3():
    """The listener is a converter OUTPUT, so v3 must live in the template.

    The v3 edits were first made to the emitted .java alone and were wiped
    by the very next --clean run -- which is how a digest that still said
    "first bad call" while recording the LAST failure got pasted twice.
    Guards the template markers, and that the committed .java agrees.
    """
    import io
    import os
    conv = os.path.join(os.path.dirname(ra_converter.__file__), "ra_converter.py")
    src = io.open(conv, encoding="utf-8").read()
    i = src.index("def emit_failure_digest_listener")
    j = src.index("def emit_progress_listener", i)
    tpl = src[i:j]
    for must in ("digest v3", "server said:", "StepOutcomes.firstFailure()",
                 "rootCauseSuffix(t)",
                 "StepOutcomes.firstFailureBody()", "ResponseMasking.mask(",
                 "OBSERVED", "INFERRED"):
        assert must in tpl, "template lost v3 marker: " + must
    for gone in ("digest v2", "lastFailure()", "Pattern[] MASKS", "recordAuthVerdict"):
        assert gone not in tpl, "template regressed to v2 marker: " + gone
    # the committed output must not drift from what the converter emits
    java = os.path.join(os.path.dirname(conv), "..", "..", "src", "main", "java",
                        "com", "ak", "api", "reporting", "FailureDigestListener.java")
    if os.path.exists(java):
        jsrc = io.open(java, encoding="utf-8").read()
        assert "digest v3" in jsrc and "server said:" in jsrc, \
            "committed FailureDigestListener.java is behind the converter template"


def test_merged_template_cells_come_out_in_a_stable_column_order():
    """34 CSVs differed between two converts of the SAME input: identical
    column set and values, different tpl_* column ORDER, because the
    varying-path set was iterated directly (per-process hash seed)."""
    trees = [
        {"contactInfo": {"name": "a", "address": {"city": "x", "country": "US", "postalCode": "1"}}, "reason": "r1"},
        {"contactInfo": {"name": "b", "address": {"city": "y", "country": "GB", "postalCode": "2"}}, "reason": "r2"},
    ]
    import json as _json
    _merged, cells = ra_converter._merge_bodies_with_placeholders(
        trees, [_json.dumps(t) for t in trees])
    cols = list(cells[0].keys())
    assert cols == sorted(cols), cols
    assert cols == list(cells[1].keys())


def test_method_name_drops_the_b2b_ticket():
    """`[A-Z]+[-_]?\d+` stopped at the first B of B2B, so every method
    came out `b2B2065Create...`. The ticket lives on @XrayTest, not here."""
    m, status, variant = ra_converter._business_method_name(
        "B2B-2065_create_and_activate_International_postalCode_200")
    assert m == "createAndActivateInternationalPostalCodeTest", m
    assert status == "200" and variant == ""
    m, _s, _v = ra_converter._business_method_name("B2B339_post_program_add_packages_200")
    assert m == "postProgramAddPackagesTest", m
    m, _s, _v = ra_converter._business_method_name("Cleanup_testdata_creation")
    assert m == "cleanupTestdataCreationTest", m


def test_every_ticket_spelling_is_stripped_from_method_names():
    """The 15 methods that kept a ticket after the first strip: a `-` after
    the number, and `[Bug-B2B-2299]` references at the end of the name."""
    cases = {
        "B2B_4183-get_programAccount_AccountID_inviteLink_200": "getProgramAccountAccountIDInviteLinkTest",
        "B2B-3234-Post_programaccount_emailDomain_400": "postProgramaccountEmailDomainTest",
        "B2B-5867-Passlocaledate_to_HWS_Modify 2": "passlocaledateToHWSModify2Test",
        "Reject_Pending_account_200[Bug-B2B-2299]": "rejectPendingAccountTest",
        "B2B-1877_post_activate_account_lowConfidenceCompanyMatch_empty[Bug-B2B-2012]":
            "postActivateAccountLowConfidenceCompanyMatchEmptyTest",
        "B2B-2065_create_and_activate_International_postalCode_200": "createAndActivateInternationalPostalCodeTest",
    }
    for name, want in cases.items():
        got, _s, _v = ra_converter._business_method_name(name)
        assert got == want, (name, got)
        assert "b2b" not in got.lower(), (name, got)


def test_class_name_is_the_business_stem_not_the_ticket():
    a = _case("B2B-5264_post_create_account_format_social_domain", [_rest_step()])
    b = _case("B2B-6251_LTA_create_LTA_account_with_email_domain_200", [_rest_step()])
    assigned = ra_converter._flow_class_assignment([a, b])
    names = {assigned[id(a)][1], assigned[id(b)][1]}
    assert len(names) == 2, names
    assert not any("B2B" in n for n in names), names   # deepened stems, no ticket
    assert all(n.endswith("Test") for n in names)


def test_identical_names_under_different_tickets_get_an_ordinal_never_the_ticket():
    a = _case("B2B-634_get_readmember_programaccounts_200", [_rest_step()])
    b = _case("B2B-793_get_readmember_programaccounts_200", [_rest_step()])
    c = _case("B2B-801_get_readmember_programaccounts_200", [_rest_step()])
    assigned = ra_converter._flow_class_assignment([a, b, c])
    names = [assigned[id(x)][1] for x in (a, b, c)]
    assert len(set(names)) == 3, names
    assert not any("B2B" in n for n in names), names
    # the leading verb token is dropped by the stem rules, as everywhere else
    assert names == ["ReadmemberProgramaccountsTest", "ReadmemberProgramaccounts2Test",
                     "ReadmemberProgramaccounts3Test"], names


def test_fs_path_lifts_the_windows_path_limit_for_clean(tmp_path):
    """--clean reported 217 deep files as "locked" and left them; one stale
    Support class then broke the compile once ticket prefixes left names."""
    import os as _os, shutil as _shutil
    bs = chr(92); ext = bs + bs + "?" + bs
    assert ra_converter._fs_path("a/b.txt") == "a/b.txt"          # short: untouched
    deep = _os.path.join(str(tmp_path), *(["d" * 40] * 7), "leaf.csv")
    assert len(_os.path.abspath(deep)) > 260
    if _os.name != "nt":
        assert ra_converter._fs_path(deep) == deep
        return
    assert ra_converter._fs_path(deep).startswith(ext)
    # create the deep tree through the prefix, then prove rmtree via _fs_path removes it
    _os.makedirs(ra_converter._fs_path(_os.path.dirname(deep)), exist_ok=True)
    with open(ra_converter._fs_path(deep), "w", encoding="utf-8") as fh:
        fh.write("x")
    top = _os.path.join(str(tmp_path), "d" * 40)
    # the root is SHORT: without force the walk fails on the deep children
    assert not ra_converter._fs_path(top).startswith(ext)
    assert ra_converter._fs_path(top, force=True).startswith(ext)
    _shutil.rmtree(ra_converter._fs_path(top, force=True))
    assert not _os.path.exists(ra_converter._fs_path(top, force=True))


def test_clean_sweeps_the_client_under_every_name_the_suite_has_used(tmp_path):
    """`--service-name ProgramAccounts` once, then a run without it, left
    ProgramaccountregressionClient.java AND ProgramAccountsClient.java --
    two identities of the same suite, every method twice."""
    import fluent_scenario as fs, json as _json, os as _os
    root = str(tmp_path)
    clients = _os.path.join(root, "src", "main", "java", "com", "ak", "api", "rest", "clients")
    _os.makedirs(clients)
    for n in ("ProgramaccountregressionClient", "ProgramAccountsClient", "OtherSuiteClient"):
        open(_os.path.join(clients, n + ".java"), "w").write("class X {}")
    orig = fs.catalog_path
    cat = _os.path.join(root, "fluent_catalog.json")
    fs.catalog_path = lambda *a, **k: cat
    try:
        c = fs._empty_catalog(); c["suites"] = {"programaccountregression": {"serviceName": "ProgramAccounts"}}
        _json.dump(c, open(cat, "w"))
        removed = ra_converter._clean_suite_output(root, "programaccountregression", "com.ak.api")
    finally:
        fs.catalog_path = orig
    left = sorted(_os.listdir(clients))
    assert left == ["OtherSuiteClient.java"], left            # another suite's client is untouched
    assert sum(1 for r in removed if r.endswith("Client.java")) == 2


def test_csv_cell_id_rewrite_matches_field_not_step_name():
    """A step named *guestid* must not turn a literal phone query value into
    an @Properties_qry_..._phoneNumber@ placeholder (B2B-5268 regex 400)."""
    import ra_converter as rc
    kept = rc._csv_cell("9012978932", "qry_get_guestid_bussinesses_verify_Checkforduplicates_false_phoneNumber")
    assert kept == "9012978932", kept
    live = rc._csv_cell("2000016128", "PropertiesDetails.accountID")
    assert live == "@Properties_accountID@", live
    # a qry_ column keeps its full name as the placeholder field (ctxGet's
    # trailing-field walk resolves accountId); the point is that it IS rewritten
    kept2 = rc._csv_cell("1900747836", "Properties.memberGuestID")
    assert kept2 == "1900747836", kept2          # a fixed pre-existing guest, the author's
    live2 = rc._csv_cell("2000016128", "qry_http_request_200_compare_name_account_country_accountId")
    assert live2.startswith("@Properties_") and live2.endswith("_accountId@"), live2


def test_request_level_headers_are_parsed():
    """ReadyAPI's WsdlRequest@request-headers setting carries content-language:
    zh-CN as an escaped fragment; Content-Type stays with the client."""
    import ra_converter as rc
    from xml.etree import ElementTree as ET
    xml = (
        '<con:restRequest xmlns:con="http://eviware.com/soapui/config" name="s">'
        '<con:settings>'
        '<con:setting id="com.eviware.soapui.impl.wsdl.WsdlRequest@request-headers">'
        '&lt;con:entry key="content-language" value="zh-CN" xmlns:con="http://eviware.com/soapui/config"/&gt;'
        '&lt;con:entry key="Content-Type" value="application/json" xmlns:con="http://eviware.com/soapui/config"/&gt;'
        '</con:setting>'
        '<con:setting id="other">x</con:setting>'
        '</con:settings></con:restRequest>')
    got = rc._request_level_headers(ET.fromstring(xml))
    assert got == {"content-language": "zh-CN"}, got
    assert rc._request_level_headers(None) == {}


def test_message_content_not_exists_renders_absent():
    """`not exists` is an absence check, not an equality against the saved
    value (71 elements across the suites)."""
    import ra_converter as rc
    src = open(rc.__file__, encoding="utf-8").read()
    assert "op_norm in _ABSENT_OPS" in src
    assert '"not exists"' in src and "jsonAbsent(softAssert, {response_var}" in src


def test_member_email_remap_is_one_user_only():
    """Two-enroll cases and two-user packs keep ${Properties#Email} on the
    member enroll; the one-user convention still gets the member slot."""
    import ra_converter as rc
    body = '{"username":"${Properties#usernamemember}","email":{"emailAddress":"${Properties#Email}"}}'

    class P:  # PropertiesStep stand-in
        def __init__(self, d): self.properties = d

    class R:  # RestStep stand-in
        def __init__(self, b): self.request_body = b; self.query_params = {}; self.headers = {}

    class Case:
        def __init__(self, steps): self.steps = steps

    one_user = Case([P({"Email": "a@x.com", "generatedemailAddress": "a@x.com"}), R(body)])
    assert "generatedemailAddress1" in rc._remap_member_enroll_email_placeholders("MemberHHonorsEnroll", body, one_user)
    two_user = Case([P({"Email": "m@x.com", "generatedemailAddress": "o@x.com"}), R(body)])
    assert rc._remap_member_enroll_email_placeholders("MemberHHonorsEnroll", body, two_user) == body
    second = Case([P({"Email": "a@x.com", "generatedemailAddress": "a@x.com"}), R(body),
                   R('{"emailAddress":"${Properties#generatedemailAddress1}"}')])
    assert rc._remap_member_enroll_email_placeholders("MemberHHonorsEnroll", body, second) == body
    assert rc._remap_member_enroll_email_placeholders("HHonorsEnroll", body, one_user) == body


def test_bootstrap_reuses_the_converters_own_client_scanner():
    """--bootstrap must not carry its own derivation of the interface.

    The first version derived signatures from the domain facades and the DSL
    and missed `TokenRefresh.client.tokenRequest(...)` -- a committed caller in
    a directory it never looked at -- so the bootstrapped clone failed to
    compile. `emit_imported_rest_client` already walks every file mentioning
    ImportedRestClient and learns how the tree reaches it.

    A second copy of a rule drifting from the original is the same failure that
    made the dedup report advertise template merges that must never happen.
    """
    import inspect
    import ra_converter as rc
    src = inspect.getsource(rc._run_bootstrap)
    assert "emit_imported_rest_client" in src, src
    assert "emit_framework_support" in src, src
    assert "_derive_baseline_client_methods" not in src, (
        "a parallel derivation is back in _run_bootstrap")


def test_bootstrap_writes_the_framework_with_no_xml(tmp_path):
    """The point of the flag: a tree with no conversions gets support types.

    Run as a subprocess so the real CLI wiring is exercised -- --input has to
    be optional, or the flag is unusable on the clone it exists for.
    """
    import subprocess as _sp
    out = str(tmp_path)
    proc = _sp.run([sys.executable,
                    os.path.join(HERE, "ra_converter.py"),
                    "--bootstrap", "--output", out,
                    "--package-root", "com.ak.api",
                    "--skip-self-test"],
                   capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    support = os.path.join(out, "src", "main", "java", "com", "ak", "api",
                           "support")
    for name in ("ImportedScenario.java", "TestThreadState.java",
                 "CtxFields.java", "ImportedTemplates.java",
                 "ImportedRestClient.java"):
        assert os.path.isfile(os.path.join(support, name)), name


def test_bootstrap_scaffolds_a_manual_client_and_never_clobbers_it(tmp_path):
    """--bootstrap leaves an editable client, and a re-run preserves edits.

    Unlike everything else bootstrap writes, this lands in COMMITTED space and
    the author fills in the endpoint bodies. Overwriting it on a later
    bootstrap or convert would silently discard their work, so the basename is
    in _AUTHOR_EDITABLE_BASENAMES (skip-if-exists).

    The scaffold exists to encode three contracts that fail confusingly when
    missed: the (String baseUrl) constructor SharedClients builds through, a
    name ending in Client for SuiteName, and implements ImportedRestClient.
    """
    import subprocess as _sp
    out = str(tmp_path)

    def boot():
        return _sp.run([sys.executable,
                        os.path.join(HERE, "ra_converter.py"),
                        "--bootstrap", "--output", out,
                        "--package-root", "com.ak.api", "--skip-self-test"],
                       capture_output=True, text=True)

    first = boot()
    assert first.returncode == 0, first.stdout + first.stderr
    stub = os.path.join(out, "src", "main", "java", "com", "ak", "api",
                        "rest", "manual", "client", "ManualClient.java")
    assert os.path.isfile(stub), "bootstrap did not scaffold ManualClient"
    body = open(stub, encoding="utf-8").read()
    assert "implements ImportedRestClient" in body, body[:400]
    assert "public ManualClient(String baseUrl)" in body, body[:400]

    marker = "// AUTHOR EDIT sentinel"
    with open(stub, "a", encoding="utf-8") as fh:
        fh.write("\n" + marker + "\n")
    second = boot()
    assert second.returncode == 0, second.stdout + second.stderr
    assert marker in open(stub, encoding="utf-8").read(), (
        "a second --bootstrap overwrote the author's ManualClient")


def test_facade_endpoint_map_resolves_aliases_and_flags_throwers(tmp_path):
    """The map exists so an author can see the endpoint behind a facade method.

    The facades are thin delegators with no javadoc, so `enrollHhonors` gives
    no hint that it reaches POST /realms/guests/enroll -- the verb and path
    live two hops away in the GENERATED client. It cannot be annotated on the
    facades themselves: those are committed and suite-agnostic, while the path
    (and whether the method resolves at all) belongs to one converted XML.

    Three behaviours are pinned: a thin delegator resolves, an alias resolves
    THROUGH the method it forwards to, and a client method this suite never
    implemented is called out -- those compile and autocomplete, then throw.
    """
    import ra_converter as rc
    root = str(tmp_path)
    base = os.path.join(root, "src", "main", "java", "com", "ak", "api")
    os.makedirs(os.path.join(base, "rest", "clients"))
    os.makedirs(os.path.join(base, "domain", "guest"))
    with open(os.path.join(base, "rest", "clients", "MysuiteClient.java"),
              "w", encoding="utf-8") as fh:
        fh.write("public class MysuiteClient {\n"
                 "    /**\n     * POST /realms/guests/enroll\n     */\n"
                 "    public Response hHonorsEnroll(String token, String body) { }\n"
                 "}\n")
    with open(os.path.join(base, "domain", "guest", "GuestApi.java"),
              "w", encoding="utf-8") as fh:
        fh.write("public final class GuestApi {\n"
                 "    public Response hHonorsEnroll(String token, String body) {\n"
                 "        return client.hHonorsEnroll(token, body);\n    }\n"
                 "    public Response enrollHhonors(String token, String body) {\n"
                 "        return hHonorsEnroll(token, body);\n    }\n"
                 "    public Response readAllEmails(String token) {\n"
                 "        return client.readAllEmails(token);\n    }\n"
                 "}\n")
    line = rc._write_facade_endpoint_map(root, "com.ak.api", "mysuite")
    assert line, "no map written"
    body = open(os.path.join(root, "_audit", "mysuite", "facade_endpoints.md"),
                encoding="utf-8").read()
    assert "`hHonorsEnroll` | POST /realms/guests/enroll" in body, body
    assert "`enrollHhonors` | POST /realms/guests/enroll" in body, body
    assert "via hHonorsEnroll" in body, body
    assert "`readAllEmails` | NOT IMPLEMENTED" in body, body
    assert "not implemented: 1" in body, body


def test_generated_token_key_is_bearer_prefixed_on_every_lookup_path():
    """A GeneratedTokenID lookup is Bearer-prefixed however it resolved.

    hiltonTokenFallback prefixed it, but it is the LAST resort in the
    resolver: with `accessToken` in ctx, resolveDeclared matched first and
    handed back the bare JWT, so the Authorization header went out
    unprefixed while SetupHelper writes the same key as "Bearer " + token.
    FrameworkPartialGapsTest asserted this and had never run -- it lives
    only in testng.xml, which could not start.

    Guards BOTH copies: the bundled framework ImportedScenario and the
    TestSupport template, because generated code calls the latter.
    """
    here = os.path.dirname(__file__)
    py = open(os.path.join(here, "ra_converter.py"), encoding="utf8").read()
    java = open(os.path.join(here, "framework", "ImportedScenario.java"),
                encoding="utf8").read()
    for name, src in (("TestSupport template", py),
                      ("framework/ImportedScenario.java", java)):
        assert "bearerForGeneratedToken" in src, name
        # ctxGetRaw must route through it, not resolve directly.
        assert ("return bearerForGeneratedToken(primaryKey, "
                "ctxGetResolved(ctx, primaryKey));") in src, name
        # Additive only: adds a prefix, never strips one, and only for a
        # key naming a generated token -- a Salesforce `*.accessToken` key
        # must keep resolving to the same value it did before.
        assert 'contains("generatedtoken")' in src, name
        assert 'regionMatches(true, 0, "Bearer ", 0, 7)' in src, name
        assert "Bearer " + '" + value' in src, name


def test_case_with_every_step_disabled_skips_instead_of_passing_empty():
    """A case whose business steps are all disabled must not emit a green test.

    B2B-7576 had 12 of its 15 steps disabled in the ReadyAPI source. The
    three that remained are the auth preamble, which the converter folds
    into shared setup -- so the chain rendered as
    `start(row, id).complete();`: a @Test that issued no request, asserted
    nothing, and PASSED. A green run implied the case had been exercised.
    """
    src = open(os.path.join(os.path.dirname(__file__), "ra_converter.py"),
               encoding="utf8").read()
    # the empty-chain branch exists and precedes the normal emit
    guard = src.index("fully_disabled = not chain.strip() and not verify_calls")
    throw = src.index("throw new org.testng.SkipException(", guard)
    normal = src.index("elif verify_calls:", guard)
    assert guard < throw < normal
    # nothing unreachable is appended after the throw
    tail = src.index("if not fully_disabled:", guard)
    assert throw < tail
    assert "softAssert.assertAll();" in src[tail:tail + 400]
    # and the audit names it
    assert 'add_runtime_skip(' in src[guard:normal]
    assert '"case-fully-disabled"' in src
    assert src.count('"case-fully-disabled"') >= 2, (
        "needs both the finding and its MEANINGS entry")


# basename -> (expected ra_converter-framework-rev, sha256[:16] of the file
# with the rev line masked). The hash ignores the rev line itself, so bumping
# the rev alone does not change the fingerprint of the code it guards.
_FRAMEWORK_REVS = {
    "CtxFields.java": (10, "d1ca472292ddbb67"),
    "IdentityVocabulary.java": (1, "5f31f59ad2c688aa"),
    "ImportedScenario.java": (23, "81773778929c7a45"),
    "ImportedTemplates.java": (2, "ebfd1453c10b5676"),
    "ImportedTestdataCleanup.java": (3, "8d404ea000343756"),
    "TestThreadState.java": (2, "fa194ad25091bd71"),
}


def test_editing_a_bundled_framework_file_requires_bumping_its_rev():
    """Change the code, bump the rev -- or run machines keep the old copy.

    _staleness_reason checks the revision marker FIRST and returns as soon
    as it finds one; the missing-declaration check is only reached for files
    that have no marker. So for these six, an unbumped rev means the
    converter SKIPS the file on any tree that already has it, and the run
    machine silently keeps executing the previous version.

    That is not hypothetical: the ctxGet Bearer fix shipped without a bump.
    ImportedScenario stayed at rev 22 on disk and at rev 22 in the bundle,
    so every existing tree would have kept the unprefixed-token code while
    the commit claimed to fix it.
    """
    import hashlib
    import re as _re
    here = os.path.join(os.path.dirname(__file__), "framework")
    rx = _re.compile(r"ra_converter-framework-rev:\s*(\d+)")
    seen = set()
    for fn in sorted(os.listdir(here)):
        if not fn.endswith(".java"):
            continue
        seen.add(fn)
        assert fn in _FRAMEWORK_REVS, (
            "%s is bundled but not in _FRAMEWORK_REVS -- add it with its rev "
            "and hash so an edit cannot ship without a bump" % fn)
        text = open(os.path.join(here, fn), encoding="utf-8").read()
        m = rx.search(text)
        assert m, "%s has no ra_converter-framework-rev marker" % fn
        want_rev, want_hash = _FRAMEWORK_REVS[fn]
        got = hashlib.sha256(
            rx.sub("ra_converter-framework-rev: X", text).encode("utf-8")
        ).hexdigest()[:16]
        if got != want_hash:
            raise AssertionError(
                "%s changed (hash %s != %s). Bump "
                "`ra_converter-framework-rev` in the file AND update "
                "_FRAMEWORK_REVS here, or existing trees will keep the old "
                "copy: _staleness_reason short-circuits on the rev marker."
                % (fn, got, want_hash))
        assert int(m.group(1)) == want_rev, (
            "%s rev is %s, expected %s" % (fn, m.group(1), want_rev))
    assert seen == set(_FRAMEWORK_REVS), (
        "_FRAMEWORK_REVS lists files that no longer exist: %s"
        % (set(_FRAMEWORK_REVS) - seen))


def test_bootstrap_creates_the_dirs_a_hand_written_test_needs():
    """None of templates/manual, csv/ or openapi/ can arrive with a clone.

    src/test/resources/csv/ is gitignored outright, and templates/manual/ is
    committed but git does not store empty directories -- so a fresh tree has
    neither, and the first manual test dies on a classpath miss whose message
    is about a template rather than about a missing folder.

    src/main/resources/openapi/ is here for a different reason: nothing fails
    without it, since codegen is off by default. It is created so "where does
    the Swagger file go" is answerable by LOOKING at the tree. Its README is
    the one file in that folder git tracks -- without it the directory is
    invisible on a clone, which is the state that prompted the question.

    Also asserts the README is skip-if-exists: it carries the rule that bodies
    are published while CSV rows are not, and an author who edits it must not
    lose that on the next bootstrap.
    """
    import tempfile
    import ra_converter as rc
    with tempfile.TemporaryDirectory() as out:
        args = SimpleNamespace(output=out, package_root="com.ak.api")
        made = rc._bootstrap_author_dirs(None, args)
        tpl = os.path.join(out, "src/test/resources/templates/manual")
        csvd = os.path.join(out, "src/test/resources/csv")
        specd = os.path.join(out, "src/main/resources/openapi")
        readme = os.path.join(tpl, "README.md")
        spec_readme = os.path.join(specd, "README.md")
        assert os.path.isdir(tpl), made
        assert os.path.isdir(csvd), made
        assert os.path.isdir(specd), made
        assert os.path.isfile(readme), made
        assert os.path.isfile(spec_readme), made
        spec_body = open(spec_readme, encoding="utf-8").read()
        # the three facts someone needs before their spec does anything
        assert "src/main/resources/openapi/" in spec_body
        assert "-Dopenapi.spec=" in spec_body, "must show how a new VERSION is set"
        assert "-Dopenapi.codegen.skip=false" in spec_body, "generation is off by default"
        body = open(readme, encoding="utf-8").read()
        # the two things an author gets wrong first
        assert "templates/manual/" in body
        assert "gitignored" in body and "#enroll_password#" in body, body
        # ofPath takes the CLASSPATH path, without the src/test/resources prefix
        assert 'Template.ofPath("b2b722Enroll", "templates/manual/' in body

        # second run: idempotent, and an edited README survives
        open(readme, "a", encoding="utf-8").write("\nAUTHOR EDIT\n")
        open(spec_readme, "a", encoding="utf-8").write("\nAUTHOR EDIT\n")
        again = rc._bootstrap_author_dirs(None, args)
        assert "AUTHOR EDIT" in open(readme, encoding="utf-8").read()
        assert "AUTHOR EDIT" in open(spec_readme, encoding="utf-8").read()
        assert not [p for p in again if p.endswith("README.md")], again


def _prune_mod():
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "prune_dead_props.py")
    spec = importlib.util.spec_from_file_location("prune_dead_props", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prune_keeps_any_column_something_can_reach():
    """A column survives unless NOTHING references it, in any spelling.

    The dangerous mistake is matching only the exact column name.
    ImportedScenario.ctxGet falls back to a trailing-field walk, so
    `Properties.AccountID` can answer a lookup for
    `PropertiesDetails.accountID` without ever being named in full. Deleting
    it would take a value the suite depends on.
    """
    p = _prune_mod()
    blob = (
        'template has #Properties_Email#\n'
        'spec has Ref.ctx("Properties.guestID")\n'
        'java has "Properties_websiteDomain"\n'
        'alias walk sees "PropertiesDetails.accountID"\n'
        'at-form @Properties_totpCode@\n'
    )
    for col in ("Properties.Email", "Properties.guestID",
                "Properties.websiteDomain", "Properties.totpCode"):
        assert p.is_referenced(col, blob), col
    # reachable only through the trailing-field walk
    assert p.is_referenced("Properties.accountID", blob), "trailing-field miss"
    # genuinely unreachable
    assert p.is_referenced("Properties.Pair_47_RatePlanCode", blob) is None


def test_prune_never_touches_framework_columns():
    """Only the ReadyAPI properties bag is in scope."""
    p = _prune_mod()
    for keep in ("test_case_id", "_stop_after", "expected_enrollGuest_status_code",
                 "expected_readProgramAccount_jsonpath_attestationSummary_status",
                 "template_readProgramAccount", "groups", "jira_xray_id"):
        assert not p.prunable(keep), keep
    for drop in ("Properties.Pair_2_RatePlanCode", "Properties.guestID_3",
                 "PropertiesGuestId_guestID"):
        assert p.prunable(drop), drop


def test_prune_is_wired_post_emit_and_can_be_turned_off():
    src = open(os.path.join(os.path.dirname(__file__), "ra_converter.py"),
               encoding="utf8").read()
    assert '"--keep-dead-props"' in src, "opt-out flag must exist"
    assert "_run_prune_dead_props(args)" in src, "must run after emit"
    # after the dataflow check, so that guard sees the tree exactly as emitted
    assert (src.index("_run_post_emit_dataflow_check(args)\n")
            < src.index("_run_prune_dead_props(args)\n"))
    # never on a bootstrap (no XML parsed, so no CSVs of its own)
    at = src.index("_run_prune_dead_props(args)\n")
    assert 'getattr(args, "bootstrap", False)' in src[at - 400:at]


def test_generated_chain_exposes_the_response_accessors():
    """A converted test must be able to read what the server said.

    The generated chain returns S from every phase and keeps each response in
    a PROTECTED `<step>Res` field, so before this the only way to a body from
    a test class was to step into RestStep in a debugger. These three
    accessors sit on ScenarioSteps, which every suite's Steps class extends,
    so they are reachable mid-chain in every converted test -- and they read
    the same per-thread LastExchange record the manual chain and the legacy
    tests read, so there is one answer rather than three.

    Guarded here because the emitter is one f-string: a careless edit to the
    template silently drops them from every future convert.
    """
    src = open(os.path.join(os.path.dirname(__file__), "ra_converter.py"),
               encoding="utf-8").read()
    head = src.index("public abstract class ScenarioSteps<S extends ScenarioSteps<S>>")
    body = src[head:src.index('rel = f"src/main/java/', head)]
    for sig in ("public final Response lastResponse()",
                "public final String lastStep()",
                "public final Response responseOf(String stepName)"):
        assert sig in body, "ScenarioSteps template lost: " + sig
    # Delegation, not a private copy: a second source of truth would let the
    # chain and LastExchange disagree about the same call.
    assert body.count("com.ak.api.rest.utilities.LastExchange.") == 3, (
        "the accessors must delegate to LastExchange, all three of them")
    # public, so a @Test method can call them -- protected would compile here
    # and fail in the test class, which is where it matters.
    assert "protected final Response lastResponse()" not in body


def test_openapi_spec_file_name_is_one_key_for_build_and_runtime():
    """The spec file name carries the API VERSION, so it changes often.

    Two readers need it -- the generator's <inputSpec> at build time, and
    OpenApiModels' classpath lookup at run time. Hard-coded in both, a new
    version is two edits, and doing only one of them generates models from
    one spec while validating bodies against another: a mismatch that
    surfaces nowhere near its cause.

    So both take it from `openapi.spec`, and -Dopenapi.spec=abc.yaml moves
    them together.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    pom = open(os.path.join(root, "pom.xml"), encoding="utf-8").read()
    assert "<openapi.spec>" in pom, "pom must declare the property"
    assert "openapi/${openapi.spec}</inputSpec>" in pom, (
        "inputSpec must READ the property, not repeat the file name")

    java = open(os.path.join(root, "src/main/java/com/ak/api/openapi",
                             "OpenApiModels.java"), encoding="utf-8").read()
    assert '"openapi.spec"' in java, (
        "the runtime lookup must read the same key the pom uses")
    assert '"openapi/ProgramAccounts-1.0.71.yaml"' not in java, (
        "the full resource path must no longer be hard-coded")


def test_openapi_readme_travels_but_no_spec_does():
    """The folder must be visible on a clone; the specs must not be.

    Those pull opposite ways. Ignoring `openapi/` outright hides the whole
    directory -- git stores no empty directories -- which is what made "where
    does the Swagger file go" unanswerable by looking at the tree. But a spec
    is a vendor API contract (endpoint paths, schemas, examples) and this repo
    is public, so no spec may ever be committed.

    The resolution is `openapi/*` plus a negation for the README: git never
    descends into an EXCLUDED DIRECTORY, so the bare `openapi/` form would
    make the negation dead and silently drop the README. Both halves are
    asserted here because either one alone is a bug -- one hides the folder,
    the other publishes a vendor spec.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    lines = [ln.strip() for ln in
             open(os.path.join(root, ".gitignore"), encoding="utf-8")]
    assert "src/main/resources/openapi/*" in lines, (
        "must ignore the CONTENTS; the bare directory form makes the "
        "negation below dead")
    assert "src/main/resources/openapi/" not in lines, (
        "the bare directory form excludes the directory itself, so git never "
        "descends into it and the README negation cannot re-include anything")
    assert "!src/main/resources/openapi/README.md" in lines, (
        "without the README the folder is invisible on a clone")
    # Nothing may re-include an actual spec.
    bad = [ln for ln in lines
           if ln.startswith("!src/main/resources/openapi/")
           and not ln.endswith("/README.md")]
    assert not bad, "only the README may be re-included, not: %s" % bad


def test_script_runner_is_the_last_thing_in_this_file():
    """verify_all runs this file as a SCRIPT, not under pytest.

    The runner collects `globals()` when it executes, so a test appended
    after the `if __name__ == "__main__"` block is defined too late and
    never runs -- 47 tests sat below it, all reported as passing by
    `[PASS] contracts`, some for months. pytest found them; the gate did
    not. Keep the block last, and keep appending ABOVE this test.
    """
    import io as _io
    import os as _os
    src = _io.open(_os.path.abspath(__file__), encoding="utf-8").read()
    main_at = src.index('if __name__ == "__main__":')
    after = src[main_at:]
    stray = [ln for ln in after.splitlines() if ln.startswith("def test_")]
    assert not stray, "tests defined AFTER the script runner never execute: " + ", ".join(stray)


if __name__ == "__main__":
    import inspect as _inspect
    import pathlib as _pathlib
    import tempfile as _tempfile
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            # Minimal fixture support: pytest's tmp_path is the only fixture
            # these tests use. A test that needs one used to crash the
            # script runner with a TypeError -- when it was reached at all.
            params = _inspect.signature(fn).parameters
            if "tmp_path" in params:
                with _tempfile.TemporaryDirectory() as _d:
                    fn(_pathlib.Path(_d))
            else:
                fn()
            print(f"ok  {fn.__name__}")
        except Exception as ex:
            failed += 1
            print(f"FAIL {fn.__name__}: {ex}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)} passed")
