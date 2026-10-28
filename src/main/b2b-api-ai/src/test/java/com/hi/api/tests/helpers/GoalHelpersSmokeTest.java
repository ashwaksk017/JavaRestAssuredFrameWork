package com.hi.api.tests.helpers;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.annotations.Test;

import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.tests.GoalStoryTestBase;

import io.restassured.response.Response;

/**
 * Every GOAL story helper, once, against a real environment, in the order of
 * the converted confirm chain (group360createstage goal58_Post_confirm_200):
 * token, the availability search, create the group, pay, confirm -- with the
 * committed starter bodies a story starts from.
 *
 * <p>Why: the story helpers are shared code that only story tests run, so a
 * story is the first to meet a broken one -- seen in review: the search went
 * out without peakRooms (the converted probe measured 460 of 500 such searches
 * answered 400, read as "no availability"), and the starter confirm body had
 * two fields where the room-block confirm sends the whole group. Run this after
 * a change to GoalStoryTestBase, a starter body or the configuration, and on a
 * new environment -- {@code src/test/resources/testng-helpers-goal.xml} on the
 * Run tests tab, with project GOAL. The first step that fails is named, and
 * nothing after it runs.</p>
 *
 * <p>It creates what a story does: a group, its payment and its confirm.</p>
 */
public class GoalHelpersSmokeTest extends GoalStoryTestBase {

    private static final String TAG = "SMOKE";
    private static final String GROUP = "templates/manual/goal_create_group.json";
    private static final String PAYMENT = "templates/manual/goal_payment.json";
    private static final String CONFIRM = "templates/manual/goal_confirm.json";

    @Test(groups = {"helpers-smoke"}, description = "every GOAL story helper, in the converted confirm chain's order")
    public void everyHelperWorksAgainstThisEnvironment() throws Exception {
        String token = goalToken();
        Map<String, String> row = new LinkedHashMap<>();
        Availability av = findAvailability(row, token, TAG);
        if (av == null) {
            return;
        }
        if (!createGroup(av, row, GROUP, token, TAG)) {
            return;
        }
        if (!payGroup(av, row, PAYMENT, token, TAG)) {
            return;
        }
        Response res = confirmGroup(av, row, CONFIRM, token, TAG);
        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200",
                TAG + "/SETUP: POST group confirm status" + said(res));
    }
}
