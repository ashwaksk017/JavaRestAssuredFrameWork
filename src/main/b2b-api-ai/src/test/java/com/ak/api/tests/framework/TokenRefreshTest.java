package com.ak.api.tests.framework;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.ak.api.rest.utilities.RestStep;
import com.ak.api.rest.utilities.TokenRefresh;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Token refresh")
public class TokenRefreshTest {

    @AfterMethod(alwaysRun = true)
    public void clearHook() {
        TokenRefresh.clearRefresherOverrideForTest();
        System.clearProperty("auth.tokenRefresh.enabled");
    }

    private static Response json(int status, String body) {
        return new ResponseBuilder()
                .setStatusCode(status)
                .setContentType(ContentType.JSON)
                .setBody(body)
                .build();
    }

    @Test(groups = {"unit"})
    @Story("Matcher: expired / invalid token")
    @Description("401/403 bodies and WWW-Authenticate invalid_token match; member-status 400 and TOTP 401 do not.")
    public void isAuthTokenFailure_matchesTokenErrorsOnly() {
        Assert.assertTrue(TokenRefresh.isAuthTokenFailure(
                json(401, "{\"error\":\"invalid_token\",\"error_description\":\"The access token expired\"}")));
        Assert.assertTrue(TokenRefresh.isAuthTokenFailure(
                json(401, "{\"message\":\"Token expired\"}")));
        Assert.assertTrue(TokenRefresh.isAuthTokenFailure(
                json(403, "{\"fault\":\"invalid token\"}")));
        Assert.assertTrue(TokenRefresh.isAuthTokenFailure(
                json(401, "JWT expired")));

        Response www = new ResponseBuilder()
                .setStatusCode(401)
                .setHeader("WWW-Authenticate",
                        "Bearer error=\"invalid_token\", error_description=\"The access token expired\"")
                .setBody("")
                .build();
        Assert.assertTrue(TokenRefresh.isAuthTokenFailure(www));

        Assert.assertFalse(TokenRefresh.isAuthTokenFailure(
                json(400, "{\"message\":\"Member status is invalid\"}")));
        Assert.assertFalse(TokenRefresh.isAuthTokenFailure(
                json(401, "{\"message\":\"TOTP code is invalid\"}")));
        Assert.assertFalse(TokenRefresh.isAuthTokenFailure(
                json(401, "{\"message\":\"Unauthorized\"}")));
        Assert.assertFalse(TokenRefresh.isAuthTokenFailure(json(200, "{}")));
        Assert.assertFalse(TokenRefresh.isAuthTokenFailure(null));
    }

    @Test(groups = {"unit"})
    @Story("Skip tokenRequest, Salesforce, expected 401")
    @Description("shouldAttempt is false for token fetch, SF steps, negative invalid_token cases, and matching expected status.")
    public void shouldAttempt_skipsTokenFetchSalesforceAndExpected401() {
        Response expired = json(401, "{\"error\":\"invalid_token\"}");
        Assert.assertTrue(TokenRefresh.shouldAttempt("HHonorsEnroll", 200, expired));
        Assert.assertFalse(TokenRefresh.shouldAttempt("tokenRequest", 200, expired));
        Assert.assertFalse(TokenRefresh.shouldAttempt("sf_token_Request", 200, expired));
        Assert.assertFalse(TokenRefresh.shouldAttempt("salesforce_contact_details_by_ID", 200, expired));
        Assert.assertFalse(TokenRefresh.shouldAttempt(
                "http_get_accountBookingMetrics_invalid_token_401", 401, expired));
        Assert.assertFalse(TokenRefresh.shouldAttempt("HHonorsEnroll", 401, expired));
        Assert.assertFalse(TokenRefresh.shouldAttempt("HHonorsEnroll", 200,
                json(400, "Member status is invalid")));
    }

    @Test(groups = {"unit"})
    @Story("Config kill switch")
    @Description("-Dauth.tokenRefresh.enabled=false disables shouldAttempt.")
    public void shouldAttempt_honorsEnabledFlag() {
        System.setProperty("auth.tokenRefresh.enabled", "false");
        Response expired = json(401, "{\"error\":\"invalid_token\"}");
        Assert.assertFalse(TokenRefresh.shouldAttempt("HHonorsEnroll", 200, expired));
    }

    @Test(groups = {"unit"})
    @Story("RestStep retries once after refresh")
    @Description("A 401 invalid_token on a business step refreshes ctx and retries; the status assert sees the second 200.")
    public void restStep_retriesBusinessCallAfterTokenRefresh() throws Exception {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("tokenId.GeneratedTokenID", "Bearer stale");
        AtomicInteger calls = new AtomicInteger();
        AtomicBoolean refreshed = new AtomicBoolean();
        TokenRefresh.overrideRefresherForTest(c -> {
            c.put("tokenId.GeneratedTokenID", "Bearer fresh");
            refreshed.set(true);
            return true;
        });
        SoftAssert sa = new SoftAssert();
        Response out = RestStep.exec(ctx, new HashMap<>(), sa, null, "B2B-token")
                .name("HHonorsEnroll")
                .expectedStatus(200)
                .post("/realms/guests/enroll", (body, q, h) -> {
                    int n = calls.incrementAndGet();
                    if (n == 1) {
                        return json(401, "{\"error\":\"invalid_token\",\"error_description\":\"token expired\"}");
                    }
                    Assert.assertEquals(ctx.get("tokenId.GeneratedTokenID"), "Bearer fresh");
                    return json(200, "{\"guestId\":\"1\"}");
                });
        Assert.assertEquals(out.getStatusCode(), 200);
        Assert.assertEquals(calls.get(), 2);
        Assert.assertTrue(refreshed.get());
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("Negative 401 is not refreshed")
    @Description("Expected HTTP 401 does not invoke the refresher.")
    public void restStep_doesNotRefreshWhen401IsExpected() throws Exception {
        AtomicInteger refreshes = new AtomicInteger();
        TokenRefresh.overrideRefresherForTest(c -> {
            refreshes.incrementAndGet();
            return true;
        });
        SoftAssert sa = new SoftAssert();
        Response out = RestStep.exec(new HashMap<>(), new HashMap<>(), sa, null, "B2B-neg")
                .name("http_get_accountBookingMetrics_invalid_token_401")
                .expectedStatus(401)
                .get("/metrics", (body, q, h) ->
                        json(401, "{\"error\":\"invalid_token\"}"));
        Assert.assertEquals(out.getStatusCode(), 401);
        Assert.assertEquals(refreshes.get(), 0);
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("Failed refresh keeps original 401")
    @Description("If refreshHiltonToken returns false, RestStep does not retry the business call.")
    public void restStep_keepsOriginalWhenRefreshFails() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        TokenRefresh.overrideRefresherForTest(c -> false);
        SoftAssert sa = new SoftAssert();
        Response out = RestStep.exec(new HashMap<>(), new HashMap<>(), sa, null, "B2B-fail")
                .name("CreateProgramAccount")
                .expectedStatus(200)
                .post("/businesses", (body, q, h) -> {
                    calls.incrementAndGet();
                    return json(401, "Token has expired");
                });
        Assert.assertEquals(out.getStatusCode(), 401);
        Assert.assertEquals(calls.get(), 1);
    }
}
