package com.ak.api.dsl;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.ak.api.context.ScenarioContext;
import com.ak.api.dsl.CustomerOnboarding.Capture;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.response.Response;

/**
 * Capturing response values into ctx from a hand-written chain.
 *
 * <p>No HTTP: responses are fabricated with {@link ResponseBuilder}, the same
 * way {@code ResponseAssertsTest} and {@code RestStepTest} do.</p>
 */
@Epic("Manual")
@Feature("Value capture")
public class PhaseCaptureTest {

    private static Response json(String body) {
        return new ResponseBuilder()
                .setStatusCode(200)
                .setContentType("application/json")
                .setBody(body)
                .build();
    }

    private static List<Capture> queue(Capture... items) {
        List<Capture> out = new ArrayList<>();
        for (Capture c : items) {
            out.add(c);
        }
        return out;
    }

    @Test(groups = {"unit"})
    @Story("a response value lands in ctx under the given key")
    public void capturesIntoARawKey() {
        Map<String, String> ctx = new LinkedHashMap<>();
        CustomerOnboarding.applyCaptures(
                json("{\"guestId\":\"12345\"}"), ctx,
                queue(Capture.toKey("guestId", "Properties.guestID")));

        Assert.assertEquals(ctx.get("Properties.guestID"), "12345",
                String.valueOf(ctx));
    }

    @Test(groups = {"unit"})
    @Story("putExtracted publishes a trailing-case alias for a d/D key")
    @Description("""
            Imported steps read both spellings, so a captured id must be
            findable under either -- this is why capture routes through
            putExtracted rather than a bare ctx.put.

            Narrow by design: flipTrailingCase only flips a trailing d/D and
            returns null otherwise, so a key ending in anything else gets no
            alias. That limit is the argument for the declared-field form.
            """)
    public void capturePublishesTheTrailingCaseAlias() {
        Map<String, String> ctx = new LinkedHashMap<>();
        CustomerOnboarding.applyCaptures(
                json("{\"guestId\":\"12345\"}"), ctx,
                queue(Capture.toKey("guestId", "Properties.guestID")));

        Assert.assertTrue(ctx.containsKey("Properties.guestID"), String.valueOf(ctx));
        Assert.assertTrue(ctx.containsKey("Properties.guestId"), String.valueOf(ctx));
    }

    @Test(groups = {"unit"})
    @Story("a capture overwrites a stale value")
    @Description("""
            putIfNonEmpty is putIfAbsent, which would let a generated default
            beat the value the server just returned. Capture must overwrite.
            """)
    public void captureOverwritesAStaleValue() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("Properties.guestID", "999-stale");

        CustomerOnboarding.applyCaptures(
                json("{\"guestId\":\"12345\"}"), ctx,
                queue(Capture.toKey("guestId", "Properties.guestID")));

        Assert.assertEquals(ctx.get("Properties.guestID"), "12345",
                "the fresh value must win");
    }

    @Test(groups = {"unit"})
    @Story("a missing path leaves ctx untouched")
    @Description("""
            A failed extract must not blank a value an earlier phase published;
            that would turn one bad response into a confusing downstream 404.
            """)
    public void missingPathDoesNotBlankAnExistingValue() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("Properties.guestID", "12345");

        CustomerOnboarding.applyCaptures(
                json("{\"somethingElse\":\"x\"}"), ctx,
                queue(Capture.toKey("guestId", "Properties.guestID")));

        Assert.assertEquals(ctx.get("Properties.guestID"), "12345",
                "an absent path must not overwrite");
    }

    @Test(groups = {"unit"})
    @Story("a typed capture is readable through the declared field")
    public void capturesIntoADeclaredField() {
        Map<String, String> ctx = new LinkedHashMap<>();
        CustomerOnboarding.applyCaptures(
                json("{\"accountId\":\"778899\"}"), ctx,
                queue(Capture.toField("accountId", ScenarioContext.ACCOUNT_ID)));

        Assert.assertEquals(ScenarioContext.of(ctx).requireAccountId(), 778899L,
                String.valueOf(ctx));
    }

    @Test(groups = {"unit"})
    @Story("nothing queued writes nothing")
    public void anEmptyQueueWritesNothing() {
        Map<String, String> ctx = new LinkedHashMap<>();
        CustomerOnboarding.applyCaptures(
                json("{\"guestId\":\"12345\"}"), ctx, new ArrayList<>());

        Assert.assertTrue(ctx.isEmpty(), String.valueOf(ctx));
    }
}
