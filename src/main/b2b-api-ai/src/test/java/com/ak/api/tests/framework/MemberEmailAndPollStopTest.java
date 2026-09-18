package com.ak.api.tests.framework;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.ak.api.rest.utilities.RestStep;
import com.ak.api.retry.Poller;
import com.ak.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

/**
 * B2B-6860: CreatePendingAccountmember 400 code 509 "The email address is
 * already in use by a member", re-sent four times by the memberId poll.
 */
@Epic("API Automation")
@Feature("Member email identity and poll stop")
public class MemberEmailAndPollStopTest {

    private static Response json(int status, String body) {
        return new ResponseBuilder()
                .setStatusCode(status)
                .setContentType(ContentType.JSON)
                .setBody(body)
                .build();
    }

    @Test(groups = {"unit"})
    @Story("generatedemailAddress follows the case")
    @Description("Saved Email and generatedemailAddress differ in the row -> they differ in ctx, same domain.")
    public void regen_splitsGeneratedEmailWhenRowDoes() {
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Email", "wx46p@zaxbys.com");
        row.put("Properties.generatedemailAddress", "mrmar@zaxbys.com");
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx, row);

        String owner = ctx.get("Properties.Email");
        String generated = ctx.get("Properties.generatedemailAddress");
        Assert.assertNotEquals(generated, owner,
                "member-create emailAddress must not be the account owner's email");
        Assert.assertNotEquals(generated, ctx.get("Properties.EmailMember"));
        Assert.assertEquals(ctx.get("Properties.GeneratedemailAddress"), generated);
        String domain = owner.substring(owner.indexOf('@'));
        Assert.assertTrue(generated.endsWith(domain), generated + " vs " + owner);
        Assert.assertEquals(ctx.get("Properties.GeneratedEmail"), owner,
                "only generatedemailAddress moves; the owner aliases stay");
    }

    @Test(groups = {"unit"})
    @Story("generatedemailAddress follows the case")
    @Description("Saved values equal (script used one user) or missing -> keep one owner email.")
    public void regen_keepsOneEmailWhenRowDoesNotSplit() {
        Map<String, String> same = new HashMap<>();
        same.put("Properties.Email", "abc@foliera.com");
        same.put("Properties.generatedemailAddress", "ABC@foliera.com");
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx, same);
        Assert.assertEquals(ctx.get("Properties.generatedemailAddress"), ctx.get("Properties.Email"));

        Map<String, String> ctx2 = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx2, new HashMap<>());
        Assert.assertEquals(ctx2.get("Properties.generatedemailAddress"), ctx2.get("Properties.Email"));

        // A saved Email with no '@' is the author's literal (the template
        // reads it as a DOMAIN: emailDomains: ["${Properties#Email}"]), and
        // the generatedemailAddress saved beside it is kept with it.
        Map<String, String> notEmails = new HashMap<>();
        notEmails.put("Properties.Email", "linkedin.com");
        notEmails.put("Properties.generatedemailAddress", "x@foliera.com");
        Map<String, String> ctx3 = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx3, notEmails);
        Assert.assertEquals(ctx3.get("Properties.Email"), "linkedin.com");
        Assert.assertEquals(ctx3.get("Properties.generatedemailAddress"), "x@foliera.com");
    }

    @Test(groups = {"unit"})
    @Story("Poll stop")
    @Description("POST 4xx that cannot change stops polling; transient ones, 5xx and GET do not.")
    public void settledFailure_rules() {
        Response inUse = json(400, "{\"notifications\":[{\"code\":\"509\","
                + "\"message\":\"The email address is already in use by a member\"}]}");
        Assert.assertTrue(RestStep.isSettledFailure("POST", inUse));
        Assert.assertTrue(RestStep.isSettledFailure("post", json(404, "{}")));
        Assert.assertTrue(RestStep.isSettledFailure("POST", json(401, "{\"error\":\"invalid_token\"}")));

        Assert.assertFalse(RestStep.isSettledFailure("POST",
                json(400, "{\"message\":\"Member status is invalid\"}")));
        Assert.assertFalse(RestStep.isSettledFailure("POST", json(429, "{}")));
        Assert.assertFalse(RestStep.isSettledFailure("POST", json(408, "{}")));
        Assert.assertFalse(RestStep.isSettledFailure("POST", json(503, "{}")));
        Assert.assertFalse(RestStep.isSettledFailure("POST", json(200, "{}")));
        Assert.assertFalse(RestStep.isSettledFailure("GET", json(404, "{}")));
        Assert.assertFalse(RestStep.isSettledFailure("POST", null));
    }

    @Test(groups = {"unit"})
    @Story("Poll stop")
    @Description("A settled result is returned after one call instead of re-sending until the timeout.")
    public void poller_stopsOnSettledResult() {
        AtomicInteger calls = new AtomicInteger();
        Response res = Poller.until(() -> {
            calls.incrementAndGet();
            return json(400, "{\"message\":\"The email address is already in use by a member\"}");
        }, r -> false, r -> RestStep.isSettledFailure("POST", r), 2_000, 50, "memberId non-empty");
        Assert.assertEquals(calls.get(), 1);
        Assert.assertEquals(res.getStatusCode(), 400);

        AtomicInteger throttled = new AtomicInteger();
        Poller.until(() -> {
            throttled.incrementAndGet();
            return json(429, "{}");
        }, r -> false, r -> RestStep.isSettledFailure("POST", r), 300, 50, "memberId non-empty");
        Assert.assertTrue(throttled.get() > 1, "a throttled POST should still be polled");
    }

    @Test(groups = {"unit"})
    @Story("activate polls follow the URL")
    @Description("B2B-5530: the activate wait read account 1 / a random id from ctx instead of the account in the URL.")
    public void activatePollAccountComesFromTheActivateUrl() {
        Assert.assertEquals(RestStep.accountIdFromActivateUrl(
                "https://host/v2/businesses/2000482207/activate"), "2000482207");
        Assert.assertEquals(RestStep.accountIdFromActivateUrl(
                "/businesses/2000482207/activate?dryRun=false"), "2000482207");
        Assert.assertEquals(RestStep.accountIdFromActivateUrl("/businesses//activate"), "");
        Assert.assertEquals(RestStep.accountIdFromActivateUrl("/businesses/2000482207"), "");
        Assert.assertEquals(RestStep.accountIdFromActivateUrl(
                "/guests/1/businesses/2/members/3/activate"), "");
        Assert.assertEquals(RestStep.accountIdFromActivateUrl(null), "");
    }
}
