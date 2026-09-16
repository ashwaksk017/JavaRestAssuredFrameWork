package com.ak.api.dsl;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import com.ak.api.dsl.CustomerOnboarding.Expectation;

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
}
