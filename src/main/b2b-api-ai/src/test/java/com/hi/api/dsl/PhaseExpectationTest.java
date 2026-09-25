package com.hi.api.dsl;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.hi.api.dsl.CustomerOnboarding.Expectation;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.response.Response;

/**
 * Response validation for hand-written phases.
 *
 * <p>RestStep asserts the status and nothing else, so before these verbs a
 * manual phase could return a 200 carrying completely wrong content and
 * still pass. These tests pin the body checks down.</p>
 *
 * <p>No HTTP: responses are fabricated with {@link ResponseBuilder}, the
 * same way {@code ResponseAssertsTest} and {@code RestStepTest} do.</p>
 */
@Epic("Manual")
@Feature("Response validation")
public class PhaseExpectationTest {

    private static final String PHASE = "createProgramAccount";

    private static Response json(String body) {
        return new ResponseBuilder()
                .setStatusCode(200)
                .setContentType("application/json")
                .setBody(body)
                .build();
    }

    private static List<Expectation> queue(Expectation... items) {
        List<Expectation> out = new ArrayList<>();
        for (Expectation e : items) {
            out.add(e);
        }
        return out;
    }

    private static int failures(SoftAssert sa) {
        try {
            sa.assertAll();
            return 0;
        } catch (AssertionError e) {
            return 1;
        }
    }

    @Test(groups = {"unit"})
    @Story("a wrong body value fails the phase")
    @Description("""
            The gap this closes: status-only validation passes a 200 that
            carries the wrong content.
            """)
    public void mismatchedJsonValueFails() {
        SoftAssert sa = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                sa, json("{\"accountStatus\":\"limited\"}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.json("accountStatus", "active")));
        Assert.assertEquals(failures(sa), 1, "a wrong value must fail");
    }

    @Test(groups = {"unit"})
    @Story("a matching body value passes")
    public void matchingJsonValuePasses() {
        SoftAssert sa = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                sa, json("{\"accountStatus\":\"active\"}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.json("accountStatus", "active")));
        Assert.assertEquals(failures(sa), 0, "a matching value must pass");
    }

    @Test(groups = {"unit"})
    @Story("the CSV column overrides the value stated in code")
    @Description("""
            Same contract the converter's own assertions follow, so a manual
            test and an imported one answer to the same row.
            """)
    public void csvColumnOverridesTheCodeDefault() {
        Map<String, String> row = new HashMap<>();
        row.put("expected_" + PHASE + "_jsonpath_accountStatus", "limited");

        SoftAssert sa = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                sa, json("{\"accountStatus\":\"limited\"}"),
                new LinkedHashMap<>(), row, PHASE,
                queue(Expectation.json("accountStatus", "active")));
        Assert.assertEquals(failures(sa), 0,
                "the row said limited, and the body is limited");
    }

    @Test(groups = {"unit"})
    @Story("an empty CSV cell disables the check")
    @Description("rowSaysSkip is fail-open: present-but-empty means do not assert.")
    public void emptyCsvCellSkipsTheCheck() {
        Map<String, String> row = new HashMap<>();
        row.put("expected_" + PHASE + "_jsonpath_accountStatus", "");

        SoftAssert sa = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                sa, json("{\"accountStatus\":\"limited\"}"),
                new LinkedHashMap<>(), row, PHASE,
                queue(Expectation.json("accountStatus", "active")));
        Assert.assertEquals(failures(sa), 0, "an empty cell must skip");
    }

    @Test(groups = {"unit"})
    @Story("exists and absent are checked")
    public void existsAndAbsentAreEnforced() {
        SoftAssert missing = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                missing, json("{\"accountStatus\":\"active\"}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.exists("accountId")));
        Assert.assertEquals(failures(missing), 1, "a missing path must fail");

        SoftAssert present = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                present, json("{\"accountId\":\"123\"}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.exists("accountId")));
        Assert.assertEquals(failures(present), 0, "a present path must pass");

        SoftAssert absent = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                absent, json("{\"accountId\":\"123\"}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.absent("accountId")));
        Assert.assertEquals(failures(absent), 1,
                "a path that IS present must fail an absent expectation");
    }

    @Test(groups = {"unit"})
    @Story("nothing queued asserts nothing")
    @Description("A phase with no stated expectations must stay silent, not fail.")
    public void anEmptyQueueAssertsNothing() {
        SoftAssert sa = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                sa, json("{\"anything\":\"at all\"}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                new ArrayList<>());
        Assert.assertEquals(failures(sa), 0, "an empty queue must not fail");
    }

    @Test(groups = {"unit"})
    @Story("bodyContains matches an exact scalar anywhere in the body")
    @Description("""
            Exact scalars only: 'verified' must not be satisfied by
            'unverified'. That is the whole point of the helper.
            """)
    public void bodyContainsMatchesAnExactScalar() {
        SoftAssert hit = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                hit, json("{\"nested\":{\"status\":\"active\"}}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.bodyContains(null, "active")));
        Assert.assertEquals(failures(hit), 0, "an exact scalar must match");

        SoftAssert miss = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                miss, json("{\"nested\":{\"status\":\"active\"}}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.bodyContains(null, "limited")));
        Assert.assertEquals(failures(miss), 1, "a value not present must fail");
    }

    @Test(groups = {"unit"})
    @Story("substring is a contains check, not equality")
    public void substringChecksContainment() {
        SoftAssert hit = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                hit, json("{\"msg\":\"hello world\"}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.substring("msg", "world")));
        Assert.assertEquals(failures(hit), 0, "a substring must match");

        SoftAssert miss = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                miss, json("{\"msg\":\"hello world\"}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.substring("msg", "mars")));
        Assert.assertEquals(failures(miss), 1, "an absent substring must fail");
    }

    @Test(groups = {"unit"})
    @Story("a subtree compares key-order insensitively")
    public void jsonTreeIgnoresKeyOrder() {
        SoftAssert sa = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                sa, json("{\"address\":{\"city\":\"Houston\",\"state\":\"TX\"}}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.jsonTree("address",
                        "{\"state\":\"TX\",\"city\":\"Houston\"}")));
        Assert.assertEquals(failures(sa), 0, "key order must not matter");
    }

    @Test(groups = {"unit"})
    @Story("a subtree whose leaves are absent fails")
    @Description("""
            jsonTreeEquals is lenient when the trees differ: it then asks
            whether every expected scalar leaf appears somewhere in the body.
            A leaf that appears nowhere must still fail.
            """)
    public void jsonTreeFailsWhenALeafIsAbsent() {
        SoftAssert sa = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                sa, json("{\"address\":{\"city\":\"Houston\",\"state\":\"TX\"}}"),
                new LinkedHashMap<>(), new HashMap<>(), PHASE,
                queue(Expectation.jsonTree("address", "{\"city\":\"Dallas\"}")));
        Assert.assertEquals(failures(sa), 1, "an absent leaf must fail");
    }

    @Test(groups = {"unit"})
    @Story("expectCaptured asserts on memory, not the response")
    @Description("""
            Pairs with capture(): capture on one phase, assert on a later one.
            The response here does not contain the value at all.
            """)
    public void capturedComparesTheCtxValue() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("Properties.guestID", "12345");

        SoftAssert hit = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                hit, json("{\"unrelated\":\"body\"}"), ctx, new HashMap<>(), PHASE,
                queue(Expectation.captured("Properties.guestID", "12345")));
        Assert.assertEquals(failures(hit), 0, "a matching ctx value must pass");

        SoftAssert miss = new SoftAssert();
        CustomerOnboarding.applyExpectations(
                miss, json("{\"unrelated\":\"body\"}"), ctx, new HashMap<>(), PHASE,
                queue(Expectation.captured("Properties.guestID", "999")));
        Assert.assertEquals(failures(miss), 1, "a differing ctx value must fail");
    }
}
