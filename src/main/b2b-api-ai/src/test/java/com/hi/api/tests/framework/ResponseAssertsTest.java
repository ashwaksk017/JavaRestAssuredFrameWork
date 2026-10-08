package com.hi.api.tests.framework;

import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.hi.api.rest.utilities.ResponseAsserts;
import com.hi.api.rest.utilities.RestUtilities;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Readable-test utilities")
public class ResponseAssertsTest {

    private static Response json(int status, String body) {
        return new ResponseBuilder()
                .setStatusCode(status)
                .setContentType(ContentType.JSON)
                .setBody(body)
                .build();
    }

    private static void assertSoftPass(SoftAssert sa) {
        sa.assertAll();
    }

    private static void assertSoftFail(SoftAssert sa) {
        try {
            sa.assertAll();
            Assert.fail("expected SoftAssert to fail");
        } catch (AssertionError expected) {
            // assertion failed as intended
        }
    }

    @Test(groups = {"unit"})
    @Story("columnSuffix flattening")
    @Description("JsonPath suffixes match ra_converter CSV column naming.")
    public void columnSuffix_flattensConverterPaths() {
        Assert.assertEquals(ResponseAsserts.columnSuffix("[0].allowBookOnBehalf"),
                "0_allowBookOnBehalf");
        Assert.assertEquals(ResponseAsserts.columnSuffix("[*].centralBillAuthAmount"),
                "centralBillAuthAmount");
        Assert.assertEquals(ResponseAsserts.columnSuffix("centralBillProfile.created"),
                "centralBillProfile_created");
        Assert.assertEquals(ResponseAsserts.columnSuffix("accountStatus"),
                "accountStatus");
        Assert.assertEquals(ResponseAsserts.columnSuffix("notifications[0].message"),
                "notifications_0_message");
        Assert.assertEquals(ResponseAsserts.columnSuffix(
                "[0].spendDetails.items[0].arrivalDate"),
                "0_spendDetails_items_0_arrivalDate");
        Assert.assertEquals(ResponseAsserts.columnSuffix("emailDomains[0]"),
                "emailDomains_0");
        Assert.assertEquals(ResponseAsserts.columnSuffix("$.accountStatus"),
                "accountStatus");
    }

    @Test(groups = {"unit"})
    @Story("ReadyAPI JsonPath Domain typo")
    @Description("contactInfo.Domain (ReadyAPI XML) reads contactInfo.websiteDomain when Domain is absent.")
    public void jsonEquals_readyApiDomainPathUsesWebsiteDomain() {
        Response res = json(200, "{\"contactInfo\":{\"websiteDomain\":\"www.example.com\"}}");
        Assert.assertEquals(RestUtilities.safeJsonExtract(res, "contactInfo.Domain"),
                "www.example.com");
        Map<String, String> ctx = new HashMap<>();
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.jsonEquals(sa, res, ctx, new HashMap<>(),
                "http_get_account_details_200",
                "contactInfo.Domain", "www.example.com");
        assertSoftPass(sa);
    }

    @Test(groups = {"unit"})
    @Story("ReadyAPI JsonPath root status vs memberStatus")
    @Description("Root path status with no such field uses memberStatus (assertion name), not accountStatus.")
    public void jsonEquals_readyApiRootStatusUsesMemberStatusNotAccountStatus() {
        Response res = json(200,
                "{\"accountStatus\":\"limited\",\"memberStatus\":\"active\"}");
        Assert.assertEquals(RestUtilities.safeJsonExtract(res, "status"), "active");
        Map<String, String> ctx = new HashMap<>();
        SoftAssert pass = new SoftAssert();
        ResponseAsserts.jsonEquals(pass, res, ctx, new HashMap<>(),
                "http_get_account_details_200", "status", "active");
        assertSoftPass(pass);

        Response wrong = json(200,
                "{\"accountStatus\":\"active\",\"memberStatus\":\"limited\"}");
        Assert.assertEquals(RestUtilities.safeJsonExtract(wrong, "status"), "limited");
        SoftAssert fail = new SoftAssert();
        ResponseAsserts.jsonEquals(fail, wrong, ctx, new HashMap<>(),
                "http_get_account_details_200", "status", "active");
        assertSoftFail(fail);
    }

    @Test(groups = {"unit"})
    @Story("jsonEquals finds value after JSON wrap changes")
    @Description("accountStatus nested under data still matches; inactive does not match active.")
    public void jsonEquals_containsWhenPathMoved() {
        Map<String, String> ctx = new HashMap<>();
        SoftAssert nested = new SoftAssert();
        ResponseAsserts.jsonEquals(nested,
                json(200, "{\"data\":{\"account\":{\"accountStatus\":\"active\"}}}"),
                ctx, new HashMap<>(), "post_confirm_validation_limited",
                "accountStatus", "active");
        assertSoftPass(nested);

        SoftAssert missing = new SoftAssert();
        ResponseAsserts.jsonEquals(missing, json(200, "{\"error\":\"forbidden\"}"),
                ctx, new HashMap<>(), "post_confirm_validation_limited",
                "accountStatus", "active");
        assertSoftFail(missing);

        SoftAssert inactive = new SoftAssert();
        ResponseAsserts.jsonEquals(inactive, json(200, "{\"accountStatus\":\"inactive\"}"),
                ctx, new HashMap<>(), "post_confirm_validation_limited",
                "accountStatus", "active");
        assertSoftFail(inactive);

        SoftAssert unverified = new SoftAssert();
        ResponseAsserts.jsonEquals(unverified, json(200, "{\"status\":\"unverified\"}"),
                ctx, new HashMap<>(), "post_confirm_validation_limited",
                "attestationSummary.status", "verified");
        assertSoftFail(unverified);

        SoftAssert pretty = new SoftAssert();
        ResponseAsserts.jsonEquals(pretty,
                json(200, "{\n  \"data\" : {\n    \"accountStatus\" : \"active\"\n  }\n}"),
                ctx, new HashMap<>(), "post_confirm_validation_limited",
                "accountStatus", "active");
        assertSoftPass(pretty);

        SoftAssert unicode = new SoftAssert();
        ResponseAsserts.jsonEquals(unicode,
                json(200, "{\"accountStatus\":\"\\u0061ctive\"}"),
                ctx, new HashMap<>(), "post_confirm_validation_limited",
                "accountStatus", "active");
        assertSoftPass(unicode);
    }

    @Test(groups = {"unit"})
    @Story("jsonEquals path mismatch does not use a sibling field")
    @Description("accountStatus=limited must fail even when memberStatus=active is in the same body.")
    public void jsonEquals_pathMismatchDoesNotMatchSiblingField() {
        Map<String, String> ctx = new HashMap<>();
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.jsonEquals(sa,
                json(200, "{\"accountStatus\":\"limited\",\"memberStatus\":\"active\"}"),
                ctx, new HashMap<>(), "post_confirm_validation_limited",
                "accountStatus", "active");
        assertSoftFail(sa);
    }

    @Test(groups = {"unit"})
    @Story("jsonEquals empty expected means empty path")
    @Description("Blank ReadyAPI JsonPath content asserts the path value is empty, not a skip.")
    public void jsonEquals_emptyExpectedRequiresEmptyPath() {
        Map<String, String> ctx = new HashMap<>();
        SoftAssert emptyOk = new SoftAssert();
        ResponseAsserts.jsonEquals(emptyOk, json(200, "{\"accountStatus\":\"\"}"),
                ctx, new HashMap<>(), "step", "accountStatus", "");
        assertSoftPass(emptyOk);

        SoftAssert emptyFail = new SoftAssert();
        ResponseAsserts.jsonEquals(emptyFail, json(200, "{\"accountStatus\":\"active\"}"),
                ctx, new HashMap<>(), "step", "accountStatus", "");
        assertSoftFail(emptyFail);
    }

    @Test(groups = {"unit"})
    @Story("jsonTreeEquals falls back to body leaves")
    public void jsonTreeEquals_wrapChangeStillPasses() {
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.jsonTreeEquals(sa,
                json(200, "{\"wrap\":{\"ok\":true,\"name\":\"SMB\"}}"),
                new HashMap<>(), new HashMap<>(), "step", "payload",
                "{\"ok\":true,\"name\":\"SMB\"}");
        assertSoftPass(sa);
    }

    @Test(groups = {"unit"})
    @Story("bodyContains quoted JSON vs substring trap")
    @Description("Quoted JSON strings match anywhere; 'active' is not found inside 'inactive'.")
    public void bodyContains_quotedJsonAndBareToken() {
        Assert.assertTrue(ResponseAsserts.bodyContainsExpected(
                "{\"wrap\":{\"memberStatus\":\"verified\"}}", "verified"));
        Assert.assertFalse(ResponseAsserts.bodyContainsExpected(
                "{\"accountStatus\":\"inactive\"}", "active"));
        Assert.assertTrue(ResponseAsserts.bodyContainsExpected(
                "{\"count\":12}", "12"));
        Assert.assertTrue(ResponseAsserts.bodyContainsExpected(
                "{\"email\":\"a@b.com\"}", "a@b.com"));
        Assert.assertFalse(ResponseAsserts.bodyContainsExpected(
                "{\"status\":\"unverified\"}", "verified"));
        Assert.assertTrue(ResponseAsserts.bodyContainsExpected(
                "{\n  \"memberStatus\" : \"verified\"\n}", "verified"));
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.bodyContains(sa, json(200, "{\"attestationSummary\":{\"source\":\"hiltonAgencyProfile\"}}"),
                "hiltonAgencyProfile", "JsonPath Match: attestationSummary.source");
        assertSoftPass(sa);
    }

    @Test(groups = {"unit"})
    @Story("jsonEquals CSV override vs default")
    @Description("CSV expected_<step>_jsonpath_* wins over the hardcoded default.")
    public void jsonEquals_csvOverrideBeatsDefault() {
        Map<String, String> ctx = new HashMap<>();
        Map<String, String> row = new HashMap<>();
        row.put("expected_post_confirm_validation_limited_jsonpath_accountStatus", "csvStatus");

        SoftAssert pass = new SoftAssert();
        ResponseAsserts.jsonEquals(pass, json(200, "{\"accountStatus\":\"csvStatus\"}"),
                ctx, row, "post_confirm_validation_limited", "accountStatus", "limited");
        assertSoftPass(pass);

        SoftAssert stillDefault = new SoftAssert();
        ResponseAsserts.jsonEquals(stillDefault, json(200, "{\"accountStatus\":\"limited\"}"),
                ctx, new HashMap<>(), "post_confirm_validation_limited",
                "accountStatus", "limited");
        assertSoftPass(stillDefault);

        SoftAssert defaultMismatch = new SoftAssert();
        ResponseAsserts.jsonEquals(defaultMismatch, json(200, "{\"accountStatus\":\"other\"}"),
                ctx, new HashMap<>(), "post_confirm_validation_limited",
                "accountStatus", "limited");
        assertSoftFail(defaultMismatch);
    }

    @Test(groups = {"unit"})
    @Story("jsonEquals ignores unresolved @Properties_expected_ rewrite")
    @Description("CSV @Properties_expected_…hhonorsNumber@ with no ctx value uses the Java default.")
    public void jsonEquals_unresolvedExpectedPropertiesFallsBackToDefault() {
        Map<String, String> ctx = new HashMap<>();
        Map<String, String> row = new HashMap<>();
        row.put("expected_http_request_200_read_program_account_add_jsonpath_honorsMembership_hhonorsNumber",
                "@Properties_expected_http_request_200_read_program_account_add_jsonpath_honorsMembership_hhonorsNumber@");
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.jsonEquals(sa,
                json(200, "{\"honorsMembership\":{\"hhonorsNumber\":\"1234567891\"}}"),
                ctx, row, "http_request_200_read_program_account_add",
                "honorsMembership.hhonorsNumber", "1234567891");
        assertSoftPass(sa);
    }

    @Test(groups = {"unit"})
    @Story("jsonEquals mid-path index CSV column")
    @Description("notifications[0].message reads expected_<step>_jsonpath_notifications_0_message.")
    public void jsonEquals_indexedPathUsesCleanSuffix() {
        Map<String, String> ctx = new HashMap<>();
        Map<String, String> row = new HashMap<>();
        row.put("expected_http_get_spendDetail_jsonpath_notifications_0_message",
                "Invalid search combination.");
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.jsonEquals(sa,
                json(400, "{\"notifications\":[{\"message\":\"Invalid search combination.\"}]}"),
                ctx, row, "http_get_spendDetail", "notifications[0].message",
                "other-default");
        assertSoftPass(sa);
    }

    @Test(groups = {"unit"})
    @Story("jsonEquals msgcontent fallback column")
    @Description("expected_<step>_msgcontent_<field> is used when the jsonpath column is empty.")
    public void jsonEquals_msgcontentColumn() {
        Map<String, String> ctx = new HashMap<>();
        Map<String, String> row = new HashMap<>();
        row.put("expected_post_confirm_validation_limited_msgcontent_accountStatus", "fromMsg");
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.jsonEquals(sa, json(200, "{\"accountStatus\":\"fromMsg\"}"),
                ctx, row, "post_confirm_validation_limited", "accountStatus", "limited");
        assertSoftPass(sa);
    }

    @Test(groups = {"unit"})
    @Story("An element is compared against its own column")
    @Description("Four elements end in `confidence`; each has a numbered column. The search by name took the lowest for all four.")
    public void jsonEqualsAt_usesTheElementsOwnColumn() {
        String step = "http_request_200_1";
        Map<String, String> row = new HashMap<>();
        row.put("expected_" + step + "_msgcontent_2_confidence", "medium");
        row.put("expected_" + step + "_msgcontent_5_confidence", "high");
        row.put("expected_" + step + "_msgcontent_7_confidence", "veryLow");
        Response res = json(200, "{\"externalMatch\":{\"attestation\":{\"confidence\":\"medium\"}},"
                + "\"internalMatch\":[{\"attestation\":{\"confidence\":\"high\"}},"
                + "{\"attestation\":{\"confidence\":\"veryLow\"}}]}");

        SoftAssert sa = new SoftAssert();
        ResponseAsserts.jsonEqualsAt(sa, res, new HashMap<>(), row, step,
                "externalMatch.attestation.confidence", "x", "expected_" + step + "_msgcontent_2_confidence");
        ResponseAsserts.jsonEqualsAt(sa, res, new HashMap<>(), row, step,
                "internalMatch[0].attestation.confidence", "x", "expected_" + step + "_msgcontent_5_confidence");
        ResponseAsserts.jsonEqualsAt(sa, res, new HashMap<>(), row, step,
                "internalMatch[1].attestation.confidence", "x", "expected_" + step + "_msgcontent_7_confidence");
        assertSoftPass(sa);

        // NEGATIVE CONTROL: the search by name compares internalMatch[0]
        // ("high") against column 2 ("medium") -- the failure this fixes.
        SoftAssert byName = new SoftAssert();
        ResponseAsserts.jsonEquals(byName, res, new HashMap<>(), row, step,
                "internalMatch[0].attestation.confidence", "x");
        Assert.expectThrows(AssertionError.class, byName::assertAll);
    }

    @Test(groups = {"unit"})
    @Story("An element is compared against its own column")
    @Description("An empty cell in the element's own column means this case does not assert it; a row without the column falls back to the search by name.")
    public void jsonEqualsAt_emptyCellSkips_missingColumnFallsBack() {
        String step = "http_request_200_1";
        Response res = json(200, "{\"a\":{\"confidence\":\"high\"}}");

        Map<String, String> blank = new HashMap<>();
        blank.put("expected_" + step + "_msgcontent_5_confidence", "");
        blank.put("expected_" + step + "_msgcontent_2_confidence", "medium");   // a sibling's
        SoftAssert skipped = new SoftAssert();
        ResponseAsserts.jsonEqualsAt(skipped, res, new HashMap<>(), blank, step,
                "a.confidence", "medium", "expected_" + step + "_msgcontent_5_confidence");
        assertSoftPass(skipped);

        Map<String, String> older = new HashMap<>();
        older.put("expected_" + step + "_msgcontent_confidence", "high");
        SoftAssert fallback = new SoftAssert();
        ResponseAsserts.jsonEqualsAt(fallback, res, new HashMap<>(), older, step,
                "a.confidence", "x", "expected_" + step + "_msgcontent_5_confidence");
        ResponseAsserts.jsonEqualsAt(fallback, res, new HashMap<>(), older, step,
                "a.confidence", "x", null);
        assertSoftPass(fallback);
    }

    @Test(groups = {"unit"})
    @Story("jsonExists CSV false asserts absence")
    @Description("expected_<step>_exists_<suffix>=false is ReadyAPI content=false: path must be absent.")
    public void jsonExists_csvFalse_assertsAbsent() {
        Map<String, String> absent = new HashMap<>();
        absent.put("expected_GET_account_member_details_200_exists_centralBillProfile_created",
                "false");
        SoftAssert missingOk = new SoftAssert();
        ResponseAsserts.jsonExists(missingOk, json(200, "{}"), absent,
                "GET_account_member_details_200", "centralBillProfile.created");
        assertSoftPass(missingOk);

        SoftAssert presentFails = new SoftAssert();
        ResponseAsserts.jsonExists(presentFails,
                json(200, "{\"centralBillProfile\":{\"created\":\"2020-01-01\"}}"),
                absent, "GET_account_member_details_200",
                "centralBillProfile.created");
        assertSoftFail(presentFails);

        SoftAssert required = new SoftAssert();
        ResponseAsserts.jsonExists(required, json(200, "{}"), new HashMap<>(),
                "GET_account_member_details_200", "centralBillProfile.created");
        assertSoftFail(required);

        SoftAssert present = new SoftAssert();
        ResponseAsserts.jsonExists(present,
                json(200, "{\"centralBillProfile\":{\"created\":\"2020-01-01\"}}"),
                new HashMap<>(), "GET_account_member_details_200",
                "centralBillProfile.created");
        assertSoftPass(present);

        Map<String, String> indexedSkip = new HashMap<>();
        indexedSkip.put("expected_http_get_spendDetail_exists_notifications_0_message",
                "false");
        SoftAssert indexed = new SoftAssert();
        ResponseAsserts.jsonExists(indexed, json(400, "{}"), indexedSkip,
                "http_get_spendDetail", "notifications[0].message");
        assertSoftPass(indexed);
    }

    @Test(groups = {"unit"})
    @Story("jsonAbsent passes when path is missing")
    @Description("ReadyAPI Existence Match content=false: pendingEmailAddress omitted is a pass.")
    public void jsonAbsent_passesWhenPathMissing_failsWhenPresent() {
        SoftAssert missing = new SoftAssert();
        ResponseAsserts.jsonAbsent(missing,
                json(200, "{\"emailAddress\":\"updated@laafd.com\",\"status\":\"pending\"}"),
                "pendingEmailAddress");
        assertSoftPass(missing);

        SoftAssert present = new SoftAssert();
        ResponseAsserts.jsonAbsent(present,
                json(200, "{\"pendingEmailAddress\":\"updated@laafd.com\"}"),
                "pendingEmailAddress");
        assertSoftFail(present);
    }

    @Test(groups = {"unit"})
    @Story("jsonCount CSV override vs default")
    @Description("count uses expected_<step>_count_<suffix> when present.")
    public void jsonCount_csvOverrideBeatsDefault() {
        Response body = json(200, "{\"items\":[1,2,3]}");
        Map<String, String> row = new HashMap<>();
        row.put("expected_GET_booking_policies_count_items", "3");
        SoftAssert csv = new SoftAssert();
        ResponseAsserts.jsonCount(csv, body, row, "GET_booking_policies", "items", 1);
        assertSoftPass(csv);

        SoftAssert def = new SoftAssert();
        ResponseAsserts.jsonCount(def, body, new HashMap<>(), "GET_booking_policies", "items", 3);
        assertSoftPass(def);
    }

    @Test(groups = {"unit"})
    @Story("status CSV override and skip sentinel")
    @Description("status_code CSV wins; negative expected skips the assertion.")
    public void status_csvOverrideAndSkip() {
        Map<String, String> override = new HashMap<>();
        override.put("expected_http_request_200_enroll_guest_status_code", "201");
        SoftAssert csv = new SoftAssert();
        ResponseAsserts.status(csv, json(201, "{}"), override,
                "http_request_200_enroll_guest", 200);
        assertSoftPass(csv);

        Map<String, String> skip = new HashMap<>();
        skip.put("expected_http_request_200_enroll_guest_status_code", "-1");
        SoftAssert skipped = new SoftAssert();
        ResponseAsserts.status(skipped, json(500, "{}"), skip,
                "http_request_200_enroll_guest", 200);
        assertSoftPass(skipped);
    }

    @Test(groups = {"unit"})
    @Story("statusFromStepColumn ignores row.expected")
    @Description("SetupHelper tokenRequest must not inherit a negative-test 403 from the expected column.")
    public void statusFromStepColumn_ignoresRowExpected() {
        Map<String, String> row = new HashMap<>();
        row.put("expected", "statusCode:403");
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.statusFromStepColumn(sa, json(200, "{}"), row, "tokenRequest", 200);
        assertSoftPass(sa);
    }

    private static final String INVALID_COL =
            "expected_get_using_invalid_query_parameters_400_invalid_status_codes";

    @Test
    @Story("the recorded invalid codes are used when the row says nothing")
    @Description("ReadyAPI's Invalid HTTP Status Codes assertion. The "
            + "converter bakes what it recorded; the row may override.")
    public void recordedInvalidCodesFailAMatchingStatus() {
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.invalidStatus(sa, json(200, "{}"), new HashMap<>(),
                INVALID_COL, "200, 204, 206");
        Assert.assertThrows(AssertionError.class, sa::assertAll);
    }

    @Test
    @Story("a status outside the recorded list passes")
    public void recordedInvalidCodesAllowOtherStatuses() {
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.invalidStatus(sa, json(400, "{}"), new HashMap<>(),
                INVALID_COL, "200, 204, 206");
        sa.assertAll();
    }

    @Test
    @Story("the CSV column overrides the recorded list")
    @Description("The generated comment promised this override for a long "
            + "time while nothing read the column.")
    public void theRowOverridesTheRecordedCodes() {
        Map<String, String> row = new HashMap<>();
        row.put(INVALID_COL, "400 404");

        SoftAssert forbidden = new SoftAssert();
        ResponseAsserts.invalidStatus(forbidden, json(404, "{}"), row,
                INVALID_COL, "200, 204, 206");
        Assert.assertThrows(AssertionError.class, forbidden::assertAll);

        // 200 is in the RECORDED list but not in the row's -- the row wins
        SoftAssert allowed = new SoftAssert();
        ResponseAsserts.invalidStatus(allowed, json(200, "{}"), row,
                INVALID_COL, "200, 204, 206");
        allowed.assertAll();
    }

    @Test
    @Story("an empty list forbids nothing and is skipped, not passed")
    public void anEmptyListIsASkip() {
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.invalidStatus(sa, json(200, "{}"), new HashMap<>(),
                INVALID_COL, "");
        sa.assertAll();     // nothing forbidden -> nothing asserted
    }

    @Test
    @Story("a blank cell falls back to the recorded list")
    public void aBlankCellDoesNotMeanNoCodes() {
        Map<String, String> row = new HashMap<>();
        row.put(INVALID_COL, "   ");
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.invalidStatus(sa, json(200, "{}"), row,
                INVALID_COL, "200");
        Assert.assertThrows(AssertionError.class, sa::assertAll);
    }

    // ---------------- ReadyAPI "Simple" assertions (rawBodyContains)
    //
    // These used to be inlined by the converter as
    // `softAssert.assertTrue(res.asString().contains(token), ...)`, which
    // bypassed this class and therefore the unresolved-placeholder guard.

    @Test
    @Story("a hash-wrapped placeholder that nothing resolved is skipped, not failed")
    public void anUnresolvedTokenIsSkippedNotFailed() {
        ResponseAsserts.resetSkippedCountsForTest();
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.rawBodyContains(sa, json(200, "{\"roomTypeCode\":\"K1S\"}"),
                "#groupid_roomTypeCode#", true, "Simple Contains");
        sa.assertAll();     // must NOT fail: the check could only be false
        Assert.assertEquals(ResponseAsserts.skippedTotal(), 1,
                "a skipped check must be COUNTED, or the digest reports zero "
                + "while checks silently did not run");
    }

    @Test
    @Story("NotContains with an unresolved placeholder is skipped, not a free pass")
    public void anUnresolvedTokenDoesNotPassVacuously() {
        ResponseAsserts.resetSkippedCountsForTest();
        SoftAssert sa = new SoftAssert();
        // The old inlined form was assertFalse(body.contains("#x#")) -- always
        // true, so it PASSED while verifying nothing at all.
        ResponseAsserts.rawBodyContains(sa, json(200, "{\"a\":1}"),
                "#groupid_roomTypeCode#", false, "Simple NotContains");
        sa.assertAll();
        Assert.assertEquals(ResponseAsserts.skippedTotal(), 1,
                "a vacuous pass must be recorded as a skip");
    }

    @Test
    @Story("a resolved token still does a RAW substring match, not a JSON walk")
    public void aResolvedTokenMatchesRawSubstring() {
        ResponseAsserts.resetSkippedCountsForTest();
        SoftAssert sa = new SoftAssert();
        // "oomTypeCod" is not a JSON scalar and not a quoted string, so the
        // bodyContains/valueInResponse path would NOT match it. ReadyAPI's
        // Simple Contains is a plain String.contains and must still pass,
        // which is why this does not reuse that path.
        ResponseAsserts.rawBodyContains(sa, json(200, "{\"roomTypeCode\":\"K1S\"}"),
                "oomTypeCod", true, "Simple Contains");
        sa.assertAll();
        Assert.assertEquals(ResponseAsserts.skippedTotal(), 0);
    }

    @Test
    @Story("a resolved token that is genuinely absent still fails")
    public void aResolvedTokenThatIsAbsentFails() {
        ResponseAsserts.resetSkippedCountsForTest();
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.rawBodyContains(sa, json(200, "{\"roomTypeCode\":\"K1S\"}"),
                "Q2SR", true, "Simple Contains");
        Assert.assertThrows(AssertionError.class, sa::assertAll);
        Assert.assertEquals(ResponseAsserts.skippedTotal(), 0,
                "a real miss is a finding, not a skip");
    }

    @Test
    @Story("NotContains fails when the token IS present")
    public void notContainsFailsWhenPresent() {
        ResponseAsserts.resetSkippedCountsForTest();
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.rawBodyContains(sa, json(200, "{\"roomTypeCode\":\"K1S\"}"),
                "K1S", false, "Simple NotContains");
        Assert.assertThrows(AssertionError.class, sa::assertAll);
    }

    @Test
    @Story("a ${..} reference that survived resolution is skipped too")
    public void aDollarRefIsAlsoSkipped() {
        ResponseAsserts.resetSkippedCountsForTest();
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.rawBodyContains(sa, json(200, "{\"a\":1}"),
                "${groupid#roomTypeCode}", true, "Simple Contains");
        sa.assertAll();
        Assert.assertEquals(ResponseAsserts.skippedTotal(), 1);
    }
}
