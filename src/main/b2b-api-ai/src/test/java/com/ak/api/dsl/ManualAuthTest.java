package com.ak.api.dsl;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import com.ak.api.context.ScenarioContext;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Auth and cleanup for HAND-WRITTEN chains.
 *
 * <h2>The bug these pin down</h2>
 *
 * <p>A hand-written chain runs no {@code tokenRequest} step, so nothing ever
 * wrote {@code tokenId.GeneratedTokenID} -- the only alias {@code API_TOKEN}
 * had. Meanwhile {@code AuthHelper.primeClientCredentialsToken} writes
 * {@code accessToken}, and {@code ImportedScenario.begin()} preserves that
 * same key. The writer and the reader were wired to DIFFERENT keys, so
 * {@code token()} returned {@code ""} and every phase sent an empty bearer --
 * silently, because {@code rawOf} returns empty rather than failing and the
 * generated clients set the header to whatever they are handed.</p>
 *
 * <h2>Why every case sets its own properties</h2>
 *
 * <p>None of these rely on a property being ABSENT. A tree configured with
 * real credentials would then behave differently from one carrying
 * {@code __SET_ME__} placeholders, and the test would pass on one machine and
 * fail on another. That exact mistake made an earlier guard fail on a
 * colleague's box and pass on mine.</p>
 */
@Epic("Manual")
@Feature("Auth and cleanup")
public class ManualAuthTest {

    private static final String CLIENT_ID = "api_config.client_id";
    private static final String CLIENT_SECRET = "api_config.client_secret";

    @AfterMethod(alwaysRun = true)
    public void clearOverrides() {
        System.clearProperty(CLIENT_ID);
        System.clearProperty(CLIENT_SECRET);
        System.clearProperty("manual.client");
    }

    private static Map<String, String> ctx() {
        return new LinkedHashMap<>();
    }

    // -----------------------------------------------------------------
    // The alias
    // -----------------------------------------------------------------

    @Test(groups = {"unit"})
    @Story("apiToken() falls back to the key the priming helper writes")
    @Description("""
            Without this alias the DSL could never see a primed token, because
            AuthHelper writes `accessToken` and API_TOKEN only listed
            `tokenId.GeneratedTokenID`.
            """)
    public void apiTokenFallsBackToAccessToken() {
        Map<String, String> ctx = ctx();
        ctx.put("accessToken", "abc123");

        Assert.assertEquals(ScenarioContext.of(ctx).apiToken(), "abc123",
                "apiToken() must resolve the accessToken key");
    }

    @Test(groups = {"unit"})
    @Story("the generated-token key still wins")
    @Description("""
            The alias is a FALLBACK. tokenId.GeneratedTokenID is what an
            imported tokenRequest step extracts and what TokenRefresh writes
            back, and it carries the Bearer prefix -- it must keep priority.
            """)
    public void generatedTokenKeyStillWins() {
        Map<String, String> ctx = ctx();
        ctx.put("tokenId.GeneratedTokenID", "Bearer fromTokenRequest");
        ctx.put("accessToken", "rawFallback");

        Assert.assertEquals(ScenarioContext.of(ctx).apiToken(),
                "Bearer fromTokenRequest",
                "the extracted token must outrank the primed fallback");
    }

    // -----------------------------------------------------------------
    // The priming gate
    // -----------------------------------------------------------------

    @Test(groups = {"unit"})
    @Story("priming is skipped when the chain already has a token")
    @Description("An imported flow that fetched its own token must be untouched.")
    public void primingIsSkippedWhenATokenIsAlreadyPresent() {
        Map<String, String> ctx = ctx();
        ctx.put("tokenId.GeneratedTokenID", "Bearer already");
        Map<String, String> before = new HashMap<>(ctx);

        CustomerOnboarding.primeTokenIfAbsent(ctx);

        Assert.assertEquals(ctx, before, "priming must be a no-op here");
    }

    @Test(groups = {"unit"})
    @Story("NEGATIVE CONTROL: placeholder credentials never reach the wire")
    @Description("""
            primeClientCredentialsToken only checks isEmpty(), so `__SET_ME__`
            would otherwise be POSTed at the token endpoint from a
            default-configured tree. The Config.isUnset gate is what keeps this
            suite offline -- if it regresses, this test starts making a real
            network call.
            """)
    public void placeholderCredentialsDoNotTriggerAFetch() {
        System.setProperty(CLIENT_ID, "__SET_ME__");
        System.setProperty(CLIENT_SECRET, "__SET_ME__");
        Map<String, String> ctx = ctx();

        CustomerOnboarding.primeTokenIfAbsent(ctx);

        Assert.assertTrue(ScenarioContext.of(ctx).apiToken().isEmpty(),
                "no token should have been fetched or stored");
        Assert.assertFalse(ctx.containsKey("accessToken"),
                "placeholder credentials must not produce an accessToken");
    }

    @Test(groups = {"unit"})
    @Story("an empty credential is treated the same as a placeholder")
    public void emptyCredentialsDoNotTriggerAFetch() {
        System.setProperty(CLIENT_ID, "");
        System.setProperty(CLIENT_SECRET, "");
        Map<String, String> ctx = ctx();

        CustomerOnboarding.primeTokenIfAbsent(ctx);

        Assert.assertTrue(ScenarioContext.of(ctx).apiToken().isEmpty());
    }

    @Test(groups = {"unit"})
    @Story("a null ctx is tolerated")
    public void nullCtxIsTolerated() {
        CustomerOnboarding.primeTokenIfAbsent(null);
    }

    // -----------------------------------------------------------------
    // Cleanup delegation
    // -----------------------------------------------------------------

    @Test(groups = {"unit"})
    @Story("cleanup is fail-soft when no generated SuiteCleanup resolves")
    @Description("""
            Cleanup runs in @AfterMethod. Throwing there would mask the real
            test result, so an unresolvable suite must warn and return.
            """)
    public void cleanupIsFailSoftWhenNothingResolves() {
        System.clearProperty("manual.client");
        ManualCleanup.afterEachTest(ctx());
    }

    @Test(groups = {"unit"})
    @Story("cleanup is fail-soft for a client whose suite was never converted")
    public void cleanupIsFailSoftForAnUnconvertedClient() {
        System.setProperty("manual.client", "TotallyUnconvertedClient");
        ManualCleanup.afterEachTest(ctx());
    }

    @Test(groups = {"unit"})
    @Story("cleanup tolerates a null ctx")
    public void cleanupToleratesNullCtx() {
        System.clearProperty("manual.client");
        ManualCleanup.afterEachTest(null);
    }
}
