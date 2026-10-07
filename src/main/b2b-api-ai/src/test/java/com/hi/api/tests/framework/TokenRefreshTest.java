package com.hi.api.tests.framework;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.hi.api.rest.utilities.RestStep;
import com.hi.api.rest.utilities.TokenRefresh;

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

    @Test(groups = {"unit"})
    @Story("A plain 401 refreshes and retries")
    @Description("""
            The gap this closes. The cache-clear path treated ANY unexpected
            401 as a dead token, but shouldAttempt demanded the body name the
            token. A gateway answering 401 with a generic Fault cleared the
            cache and never re-issued the call, so the test still failed.
            """)
    public void shouldAttempt_onAPlain401WithNoTokenPhrase() {
        Assert.assertTrue(TokenRefresh.shouldAttempt("HHonorsEnroll", 200,
                json(401, "{\"Fault\":{\"code\":401,\"message\":\"Unauthorized\"}}")),
                "an unexpected 401 must refresh and retry even with a generic body");
        Assert.assertTrue(TokenRefresh.shouldAttempt("HHonorsEnroll", 200,
                json(401, "")),
                "an unexpected 401 with an empty body must still refresh");
    }

    @Test(groups = {"unit"})
    @Story("A plain 403 stays a permission denial")
    @Description("Refreshing a token cannot fix a scope/permission problem, so it must not retry.")
    public void shouldAttempt_leavesAPlain403Alone() {
        Assert.assertFalse(TokenRefresh.shouldAttempt("HHonorsEnroll", 200,
                json(403, "{\"Fault\":{\"code\":403,\"message\":\"Forbidden\"}}")),
                "a plain 403 is a permission denial, not a dead token");
        // control: a 403 that DOES name the token is still a refresh
        Assert.assertTrue(TokenRefresh.shouldAttempt("HHonorsEnroll", 200,
                json(403, "{\"error\":\"token expired\"}")),
                "a 403 naming the token must still refresh");
    }

    @Test(groups = {"unit"})
    @Story("Negative auth tests are never retried")
    @Description("A case that EXPECTS 401/403 must not have its token refreshed underneath it.")
    public void shouldAttempt_skipsTestsThatExpectAnAuthFailure() {
        Response plain401 = json(401, "{\"message\":\"Unauthorized\"}");
        Assert.assertFalse(TokenRefresh.shouldAttempt("SomeStep", 401, plain401));
        Assert.assertFalse(TokenRefresh.shouldAttempt("SomeStep", 403, plain401));
    }

    // --- the auth server re-issuing the SAME token -------------------
    //
    // A client-credentials grant is not a refresh-token exchange: most
    // servers hand back the identical access token until it expires. On
    // one full run 1,229 of 1,230 refreshes produced a token the cache
    // already knew was rejected, and every retry then failed. Worse, the
    // log read "regenerating ... then retrying", so the following 401
    // looked like bad credentials rather than an unchanged token sent to
    // the wrong audience.

    @Test(groups = {"framework", "auth"})
    @Story("A re-issued token is recognised, not retried")
    @Description("TokenCache knows the token was rejected; refreshing to "
            + "the same value cannot help and must not be presented again.")
    public void rejectedTokenIsRecognisedWhenTheServerReissuesIt() {
        String token = "same-token-value-aaaaaaaaaaaa";
        com.hi.api.auth.TokenCache.markRejected(token);
        Assert.assertTrue(com.hi.api.auth.TokenCache.isRejected(token),
                "the cache must remember a rejected token for the refresh "
                + "path to be able to notice a re-issue");
    }

    @Test(groups = {"framework", "auth"})
    @Story("A genuinely new token is not mistaken for a re-issue")
    @Description("The guard must not block a refresh that DID change the "
            + "token, or every real refresh stops working.")
    public void aDifferentTokenIsNotTreatedAsRejected() {
        com.hi.api.auth.TokenCache.markRejected("old-token-value-bbbbbbbb");
        Assert.assertFalse(
                com.hi.api.auth.TokenCache.isRejected("new-token-value-cccccccc"),
                "a new token value must still be usable");
    }

    @Test(groups = {"framework", "auth"})
    @Story("The bearer prefix does not hide a re-issue")
    @Description("ctx stores the token WITH the scheme; the cache stores it "
            + "without. Comparing the wrong one would miss the re-issue.")
    public void theSchemePrefixDoesNotHideARejectedToken() {
        String raw = "prefixed-token-value-dddddddd";
        com.hi.api.auth.TokenCache.markRejected(raw);
        String withScheme = "Bearer " + raw;
        String stripped = withScheme.startsWith("Bearer ")
                ? withScheme.substring("Bearer ".length()) : withScheme;
        Assert.assertTrue(com.hi.api.auth.TokenCache.isRejected(stripped));
    }

    // --- skipped checks have to be countable -------------------------
    //
    // Skipping an assertion whose expected value is still a placeholder
    // is right: it could only ever be false, and reporting it was 24
    // fake failures in one run. But the run then says PASSED for a test
    // that verified almost nothing. One run skipped 246 checks and
    // reported 20 passes, and the only trace was 246 WARN lines in a
    // 20 MB log.

    @Test(groups = {"framework", "reporting"})
    @Story("A skipped check is counted, not just logged")
    @Description("A green run that checked nothing is the most expensive "
            + "kind of green, so the count must reach the summary.")
    public void skippedChecksAreCounted() {
        com.hi.api.rest.utilities.ResponseAsserts.resetSkippedCountsForTest();
        Assert.assertEquals(
                com.hi.api.rest.utilities.ResponseAsserts.skippedTotal(), 0);

        SoftAssert sa = new SoftAssert();
        Response res = json(200, "{\"propCode\":\"ABC\"}");
        com.hi.api.rest.utilities.ResponseAsserts.valueInResponse(
                sa, res, "#DataSource_propCode#", "propCode", "JsonPath Match");

        Assert.assertEquals(
                com.hi.api.rest.utilities.ResponseAsserts.skippedTotal(), 1,
                "an assertion skipped for an unresolved placeholder must be "
                + "counted, or the summary overstates what ran");
        Assert.assertTrue(
                com.hi.api.rest.utilities.ResponseAsserts.skippedCounts()
                        .keySet().stream()
                        .anyMatch(k -> k.contains("placeholder")),
                "the count must say WHY, so the summary can be acted on");
        sa.assertAll();   // the skip must not have recorded a failure
    }

    @Test(groups = {"framework", "reporting"})
    @Story("A check that DOES run is not counted as skipped")
    @Description("Otherwise the number is noise.")
    public void aRealAssertionIsNotCountedAsSkipped() {
        com.hi.api.rest.utilities.ResponseAsserts.resetSkippedCountsForTest();
        SoftAssert sa = new SoftAssert();
        Response res = json(200, "{\"propCode\":\"ABC\"}");
        com.hi.api.rest.utilities.ResponseAsserts.valueInResponse(
                sa, res, "ABC", "propCode", "JsonPath Match");
        Assert.assertEquals(
                com.hi.api.rest.utilities.ResponseAsserts.skippedTotal(), 0);
        sa.assertAll();
    }
}
