package com.ak.api.tests.framework;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import org.testng.Assert;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.ak.api.rest.utilities.RestStep;

import io.qameta.allure.Allure;
import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Readable-test utilities")
public class RestStepTest {

    @Test(groups = {"unit"})
    @Story("Query placeholder resolution")
    @Description("resolveQuery expands #Properties_Email# via mergedRow aliases, matching generated enroll query params.")
    public void resolveQuery_expandsHashPlaceholders() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Email", "owner@example.com");
        ctx.put("Properties.Phone", "555123456");
        Map<String, String> raw = new LinkedHashMap<>();
        raw.put("ownerEmailAddress", "#Properties_Email#");
        raw.put("phoneNum", "#Properties_Phone#");
        Map<String, String> q = RestStep.resolveQuery(new HashMap<>(), ctx, raw);
        Assert.assertEquals(q.get("ownerEmailAddress"), "owner@example.com");
        Assert.assertEquals(q.get("phoneNum"), "555123456");
    }

    @Test(groups = {"unit"})
    @Story("Empty query CSV cells stay empty, not the word null")
    @Description("mapJsonValues fallback 'null' must not be sent as travelAgentId=null; the param key is still present.")
    public void resolveQuery_emptyCsvIsEmptyStringNotWordNull() throws Exception {
        Map<String, String> raw = new LinkedHashMap<>();
        raw.put("emailDomain", "ride.com");
        raw.put("travelAgentId", "");
        raw.put("country", "#qry_country#");
        Map<String, String> row = new HashMap<>();
        row.put("qry_country", "");
        Map<String, String> q = RestStep.resolveQuery(row, new HashMap<>(), raw);
        Assert.assertEquals(q.get("emailDomain"), "ride.com");
        Assert.assertTrue(q.containsKey("travelAgentId"), q.toString());
        Assert.assertEquals(q.get("travelAgentId"), "");
        Assert.assertTrue(q.containsKey("country"), q.toString());
        Assert.assertEquals(q.get("country"), "");
        Assert.assertNotEquals(q.get("travelAgentId"), "null");
    }

    @Test(groups = {"unit"})
    @Story("Query resolution of guestID after generateStandard")
    @Description("#Properties_guestID# resolves after generateStandard; guestId/guestID are not collapsed.")
    public void resolveQuery_guestIDAfterGenerateStandard() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        com.ak.api.support.CtxFields.generateStandard(ctx, "Properties");
        Map<String, String> raw = new LinkedHashMap<>();
        raw.put("guestId", "#Properties_guestID#");
        Map<String, String> q = RestStep.resolveQuery(new HashMap<>(), ctx, raw);
        Assert.assertEquals(q.get("guestId"), ctx.get("Properties.guestID"));
        Assert.assertFalse(q.get("guestId").isEmpty());
        Assert.assertNotEquals(q.get("guestId"), "null");
    }

    @Test(groups = {"unit"})
    @Story("GET businesses polls until salesforceId when MessageContent column exists")
    public void exec_get_pollsUntilSalesforceIdPresent() throws Exception {
        System.setProperty("rest.pollSalesforceIdMs", "4000");
        System.setProperty("rest.pollSalesforceIdIntervalMs", "1");
        try {
            Map<String, String> row = new HashMap<>();
            row.put("expected_http_request_200_2_msgcontent_salesforceId", "");
            row.put("expected_http_request_200_2_status_code", "200");
            AtomicInteger hits = new AtomicInteger();
            SoftAssert sa = new SoftAssert();
            Response res = RestStep.exec(new HashMap<>(), row, sa, null, "B2B-2065")
                    .name("http_request_200_2")
                    .expectedStatus(200)
                    .get("/businesses/1", (body, q, h) -> {
                        int n = hits.incrementAndGet();
                        String json = n < 3
                                ? "{\"status\":\"active\"}"
                                : "{\"status\":\"active\",\"alternateAccounts\":{\"salesforceId\":\"001abc\"}}";
                        return new ResponseBuilder()
                                .setStatusCode(200)
                                .setContentType(ContentType.JSON)
                                .setBody(json)
                                .build();
                    });
            Assert.assertTrue(hits.get() >= 3, "hits=" + hits.get());
            Assert.assertEquals(
                    com.ak.api.rest.utilities.RestUtilities.safeJsonExtract(
                            res, "alternateAccounts.salesforceId"),
                    "001abc");
            sa.assertAll();
        } finally {
            System.clearProperty("rest.pollSalesforceIdMs");
            System.clearProperty("rest.pollSalesforceIdIntervalMs");
        }
    }

    @Test(groups = {"unit"})
    @Story("Hyphenated Properties#hilton-member-id path placeholders")
    @Description("@Properties_hilton-member-id@ resolves from PropertiesaccountID.hilton-member-id.")
    public void resolveQuery_hiltonMemberIdFromAliasedPropertiesStep() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("PropertiesaccountID.hilton-member-id", "349237001");
        Map<String, String> raw = new LinkedHashMap<>();
        raw.put("memberId", "@Properties_hilton-member-id@");
        Map<String, String> q = RestStep.resolveQuery(new HashMap<>(), ctx, raw);
        Assert.assertEquals(q.get("memberId"), "349237001");
        Assert.assertEquals(
                com.ak.api.data.PlaceholderResolver.resolveAll(
                        "/members/@Properties_hilton-member-id@/confirmValidation", ctx),
                "/members/349237001/confirmValidation");
    }

    @Test(groups = {"unit"})
    @Story("RestStep.exec captures query and asserts status")
    @Description("Fluent post() resolves query placeholders, invokes the Exchange, and status-asserts 200.")
    public void exec_post_resolvesQueryAndStatus() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Email", "enroll@example.com");
        Map<String, String> row = new HashMap<>();
        row.put("expected_http_request_200_enroll_guest_status_code", "200");
        SoftAssert sa = new SoftAssert();

        Response fake = new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody("{\"guestId\":\"190099999\"}")
                .build();

        final Map<String, String>[] capturedQuery = new Map[1];
        Response enroll = RestStep.exec(ctx, row, sa, null, "B2B-9098")
                .name("http_request_200_enroll_guest")
                .query("ownerEmailAddress", "#Properties_Email#")
                .expectedStatus(200)
                .post("/realms/guests/enroll", (body, q, h) -> {
                    capturedQuery[0] = q;
                    return fake;
                });

        Assert.assertEquals(enroll.statusCode(), 200);
        Assert.assertEquals(capturedQuery[0].get("ownerEmailAddress"), "enroll@example.com");
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("RestStep.lastResolvedBody after exec")
    @Description("lastResolvedBody is empty when no template is set; extra headers skip Authorization.")
    public void exec_recordsLastResolvedBodyAndSkipsAuthHeader() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Email", "h@example.com");
        SoftAssert sa = new SoftAssert();
        Response fake = new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody("{}")
                .build();
        final Map<String, String>[] capturedHeaders = new Map[1];
        RestStep.exec(ctx, new HashMap<>(), sa, null, "B2B-9098")
                .name("tokenRequest")
                .header("X-Custom", "#Properties_Email#")
                .header("Authorization", "should-skip")
                .expectedStatus(200)
                .get("/token", (body, q, h) -> {
                    capturedHeaders[0] = h;
                    return fake;
                });
        Assert.assertEquals(RestStep.lastResolvedBody(), "");
        Assert.assertEquals(capturedHeaders[0].get("X-Custom"), "h@example.com");
        Assert.assertFalse(capturedHeaders[0].containsKey("Authorization"));
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("Allure Request/Response nest under the REST step")
    @Description("The HTTP exchange runs while an Allure step is current, so "
            + "AllureRestAssured attachments attach to that step instead of "
            + "the test case.")
    public void allureStepStaysOpenDuringHttpExchange() throws Exception {
        var life = Allure.getLifecycle();
        String before = life.getCurrentTestCaseOrStep().orElse(null);
        AtomicReference<String> duringCall = new AtomicReference<>();
        Response fake = new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody("{}")
                .build();
        RestStep.exec(new HashMap<>(), new HashMap<>(), new SoftAssert(), null, "case")
                .name("HHonorsEnroll")
                .expectedStatus(200)
                .post("/realms/guests/enroll", (body, q, h) -> {
                    life.getCurrentTestCaseOrStep().ifPresent(duringCall::set);
                    return fake;
                });
        Assert.assertNotNull(duringCall.get(), "Allure current item should be set during HTTP");
        Assert.assertNotEquals(duringCall.get(), before,
                "HTTP must run under an Allure step, not the test case");
        Assert.assertEquals(life.getCurrentTestCaseOrStep().orElse(null), before,
                "Allure step must be closed after RestStep returns");
    }

    @Test(groups = {"unit"})
    @Story("Identity pack regenerates once per attempt")
    @Description("Two .regenIdentity() calls in one ctx keep the same owner/member emails.")
    public void regenIdentity_runsOncePerAttempt() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        SoftAssert sa = new SoftAssert();
        Response fake = new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody("{}")
                .build();
        RestStep.exec(ctx, new HashMap<>(), sa, null, "B2B-6801")
                .name("HHonorsEnroll")
                .regenIdentity()
                .expectedStatus(200)
                .post("/realms/guests/enroll", (body, q, h) -> fake);
        String owner = ctx.get("Properties.Email");
        String member = ctx.get("Properties.generatedemailAddress1");
        Assert.assertNotNull(owner);
        Assert.assertNotEquals(owner, member);
        RestStep.exec(ctx, new HashMap<>(), sa, null, "B2B-6801")
                .name("MemberHHonorsEnroll")
                .regenIdentity()
                .expectedStatus(200)
                .post("/realms/guests/enroll", (body, q, h) -> fake);
        Assert.assertEquals(ctx.get("Properties.Email"), owner);
        Assert.assertEquals(ctx.get("Properties.generatedemailAddress1"), member);
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("Enroll 200 overwrites Properties.hhonorsNumber from the response")
    public void exec_post_enrollCapturesHhonorsNumber() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.hhonorsNumber", "111111111");
        SoftAssert sa = new SoftAssert();
        Response fake = new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody("{\"guestId\":\"190099999\",\"hhonorsNumber\":\"2743222453\"}")
                .build();
        RestStep.exec(ctx, new HashMap<>(), sa, null, "B2B-2065")
                .name("HHonorsEnroll")
                .expectedStatus(200)
                .post("/realms/guests/enroll", (body, q, h) -> fake);
        Assert.assertEquals(ctx.get("Properties.hhonorsNumber"), "2743222453");
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("CSV #STEP_Response_field# is published from the JSON body")
    @Description("B2B-2147 expected_..._leadspaceId=#REST_Request_Response_sourceId# must resolve after attest GET.")
    public void exec_get_publishesCsvStepResponseHashFromBody() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        Map<String, String> row = new HashMap<>();
        row.put("expected_http_request_200_2_jsonpath_alternateAccounts_leadspaceId",
                "#REST_Request_Response_sourceId#");
        SoftAssert sa = new SoftAssert();
        Response fake = new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody("{\"sourceId\":\"ls-from-attest\"}")
                .build();
        RestStep.exec(ctx, row, sa, null, "B2B-2147")
                .name("REST_Request")
                .expectedStatus(200)
                .get("/businesses/smb/attest", (body, q, h) -> fake);
        Assert.assertEquals(ctx.get("REST_Request_Response_sourceId"), "ls-from-attest");
        Assert.assertEquals(
                com.ak.api.data.PlaceholderResolver.resolveAll(
                        "#REST_Request_Response_sourceId#", ctx),
                "ls-from-attest");
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("CSV #STEP_RawRequest_field# is published from the resolved body")
    @Description("B2B-450 expected_..._name=#http_request_200_createAccount_RawRequest_contactInfo_address_city# must resolve after create.")
    public void exec_post_publishesCsvStepRawRequestHashFromBody() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        Map<String, String> row = new HashMap<>();
        row.put("expected_http_request_200_jsonpath_name",
                "#http_request_200_createAccount_RawRequest_contactInfo_address_city#");
        SoftAssert sa = new SoftAssert();
        Response fake = new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody("{\"accountId\":\"2000001\"}")
                .build();
        RestStep.exec(ctx, row, sa, null, "B2B-450")
                .name("http_request_200_createAccount")
                .template("templates/programaccountregression/businesses/createaccount_200_040e3c43f9.json")
                .expectedStatus(200)
                .post("/guests/1/businesses", (body, q, h) -> fake);
        Assert.assertEquals(
                ctx.get("http_request_200_createAccount_RawRequest_contactInfo_address_city"),
                "Houston");
        Assert.assertEquals(
                com.ak.api.data.PlaceholderResolver.resolveAll(
                        "#http_request_200_createAccount_RawRequest_contactInfo_address_city#",
                        ctx),
                "Houston");
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("GET businesses stores ReadyAPI http_request_200_2 salesforceId alias")
    public void exec_get_storesSalesforceIdAlias() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        SoftAssert sa = new SoftAssert();
        Response fake = new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody("{\"alternateAccounts\":{\"salesforceId\":\"001abc\"}}")
                .build();
        RestStep.exec(ctx, new HashMap<>(), sa, null, "B2B-5484")
                .name("get_program_account")
                .expectedStatus(200)
                .get("/businesses/2000464021", (body, q, h) -> fake);
        Assert.assertEquals(ctx.get("get_program_account.alternateAccounts_salesforceId"), "001abc");
        Assert.assertEquals(ctx.get("http_request_200_2.alternateAccounts_salesforceId"), "001abc");
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("Empty Salesforce Account path is filled from ctx before fail-fast")
    public void exec_get_fillsEmptySalesforceAccountIdFromCtx() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("http_request_200_2.alternateAccounts_salesforceId", "001xyz");
        SoftAssert sa = new SoftAssert();
        final String[] seen = new String[1];
        Response fake = new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody("{}")
                .build();
        RestStep.exec(ctx, new HashMap<>(), sa, null, "B2B-5484")
                .name("salesforce_account_details_by_ID")
                .expectedStatus(200)
                .get("/data/v55.0/sobjects/Account/", (body, q, h) -> {
                    seen[0] = "called";
                    return fake;
                });
        Assert.assertEquals(seen[0], "called");
        sa.assertAll();
    }
}
