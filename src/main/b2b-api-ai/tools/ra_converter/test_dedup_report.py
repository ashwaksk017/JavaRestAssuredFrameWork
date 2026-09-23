"""dedup_report guards: the two verify methods that motivated it are found
as one group, what varies is named, compound methods are left alone, and
the audit hook writes into _audit/<suite>/.

Runs as a script (verify_all) or under pytest.
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import dedup_report as dr  # noqa: E402

# The real pair from Insights (verify runners), trimmed to what the parser reads.
FIXTURE = '''package x;

public final class Steps {

    public void runVerifyVerify8() throws Exception {
        // ==== REST step: https_Get_verify_200  (GET /guests/{guestId}/businesses/verify) ====
        this.https_Get_verify_200Res = RestStep.exec(ctx, row, softAssert, holder, testCaseId)
                .name("https_Get_verify_200")
                .regenIdentity()
                .query("country", "#Properties_country#")
                .query("emailDomain", "#qry_https_Get_verify_200_emailDomain#")
                .query("website", "#Properties_websiteDomain#")
                .expectedStatus(200)
                .get("/guests/{guestId}/businesses/verify".replace("{guestId}", x),
                    (body, q, h) -> client.verifyProgramAccount(t, g, q));
        TestSupport.putExtracted(ctx, "https_Get_verify_200_RawRequest", RestStep.lastResolvedBody());
        ResponseAsserts.jsonExists(softAssert, https_Get_verify_200Res, row, "https_Get_verify_200", "[0].accountId");

    }

    public void runVerifyVerify9() throws Exception {
        // ==== REST step: https_Get_verify_200  (GET /guests/{guestId}/businesses/verify) ====
        this.https_Get_verify_200Res = RestStep.exec(ctx, row, softAssert, holder, testCaseId)
                .name("https_Get_verify_200")
                .regenIdentity()
                .query("country", "#Properties_country#")
                .query("website", "#Properties_websiteDomain#")
                .query("emailDomain", "#Properties_Domain#")
                .expectedStatus(200)
                .get("/guests/{guestId}/businesses/verify".replace("{guestId}", x),
                    (body, q, h) -> client.verifyProgramAccount(t, g, q));
        TestSupport.putExtracted(ctx, "https_Get_verify_200_RawRequest", RestStep.lastResolvedBody());
        ResponseAsserts.jsonEquals(softAssert, https_Get_verify_200Res, ctx, row, "https_Get_verify_200", "[0].confidence", "medium");
        ResponseAsserts.jsonExists(softAssert, https_Get_verify_200Res, row, "https_Get_verify_200", "[1].inviteKey");
        ResponseAsserts.jsonAbsent(softAssert, https_Get_verify_200Res, "[0].inviteKey");
        ResponseAsserts.jsonEquals(softAssert, https_Get_verify_200Res, ctx, row, "https_Get_verify_200", "[0].status", "limited");

    }

    public S readProgramAccount7() throws Exception {
        // ==== REST step: get_account  (GET /businesses/{accountId}) ====
        this.get_accountRes = RestStep.exec(ctx, row, softAssert, holder, testCaseId)
                .name("get_account")
                .expectedStatus(404)
                .get("/businesses/{accountId}".replace("{accountId}", y),
                    (body, q, h) -> client.readProgramAccount(t, a, q));
        return self();
    }

    public S prepareSalesforceAccount3() throws Exception {
        // ==== REST step: sf_token  (POST /services/oauth2/token) ====
        Response a = RestStep.exec(ctx, row, softAssert, holder, testCaseId).name("sf_token").expectedStatus(200).post("/services/oauth2/token", (b, q, h) -> client.sfToken(q, b));
        // ==== REST step: sf_account  (GET /services/data/v58.0/sobjects/Account/{id}) ====
        Response b = RestStep.exec(ctx, row, softAssert, holder, testCaseId).name("sf_account").expectedStatus(200).get("/services/data/v58.0/sobjects/Account/{id}", (bb, q, h) -> client.sfAccount(t, i));
        return self();
    }
}
'''


def test_the_two_verify_methods_are_one_group_and_the_differences_are_named():
    recs = dr.parse_methods(FIXTURE, "Steps.java")
    groups = dr.group_by_shape(recs, 2)
    assert len(groups) == 1, [g["shape"] for g in groups]
    (g,) = groups
    assert sorted(m["method"] for m in g["members"]) == ["runVerifyVerify8", "runVerifyVerify9"]
    verb, path, qk, hk, body = g["shape"]
    assert (verb, path, body) == ("GET", "/guests/{}/businesses/verify", False)
    assert qk == ("country", "emailDomain", "website")
    # what a phase spec must carry for these two:
    assert g["varies"]["query emailDomain"] == ["#Properties_Domain#", "#qry_https_Get_verify_200_emailDomain#"]
    assert g["varies"]["assertions"] == ["1 check(s)", "4 check(s)"]
    assert "expected status" not in g["varies"]      # both 200
    assert "query country" not in g["varies"]        # same source in both


def test_assertions_are_read_as_kind_path_expected():
    recs = {r["method"]: r for r in dr.parse_methods(FIXTURE, "Steps.java")}
    a9 = dict(((k, p), e) for k, p, e in recs["runVerifyVerify9"]["asserts"])
    assert a9[("jsonEquals", "[0].confidence")] == "medium"
    assert a9[("jsonEquals", "[0].status")] == "limited"
    assert ("jsonExists", "[1].inviteKey") in a9
    assert ("jsonAbsent", "[0].inviteKey") in a9
    assert recs["runVerifyVerify9"]["extracts"] == ()     # RawRequest bookkeeping is not an extract


def test_single_member_shapes_and_compound_methods_are_not_groups():
    recs = dr.parse_methods(FIXTURE, "Steps.java")
    by = {r["method"]: r for r in recs}
    assert by["readProgramAccount7"]["rest_steps"] == 1
    assert by["prepareSalesforceAccount3"]["rest_steps"] == 2 and "shape" not in by["prepareSalesforceAccount3"]
    groups = dr.group_by_shape(recs, 2)
    assert all("readProgramAccount7" not in [m["method"] for m in g["members"]] for g in groups)


def test_report_text_names_the_collapse():
    recs = dr.parse_methods(FIXTURE, "Steps.java")
    text = dr.render(recs, dr.group_by_shape(recs, 2))
    assert "single-call methods: 3 -> distinct call shapes: 2" in text
    assert "collapsible: 2 methods in 1 groups" in text
    assert "GET /guests/{}/businesses/verify?country,emailDomain,website" in text
    assert "varies in query emailDomain" in text
    assert "runVerifyVerify8, runVerifyVerify9" in text


ENTRY_A = '''package x;
public final class OnboardingFlowAGuestidmember2 extends ProgramAccountsSteps<OnboardingFlowAGuestidmember2> {
    public static OnboardingFlowAGuestidmember2 start(Map<String, String> row) throws Exception { return null; }
    @Override
    protected OnboardingFlowAGuestidmember2 bootstrap() throws Exception {
        ImportedScenario.runSetup("flow_A", client, ctx, row, softAssert, holder, testCaseId);
        {
            CtxFields.generateStandard(ctx, "Properties", "guestIDmember", "generatedemailAddress1", "generatedemailAddress2");
        }
        CtxFields.seedFromRow(ctx, row, "Properties.");
        return self();
    }
}
'''
ENTRY_B = ENTRY_A.replace("OnboardingFlowAGuestidmember2", "OnboardingFlowAGuestidmember3").replace(
    '"generatedemailAddress2");',
    '"partnerProgramAccountNumber");\n            ImportedScenario.putExtracted(ctx, "Properties.role", com.ak.api.data.FakeData.oneOf("admin", "employee", "owner"));')
ENTRY_C = ENTRY_A.replace("OnboardingFlowAGuestidmember2", "OnboardingFlowB").replace('"flow_A"', '"flow_B"')


def test_entry_classes_that_differ_only_in_their_property_pack_are_one_group():
    a, b, c = (dr.parse_entry(src, "e.java") for src in (ENTRY_A, ENTRY_B, ENTRY_C))
    assert a and b and c
    assert a["flow"] == "flow_A" and c["flow"] == "flow_B"
    assert a["packs"] == (("Properties", ("guestIDmember", "generatedemailAddress1", "generatedemailAddress2")),)
    assert b["picks"] == (("Properties.role", "admin,employee,owner"),)
    assert a["shape"] == b["shape"] and a["shape"] != c["shape"]
    groups = dr.group_entries([a, b, c], 2)
    assert len(groups) == 1 and sorted(m["entry"] for m in groups[0]["members"]) == [
        "OnboardingFlowAGuestidmember2", "OnboardingFlowAGuestidmember3"]
    assert "partnerProgramAccountNumber" in groups[0]["varies"]["generated fields"]
    assert groups[0]["varies"]["picked values"] == ["Properties.role"]
    assert dr.parse_entry(FIXTURE, "Steps.java") is None       # a Steps class is not an entry


def test_report_counts_entry_classes():
    entries = [dr.parse_entry(s, "e.java") for s in (ENTRY_A, ENTRY_B, ENTRY_C)]
    recs = dr.parse_methods(FIXTURE, "Steps.java")
    text = dr.render(recs, dr.group_by_shape(recs, 2), entries=entries)
    assert "entry classes: 3 -> distinct setup shapes: 2" in text
    assert "2 classes in 1 groups differ only in generated fields / picked values" in text


def test_write_audit_produces_the_file_and_extends_summary(tmp_path=None):
    with tempfile.TemporaryDirectory() as d:
        root = tmp_path or d
        support = os.path.join(root, "src", "main", "java", "com", "ak", "api", "support", "s")
        os.makedirs(support)
        with open(os.path.join(support, "Steps.java"), "w", encoding="utf-8") as fh:
            fh.write(FIXTURE)
        audit = os.path.join(root, "_audit", "unit")
        os.makedirs(audit)
        with open(os.path.join(audit, "summary.md"), "w", encoding="utf-8") as fh:
            fh.write("# summary\n")
        headline = dr.write_audit(root, "com.ak.api", "unit")
        assert headline and "2 methods in 1 groups" in headline, headline
        assert os.path.exists(os.path.join(audit, "dedup_report.txt"))
        summary = open(os.path.join(audit, "summary.md"), encoding="utf-8").read()
        assert "## Reuse (same call, different data)" in summary
        assert "GET /guests/{}/businesses/verify" in summary
        # a tree with nothing generated is not an error, just no report
        assert dr.write_audit(os.path.join(root, "nowhere"), "com.ak.api", "unit") is None


def _templates_root(tmp, bodies):
    """Write {name: tree} under a throwaway src/main/resources/templates/s."""
    import json
    d = os.path.join(tmp, "src", "main", "resources", "templates", "s")
    os.makedirs(d, exist_ok=True)
    for name, tree in bodies.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            json.dump(tree, fh)
    return tmp


def test_two_identity_slots_are_never_one_family():
    """A mis-read placeholder invents merge opportunities that must not exist.

    Both pairs below were advertised as "still mergeable" until
    template_stats started sharing ra_converter._leaf_is_placeholder:

      @Properties_guestIDmember2@ vs @Properties_memberGuestID@
          -- `@...@` was not matched at all by the old `#[^#]+#` regex
      #Properties_username##Properties_Hardcodeddomain#
          -- concatenated, so an inner `#` defeated the same regex

    Merging either makes two different identity slots share one CSV cell.
    That is the failure that made MemberHHonorsEnroll 409 on a duplicate
    email, and the Tier-2 merger already refuses it -- only the audit
    disagreed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _templates_root(tmp, {
            "a_1.json": {"guestId": "@Properties_guestIDmember2@"},
            "a_2.json": {"guestId": "@Properties_memberGuestID@"},
            "b_1.json": {"emailDomain":
                         "#Properties_username##Properties_Hardcodeddomain#"},
            "b_2.json": {"emailDomain":
                         "#Properties_username##Properties_Domain#"},
        })
        st = dr.template_stats(root)
    assert st["files"] == 4, st
    assert st["mergeable_groups"] == 0, (
        "different identity slots reported mergeable: %s" % (st["largest"],))
    assert st["families"] == 4, st


def test_same_placeholders_different_literals_still_merge():
    """NEGATIVE CONTROL: the fix must not silence real merge candidates.

    Identical placeholders at identical paths, differing only in literal
    data, is precisely what Tier 2 collapses into one template plus tpl_*
    columns. If this stops being reported, the audit has gone blind rather
    than accurate.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _templates_root(tmp, {
            "c_1.json": {"guestId": "@Properties_memberGuestID@", "city": "Houston"},
            "c_2.json": {"guestId": "@Properties_memberGuestID@", "city": "Austin"},
        })
        st = dr.template_stats(root)
    assert st["files"] == 2, st
    assert st["mergeable_groups"] == 1, (
        "a genuine literal-only difference was not reported: %s" % (st,))
    assert st["mergeable_files"] == 2, st

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
