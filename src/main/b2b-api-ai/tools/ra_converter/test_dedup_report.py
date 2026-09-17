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
