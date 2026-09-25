package com.hi.api.tests.framework;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Random;
import java.util.Set;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.hi.api.auth.TokenCache;
import com.hi.api.rest.utilities.AuthDiagnostics;
import com.hi.api.rest.utilities.AuthHelper;
import com.hi.api.rest.utilities.StepOutcomes;
import com.hi.api.support.CtxFields;
import com.hi.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

/**
 * The failure digest must report what actually went wrong, and a token
 * the server rejected must not come back after the cache drops it.
 */
@Epic("API Automation")
@Feature("Failure digest accuracy")
public class AuthDigestAccuracyTest {

    @AfterMethod(alwaysRun = true)
    public void reset() {
        StepOutcomes.reset();
        AuthDiagnostics.clearExpectedStatus();
        TokenCache.resetForTest();
        System.clearProperty("ALLOWED_DOMAINS");
    }

    private static Response json(int status, String body) {
        return new ResponseBuilder()
                .setStatusCode(status)
                .setContentType(ContentType.JSON)
                .setBody(body)
                .build();
    }

    @Test(groups = {"unit"})
    @Story("First failing call")
    @Description("A step that asked for 400 and got 400 passed -- it is not an upstream failure.")
    public void stepOutcomes_ignoresAssertedStatus() {
        StepOutcomes.record("createMember_400", 400, 400);
        Assert.assertNull(StepOutcomes.lastFailure());

        StepOutcomes.record("createMember", 400, 200);
        Assert.assertEquals(StepOutcomes.lastFailure(), "createMember -> HTTP 400");

        StepOutcomes.reset();
        StepOutcomes.record("noExpectation", 404, -1);
        Assert.assertEquals(StepOutcomes.lastFailure(), "noExpectation -> HTTP 404");
    }

    @Test(groups = {"unit"})
    @Story("Auth rejections")
    @Description("The filter can tell a negative auth test from a rejected token.")
    public void expectedStatus_isVisibleOnlyWhileSet() {
        Assert.assertFalse(AuthDiagnostics.isExpectedStatus(403));
        AuthDiagnostics.expectStatus(403);
        Assert.assertTrue(AuthDiagnostics.isExpectedStatus(403));
        Assert.assertFalse(AuthDiagnostics.isExpectedStatus(401));
        AuthDiagnostics.clearExpectedStatus();
        Assert.assertFalse(AuthDiagnostics.isExpectedStatus(403));
    }

    @Test(groups = {"unit"})
    @Story("Token churn")
    @Description("401 drops the token; a permission 403 does not; a token-worded 403 does; asserted 401/403 never.")
    public void deadTokenSignal_onlyForRealTokenFailures() {
        Assert.assertTrue(AuthDiagnostics.isDeadTokenSignal(json(401, "{}"), 200));
        Assert.assertFalse(AuthDiagnostics.isDeadTokenSignal(
                json(403, "{\"message\":\"Member is not allowed\"}"), 200));
        Assert.assertTrue(AuthDiagnostics.isDeadTokenSignal(
                json(403, "{\"error\":\"invalid_token\"}"), 200));
        Assert.assertFalse(AuthDiagnostics.isDeadTokenSignal(json(401, "{}"), 401));
        Assert.assertFalse(AuthDiagnostics.isDeadTokenSignal(json(401, "{}"), 403));
        Assert.assertFalse(AuthDiagnostics.isDeadTokenSignal(null, 200));
    }

    @Test(groups = {"unit"})
    @Story("Token churn")
    @Description("After a rejection clears the cache, the ctx copy of that token must not re-seed it.")
    public void authHelper_doesNotReseedRejectedToken() {
        TokenCache.putAccessToken("dead-token");
        TokenCache.markRejected("Bearer dead-token");
        TokenCache.clear();
        Map<String, String> ctx = new HashMap<>();
        ctx.put("accessToken", "dead-token");
        AuthHelper.primeClientCredentialsToken(ctx);
        Assert.assertNull(TokenCache.getAccessToken(),
                "the rejected token came back into the cache");
    }

    @Test(groups = {"unit"})
    @Story("Token churn")
    @Description("A cache hit replaces a REJECTED ctx token; a live one is left alone.")
    public void applyToCtx_replacesOnlyRejectedToken() {
        TokenCache.putAccessToken("fresh-token");
        TokenCache.markRejected("Bearer dead-token");
        Map<String, String> ctx = new HashMap<>();
        ctx.put("accessToken", "dead-token");
        ctx.put("tokenId.GeneratedTokenID", "Bearer dead-token");
        TokenCache.applyToCtx(ctx);
        Assert.assertEquals(ctx.get("accessToken"), "fresh-token");
        Assert.assertEquals(ctx.get("tokenId.GeneratedTokenID"), "Bearer fresh-token");

        ctx.put("tokenId.GeneratedTokenID", "Bearer live-token");
        TokenCache.applyToCtx(ctx);
        Assert.assertEquals(ctx.get("tokenId.GeneratedTokenID"), "Bearer live-token");
    }

    @Test(groups = {"unit"})
    @Story("Allowed domains")
    @Description("Pick mirrors ReadyAPI: last entry never chosen when there are several; www. and blanks normalized.")
    public void pickAllowedDomain_mirrorsReadyApi() {
        Random rnd = new Random(7);
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < 200; i++) {
            seen.add(CtxFields.pickAllowedDomain("a.com, www.B.com ,c.com", rnd));
        }
        Assert.assertEquals(seen, Set.of("a.com", "b.com"));
        Assert.assertEquals(CtxFields.pickAllowedDomain("only.com", rnd), "only.com");
        Assert.assertNull(CtxFields.pickAllowedDomain("", rnd));
        Assert.assertNull(CtxFields.pickAllowedDomain(" , ", rnd));
        Assert.assertNull(CtxFields.pickAllowedDomain(null, rnd));
    }

    @Test(groups = {"unit"})
    @Story("Allowed domains")
    @Description("Identity regen builds domain and emails on ALLOWED_DOMAINS when it is configured.")
    public void regen_usesAllowedDomainWhenConfigured() {
        System.setProperty("ALLOWED_DOMAINS", "allowed-one.com");
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx, new HashMap<>());
        Assert.assertEquals(ctx.get("Properties.Domain"), "allowed-one.com");
        Assert.assertEquals(ctx.get("Properties.websiteDomain"), "allowed-one.com");
        Assert.assertTrue(ctx.get("Properties.generatedemailAddress").endsWith("@allowed-one.com"),
                ctx.get("Properties.generatedemailAddress"));
        Assert.assertTrue(ctx.get("Properties.EmailMember").endsWith("@allowed-one.com"),
                ctx.get("Properties.EmailMember"));
    }

    @Test(groups = {"unit"})
    @Story("Allowed domains")
    @Description("A frozen Hardcodeddomain still wins over ALLOWED_DOMAINS.")
    public void regen_keepsFrozenDomain() {
        System.setProperty("ALLOWED_DOMAINS", "allowed-one.com");
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Hardcodeddomain", "frozen.com");
        ImportedScenario.regenRandomProperties(ctx, new HashMap<>());
        Assert.assertEquals(ctx.get("Properties.Domain"), "frozen.com");
    }
}
