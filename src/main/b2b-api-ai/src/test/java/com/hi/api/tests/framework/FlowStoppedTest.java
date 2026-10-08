package com.hi.api.tests.framework;

import java.lang.reflect.Proxy;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

import org.testng.Assert;
import org.testng.ITestClass;
import org.testng.ITestNGMethod;
import org.testng.ITestResult;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.hi.api.rest.utilities.FlowStopped;
import com.hi.api.rest.utilities.RestStep;
import com.hi.api.retry.RetryAnalyzer;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

/**
 * A case ends at the write the server refused, instead of running its
 * remaining steps against ids that were never created.
 *
 * <p>From one leadspace run: 27 creates answered 400, and each test went on
 * to skip a passcode query, post last run's passcode to last run's account,
 * poll 14 times for an id on an account that did not exist -- and was then
 * run twice more by the retry analyzer for the same 400.</p>
 */
@Epic("Framework")
@Feature("Flow stops at a rejected write")
public class FlowStoppedTest {

    private static final String DOMAIN_400 = "{\"code\":997,\"message\":\"Bad Request\","
            + "\"notifications\":[{\"code\":\"503\",\"fields\":[\"emailAddress\"],"
            + "\"message\":\"Email address domain must match an allowed domain\"}]}";

    @AfterMethod(alwaysRun = true)
    public void clear() {
        System.clearProperty("test.stopAfterRejectedWrite");
        System.clearProperty("test.retryRejectedWrite");
        System.clearProperty("wholeTestRetry");
        RetryAnalyzer.resetAttempts();
    }

    private static Response json(int status, String body) {
        return new ResponseBuilder().setStatusCode(status)
                .setContentType(ContentType.JSON).setBody(body).build();
    }

    private static Response post(Map<String, String> row, SoftAssert sa, String step,
                                 int expected, Response answer, AtomicInteger sent)
            throws Exception {
        return RestStep.exec(new HashMap<>(), row, sa, null, "B2B-8784")
                .name(step)
                .expectedStatus(expected)
                .post("/guests/1/businesses", (body, q, h) -> {
                    sent.incrementAndGet();
                    return answer;
                });
    }

    @Test(groups = {"unit"})
    @Story("A rejected create ends the case")
    @Description("POST expecting 200 answered 400 -> FlowStopped, carrying the status assertion's wording and the server's reason.")
    public void rejectedCreate_stopsTheFlow() throws Exception {
        AtomicInteger sent = new AtomicInteger();
        try {
            post(new HashMap<>(), new SoftAssert(), "http_request_200_1", 200,
                    json(400, DOMAIN_400), sent);
            Assert.fail("the flow went on past a rejected create");
        } catch (FlowStopped stop) {
            Assert.assertEquals(stop.step(), "http_request_200_1");
            Assert.assertEquals(stop.status(), 400);
            Assert.assertTrue(stop.getMessage().startsWith(
                    "expected status for http_request_200_1 expected [200] but found [400]"),
                    stop.getMessage());
            Assert.assertTrue(stop.getMessage().contains("must match an allowed domain"),
                    "the server's reason belongs in the failure: " + stop.getMessage());
            Assert.assertFalse(stop.worthRetrying());
        }
        Assert.assertEquals(sent.get(), 1, "a 400 must not be re-sent");
    }

    @Test(groups = {"unit"})
    @Story("A rejected create ends the case")
    @Description("NEGATIVE CONTROL: the switch off restores the old behaviour -- the response comes back and only the soft assertion records the failure.")
    public void switchedOff_theFlowContinues() throws Exception {
        System.setProperty("test.stopAfterRejectedWrite", "false");
        SoftAssert sa = new SoftAssert();
        Response res = post(new HashMap<>(), sa, "http_request_200_1", 200,
                json(400, DOMAIN_400), new AtomicInteger());
        Assert.assertEquals(res.getStatusCode(), 400);
        Assert.expectThrows(AssertionError.class, sa::assertAll);
    }

    @Test(groups = {"unit"})
    @Story("Only a write that asked for a success")
    @Description("A negative case that expects its 400 and gets it, a success, and a step with no expectation all continue.")
    public void whatTheStepAskedFor_continues() throws Exception {
        SoftAssert sa = new SoftAssert();
        Assert.assertEquals(post(new HashMap<>(), sa, "http_request_400", 400,
                json(400, DOMAIN_400), new AtomicInteger()).getStatusCode(), 400);
        Assert.assertEquals(post(new HashMap<>(), sa, "http_request_200_1", 200,
                json(200, "{\"accountId\":1}"), new AtomicInteger()).getStatusCode(), 200);
        sa.assertAll();
    }

    @Test(groups = {"unit"})
    @Story("Only a write that asked for a success")
    @Description("The CSV column is the expectation the step really asserts: it overrides the builder value both ways, and a list of codes is honoured.")
    public void csvColumn_decides() throws Exception {
        // Builder says 200, the row says this case expects the 400.
        Map<String, String> negative = new HashMap<>();
        negative.put("expected_http_request_200_1_status_code", "400");
        SoftAssert sa = new SoftAssert();
        post(negative, sa, "http_request_200_1", 200, json(400, DOMAIN_400), new AtomicInteger());
        sa.assertAll();

        // A list that includes what came back.
        Assert.assertFalse(FlowStopped.isRejectedWrite("POST", "/x", "s", -1,
                Set.of(200, 400), json(400, "{}")));
        // A list of successes that does not.
        Assert.assertTrue(FlowStopped.isRejectedWrite("POST", "/x", "s", -1,
                Set.of(200, 201), json(400, "{}")));
        // A list that expects some failure, just not this one: a negative
        // case, and what it leaves behind is not ours to guess.
        Assert.assertFalse(FlowStopped.isRejectedWrite("POST", "/x", "s", -1,
                Set.of(400, 404), json(409, "{}")));
    }

    @Test(groups = {"unit"})
    @Story("Only a write that asked for a success")
    @Description("Reads, deletes, token calls, negative cases and steps with no expectation never stop a flow.")
    public void whatIsNotARejectedWrite() {
        Response r400 = json(400, "{}");
        Assert.assertTrue(FlowStopped.isRejectedWrite("POST", "/businesses", "create", 200, null, r400));
        Assert.assertTrue(FlowStopped.isRejectedWrite("PUT", "/businesses/1", "update", 204, null, r400));
        Assert.assertTrue(FlowStopped.isRejectedWrite("PATCH", "/businesses/1", "update", 200, null, r400));
        Assert.assertTrue(FlowStopped.isRejectedWrite("POST", "/partner", "amex", 200, null, json(503, "{}")));

        // A GET that fails says "not visible", and waits for it already exist.
        Assert.assertFalse(FlowStopped.isRejectedWrite("GET", "/businesses/1", "read", 200, null, json(404, "{}")));
        // A DELETE that fails is usually a pre-clean of something already gone.
        Assert.assertFalse(FlowStopped.isRejectedWrite("DELETE", "/businesses/1", "delete", 204, null, json(404, "{}")));
        Assert.assertFalse(FlowStopped.isRejectedWrite("POST", "/businesses", "create", -1, null, r400));
        Assert.assertFalse(FlowStopped.isRejectedWrite("POST", "/businesses", "create", 400, null, json(409, "{}")));
        Assert.assertFalse(FlowStopped.isRejectedWrite("POST", "/businesses", "create", 200, null, json(200, "{}")));
        Assert.assertFalse(FlowStopped.isRejectedWrite("POST", "/services/oauth2/token", "sf_token_Request", 200, null, r400));
        Assert.assertFalse(FlowStopped.isRejectedWrite("POST", "/x", "tokenRequest", 200, null, r400));
        Assert.assertFalse(FlowStopped.isRejectedWrite("POST", "/businesses", "create", 200, null, null));
    }

    @Test(groups = {"unit"})
    @Story("A refusal is not retried")
    @Description("Which rejections another attempt could change: a 5xx, a throttle, a conflict, an auth verdict -- not a 400, 404 or 422.")
    public void whichRejectionsAreWorthAnotherAttempt() {
        for (int settled : new int[] {400, 404, 405, 415, 422}) {
            Assert.assertFalse(FlowStopped.worthRetrying(settled), "HTTP " + settled);
        }
        for (int mayClear : new int[] {401, 403, 408, 409, 429, 500, 502, 503}) {
            Assert.assertTrue(FlowStopped.worthRetrying(mayClear), "HTTP " + mayClear);
        }
    }

    @Test(groups = {"unit"})
    @Story("A refusal is not retried")
    @Description("One refusal is retried (it may be a collision on a random value); the SAME refusal on the next attempt is not, and the listeners then see no retries remaining.")
    public void retryAnalyzer_stopsWhenTheSameRefusalRepeats() throws Exception {
        System.setProperty("wholeTestRetry", "2");
        RetryAnalyzer ra = new RetryAnalyzer();

        ITestResult first = result(stop(400, DOMAIN_400), 1_000L);
        Assert.assertTrue(RetryAnalyzer.moreRetriesRemain(first), "before retry()");
        Assert.assertTrue(ra.retry(first), "a first refusal gets another attempt");
        Assert.assertTrue(RetryAnalyzer.moreRetriesRemain(first), "after retry(), same attempt");

        ITestResult second = result(stop(400, DOMAIN_400), 2_000L);
        Assert.assertFalse(RetryAnalyzer.moreRetriesRemain(second), "before retry()");
        Assert.assertFalse(ra.retry(second), "the same refusal twice is settled");
        Assert.assertFalse(RetryAnalyzer.moreRetriesRemain(second), "after retry()");
    }

    @Test(groups = {"unit"})
    @Story("A refusal is not retried")
    @Description("NEGATIVE CONTROLS: a DIFFERENT refusal on the next attempt, a refusal that may clear, an ordinary failure, and the switch all keep their retries. Ids in the server's message do not make two refusals different.")
    public void retryAnalyzer_keepsEveryOtherRetry() throws Exception {
        System.setProperty("wholeTestRetry", "2");
        RetryAnalyzer ra = new RetryAnalyzer();

        // "Username is not unique" then a different reason: both retried.
        Assert.assertTrue(ra.retry(result(stop(400, "{\"message\":\"Username is not unique.\"}"), 1L)));
        Assert.assertTrue(ra.retry(result(stop(400, DOMAIN_400), 2L)));
        RetryAnalyzer.resetAttempts();

        // The same reason with different ids IS the same refusal.
        Assert.assertEquals(stop(404, "{\"message\":\"account 2000510893 not found\"}").signature(),
                stop(404, "{\"message\":\"account 2000510901 not found\"}").signature());

        // A conflict may clear on a fresh identity: never settled.
        Assert.assertTrue(ra.retry(result(stop(409, "{}"), 1L)));
        Assert.assertTrue(ra.retry(result(stop(409, "{}"), 2L)));
        RetryAnalyzer.resetAttempts();

        ITestResult other = result(new AssertionError("JsonPath Match: status"), 1L);
        Assert.assertTrue(ra.retry(other));
        Assert.assertTrue(ra.retry(result(new AssertionError("JsonPath Match: status"), 2L)));
        RetryAnalyzer.resetAttempts();

        System.setProperty("test.retryRejectedWrite", "true");
        Assert.assertTrue(ra.retry(result(stop(400, DOMAIN_400), 1L)));
        Assert.assertTrue(ra.retry(result(stop(400, DOMAIN_400), 2L)));
    }

    private static FlowStopped stop(int status) throws Exception {
        return stop(status, "{}");
    }

    private static FlowStopped stop(int status, String body) throws Exception {
        try {
            post(new HashMap<>(), new SoftAssert(), "http_request_200_1", 200,
                    json(status, body), new AtomicInteger());
        } catch (FlowStopped s) {
            return s;
        }
        throw new AssertionError("HTTP " + status + " did not stop the flow");
    }

    private static ITestResult result(Throwable thrown, long startMillis) {
        ClassLoader cl = FlowStoppedTest.class.getClassLoader();
        ITestClass testClass = (ITestClass) Proxy.newProxyInstance(
                cl, new Class<?>[] {ITestClass.class}, (p, m, a) ->
                        "getRealClass".equals(m.getName()) ? FlowStoppedTest.class : null);
        ITestNGMethod method = (ITestNGMethod) Proxy.newProxyInstance(
                cl, new Class<?>[] {ITestNGMethod.class}, (p, m, a) ->
                        "getMethodName".equals(m.getName()) ? "stopped" : null);
        return (ITestResult) Proxy.newProxyInstance(
                cl, new Class<?>[] {ITestResult.class}, (p, m, a) -> {
                    switch (m.getName()) {
                        case "getTestClass": return testClass;
                        case "getMethod": return method;
                        case "getParameters": return new Object[0];
                        case "getThrowable": return thrown;
                        case "getStartMillis": return startMillis;
                        default: return null;
                    }
                });
    }
}
