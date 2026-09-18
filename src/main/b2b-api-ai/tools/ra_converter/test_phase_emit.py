"""phase_emit guards: a rendered body splits into spec + hook exactly, Refs
parse from the renderer's expressions, the repeat rule shapes the chain,
and the generated Java shells have the shape the runtime expects.

Runs as a script (verify_all) or under pytest.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import phase_emit as pe  # noqa: E402
import phase_model as pm  # noqa: E402
from test_dedup_report import FIXTURE  # noqa: E402

V2S = {"http_request_200_enroll_guestRes": "http_request_200_enroll_guest"}


def _body(method: str) -> list[str]:
    return FIXTURE.split(f"public void {method}() throws Exception {{")[1].split("\n    }")[0].splitlines()


def test_verify9_splits_into_four_checks_and_nothing_else():
    sp = pe.split_rest_body(_body("runVerifyVerify9"), "https_Get_verify_200Res", "https_Get_verify_200")
    assert sp.chain_seen
    assert sp.checks == [("equals", "[0].confidence", "medium"), ("exists", "[1].inviteKey", ""),
                         ("absent", "[0].inviteKey", ""), ("equals", "[0].status", "limited")]
    assert sp.extracts == [] and sp.leftover == []


def test_lines_outside_the_closed_set_go_to_the_hook():
    lines = _body("runVerifyVerify9") + [
        "        // [groovy] DataGenInput -- auto-translated",
        "        String domain = Config.get(\"ALLOWED_DOMAINS\", \"\");",
        "        softAssert.assertNotNull(RestUtilities.safeJsonExtract(https_Get_verify_200Res, \"[0].accountId\"));",
    ]
    sp = pe.split_rest_body(lines, "https_Get_verify_200Res", "https_Get_verify_200")
    assert len(sp.checks) == 4
    assert [l.strip()[:20] for l in sp.leftover] == ["// [groovy] DataGenI", "String domain = Conf", "softAssert.assertNot"]
    assert pe.hook_blockers(sp.leftover) is None
    assert pe.hook_blockers(["if (!__stopAfter.isEmpty()) { return this; }"]) == "prefix-merged stop marker"


def test_a_transfer_extract_on_the_current_response_becomes_a_spec_extract():
    lines = _body("runVerifyVerify8") + [
        '        TestSupport.putExtracted(ctx, "Properties.guestID", com.ak.api.rest.utilities.RestUtilities.safeJsonExtract(https_Get_verify_200Res, "guestId"));',
        '        TestSupport.putExtracted(ctx, "Other", com.ak.api.rest.utilities.RestUtilities.safeJsonExtract(otherRes, "x"));',
    ]
    sp = pe.split_rest_body(lines, "https_Get_verify_200Res", "https_Get_verify_200")
    assert sp.extracts == [("Properties.guestID", "guestId", "json")]
    assert len(sp.leftover) == 1 and "otherRes" in sp.leftover[0]      # another step's response: hook


def test_refs_parse_from_the_renderers_expressions():
    assert pe.parse_ref('TestSupport.ctxGet(ctx, "PropertiesDetails.accountID")', {}) == ("ctx", "PropertiesDetails.accountID")
    assert pe.parse_ref('ctx.getOrDefault("tokenId.GeneratedTokenID", "")', {}) == ("ctx", "tokenId.GeneratedTokenID")
    assert pe.parse_ref('com.ak.api.rest.utilities.RestUtilities.safeJsonExtract(http_request_200_enroll_guestRes, "guestId")', V2S) \
        == ("resp", "http_request_200_enroll_guest", "guestId")
    assert pe.parse_ref('row.getOrDefault("path_x_id", "8888888888")', {}) == ("row", "path_x_id", "8888888888")
    assert pe.parse_ref('""', {}) == ("lit", "")
    kind, java = pe.parse_ref('"Basic " + java.util.Base64.getEncoder().encodeToString("u:p".getBytes())', {})
    assert kind == "expr"
    j = pe.ref_java(("expr", 'String.valueOf(http_request_200_enroll_guestRes.jsonPath().get("a"))'), V2S)
    assert 'c.response("http_request_200_enroll_guest")' in j and j.startswith("Ref.expr(")


def test_chain_follows_the_repeat_rule_and_forces_taken_names():
    assert pe.chain_calls([("enrollGuest", "a"), ("readProgramAccount", "b")]) == [".enrollGuest()", ".readProgramAccount()"]
    assert pe.chain_calls([("readProgramAccount", "a"), ("readProgramAccount", "b")]) == \
        ['.readProgramAccount("a")', '.readProgramAccount("b")']
    assert pe.chain_calls([("readProgramAccount", "a")], force_step={"readProgramAccount"}) == ['.readProgramAccount("a")']


def test_vocab_methods_skip_the_no_arg_form_for_taken_names():
    j = pe.vocab_methods_java(["enrollGuest", "readProgramAccount"], taken={"readProgramAccount"})
    assert "public S enrollGuest() throws Exception" in j
    assert "public S enrollGuest(String step)" in j
    assert "public S readProgramAccount() throws Exception" not in j
    assert "public S readProgramAccount(String step)" in j


def test_spec_builder_and_calls_agree_on_the_engine_id():
    spec = pm.PhaseSpec(
        suite="s", case="c", step_name="get_account", sid="get_account", verb="GET",
        path="/businesses/{accountId}", client_method="readProgramAccount", receiver="client",
        template_expr=None, regen=False, expected_status=200, query=(("include", "#col#"),),
        path_args=('TestSupport.ctxGet(ctx, "Properties.accountId")',),
        token_expr='ctx.getOrDefault("tokenId.GeneratedTokenID", "")',
        engine_id=pe.engine_id("readProgramAccount", 1, True, False, False),
        path_refs=(("ctx", "Properties.accountId"),), token_ref=("ctx", "tokenId.GeneratedTokenID"))
    sp = pe.Split(checks=[("exists", "accountId", "")])
    j = pe.spec_builder_java(spec, sp, None, {}, "XPhases::hook1_get_account", indent=0)
    assert j.startswith('PhaseSpec.phase("get_account")')
    assert '.engine("readProgramAccount/1q")' in j and '.get("/businesses/{accountId}")' in j
    assert '.args(Ref.ctx("Properties.accountId"))' in j and '.query("include", "#col#")' in j
    assert '.exists("accountId")' in j and '.after(XPhases::hook1_get_account)' in j and j.endswith(".build()")
    calls = pe.calls_java("com.x.support.s", [("readProgramAccount", 1, True, False, False)])
    assert 'case "readProgramAccount/1q":' in calls
    assert "c.client.readProgramAccount(p.token.resolve(c), p.arg(0).resolve(c), q)" in calls


def test_hook_prelude_declares_only_the_responses_the_lines_use():
    h = pe.hook_java("hook1_x", ['softAssert.assertNotNull(RestUtilities.safeJsonExtract(xRes, "a"));',
                                'String s = RestUtilities.safeJsonExtract(http_request_200_enroll_guestRes, "guestId");'],
                     "xRes", V2S)
    assert "static void hook1_x(io.restassured.response.Response res, PhaseContext c)" in h
    assert "Response xRes = res;" in h
    assert 'Response http_request_200_enroll_guestRes = c.response("http_request_200_enroll_guest");' in h
    assert "__domainApis" in h and " domain " not in h     # no local named `domain`: translated Groovy declares its own


def test_reserved_vocabulary_names_get_a_suffix():
    j = pe.vocab_methods_java(["start", "enrollGuest"])
    assert "public S startPhase()" in j and "public S start()" not in j
    assert pe.chain_calls([("start", "s"), ("enrollGuest", "e")]) == [".startPhase()", ".enrollGuest()"]


def test_extracts_keep_the_last_write_in_the_old_order():
    spec = pm.PhaseSpec(
        suite="s", case="c", step_name="x", sid="x", verb="GET", path="/a", client_method="m",
        receiver="client", template_expr=None, regen=False, expected_status=200,
        extracts=(("Properties.guestID", "json", "guestId"), ("K", "json", "first")),
        engine_id="m/0", path_refs=(), token_ref=("ctx", "t"))
    sp = pe.Split(extracts=[("K", "second", "json")])          # a transfer step after the call
    j = pe.spec_builder_java(spec, sp, None, {}, None, indent=0)
    assert '.extract("K", "second")' in j and '.extract("K", "first")' not in j
    assert j.index('.extract("Properties.guestID", "guestId")') < j.index('.extract("K", "second")')


def test_phases_class_registers_each_case_once():
    j = pe.phases_class_java("com.x.cases", "FooTestPhases", ["java.util.Map"], [
        {"case": "B2B-1_a", "entries": [
            {"vocab": "enrollGuest", "step": "HHonorsEnroll", "verify": False, "spec_java": 'PhaseSpec.phase("HHonorsEnroll").build()'},
            {"vocab": "verifyAccountid", "step": "v", "verify": True, "spec_java": 'PhaseSpec.phase("v").build()'}]},
    ], [])
    assert 'CaseRegistry.register("B2B-1_a")' in j
    assert '.phase("enrollGuest", "HHonorsEnroll", FooTestPhases::spec1)' in j
    assert '.verify("verifyAccountid", "v", FooTestPhases::spec2);' in j
    assert "private static PhaseSpec spec1()" in j and "public static synchronized void register()" in j


def test_cluster_members_register_through_one_loop_and_share_factories():
    same = [{"vocab": "enrollGuest", "step": "HHonorsEnroll", "verify": False,
             "spec_java": 'PhaseSpec.phase("HHonorsEnroll").build()'}]
    j = pe.phases_class_java("com.x.cases", "FooTestPhases", [], [
        {"case": "B2B-1_a", "entries": same}, {"case": "B2B-1_b", "entries": same},
        {"case": "B2B-2_c", "entries": [dict(same[0], step="Other")]},
    ], [])
    assert 'for (String id : new String[] {"B2B-1_a", "B2B-1_b"}) {' in j
    assert 'CaseRegistry.register("B2B-2_c")' in j
    assert j.count("private static PhaseSpec spec") == 1, "one builder text -> one factory"
    assert j.count("FooTestPhases::spec1") == 2


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as ex:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(ex).__name__}: {ex}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)} passed")
