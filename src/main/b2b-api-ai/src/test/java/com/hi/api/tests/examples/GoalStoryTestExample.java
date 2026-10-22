package com.hi.api.tests.examples;

import java.util.Map;

import org.testng.annotations.Test;

import com.hi.api.data.PerMethodCsvDataProvider;
import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.retry.RetryAnalyzer;
import com.hi.api.tests.GoalStoryTestBase;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

/**
 * THE SHAPE OF A GOAL STORY TEST -- the example the workbench shows Cursor
 * for a GOAL job. Never run (enabled = false): its story and case are made up.
 *
 * <p>GOAL is not B2B: no people are created. The test FINDS a property and
 * dates with availability (never a fixed property or a frozen date), takes
 * the server-made {@code groupId} and room type from the shop answer, and
 * carries them into the group -- the proven path of the converted GOAL
 * suites (shop 179/179, create group 36/36). See
 * .cursor/skills/jira-to-restassured/GOAL_FLOWS.md.</p>
 *
 * <p>DATA_INVENTORY (TC-001, made-up story GOAL-1234)</p>
 * <ol>
 *   <li>setup: POST /realms/applications/token on GOAL's service -> TOKEN ({@code goalToken})</li>
 *   <li>setup: GET /props/{propCode}/groups per candidate x offset -> EXTRACT propCode, dates,
 *       groupId, roomRates[i].roomTypeCode ({@code findAvailability})</li>
 *   <li>act: POST /props/{propCode}/groups -> the group ({@code createGroupResponse})</li>
 * </ol>
 * <p>Configuration: none per story. Per environment (GOAL's file): api_config.client_id /
 * client_secret, services.hospitality_internal_all_v2, base_url, goal.availability.props.</p>
 */
@Epic("API Automation")
@Feature("GOAL Groups")
public class GoalStoryTestExample extends GoalStoryTestBase {

    private static final String CASE = "TC-001";
    private static final String CREATE_GROUP = "templates/manual/goal_create_group.json";

    @Test(enabled = false, groups = {"example"}, retryAnalyzer = RetryAnalyzer.class,
            dataProvider = "rows", dataProviderClass = PerMethodCsvDataProvider.class)
    @Story("GOAL-1234 a room block is created for a property with availability")
    @Description("TC-001 covers AC-1, AC-2. AC-1: POST the group returns 200. "
            + "AC-2: the group's room type is the one the shop quoted.")
    public void createGroup_forAvailableProperty_200(Map<String, String> row) throws Exception {
        // Setup: the token, and a property + dates the shop proves have inventory.
        String token = goalToken();
        Availability av = findAvailability(row, token, CASE);
        if (av == null) {
            return;                                        // the SETUP assertion says what was tried
        }

        // Act.
        Response res = createGroupResponse(av, row, CREATE_GROUP, token, CASE);

        // Assert -- one assertion (at least) per expected result, tagged with its AC.
        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200",
                "TC-001/AC-1: POST the group returns 200");
        softAssert.assertEquals(RestUtilities.safeJsonExtract(res, "roomRates[0].roomTypeCode"),
                av.roomTypeCode, "TC-001/AC-2: the group's room type is the one the shop quoted");
    }
}
