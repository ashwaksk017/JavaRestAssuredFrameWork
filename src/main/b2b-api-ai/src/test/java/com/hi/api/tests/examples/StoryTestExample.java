package com.hi.api.tests.examples;

import java.util.Map;

import org.testng.annotations.Test;

import com.hi.api.data.PerMethodCsvDataProvider;
import com.hi.api.rest.ApiRoutes;
import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.retry.RetryAnalyzer;
import com.hi.api.tests.StoryTestBase;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

/**
 * THE SHAPE OF A STORY TEST -- the example the workbench shows Cursor. Never
 * run (enabled = false): its story and case are made up.
 *
 * <p>People are built as the converted B2B suites build them: every guest is
 * ENROLLED by the test, every id comes from a response, a role is given by
 * the owner through the API, and every call carries the service token with
 * the acting guest in the path. Nobody logs in as a person set up by hand.
 * See .cursor/skills/jira-to-restassured/B2B_FLOWS.md.</p>
 *
 * <p>DATA_INVENTORY (TC-001, made-up story ABC-1234)</p>
 * <ol>
 *   <li>setup: POST /realms/guests/enroll (owner) -> EXTRACT guestId</li>
 *   <li>setup: POST /guests/{owner}/businesses -> EXTRACT accountId, owner memberId</li>
 *   <li>setup: DB email_otp -> POST .../confirmValidation; POST /businesses/{accountId}/activate</li>
 *   <li>setup: enrol member -> EXTRACT guestId; GET /businesses/{accountId} -> EXTRACT inviteKey;
 *       POST /guests/{member}/businesses/{accountId}/members -> EXTRACT memberId; activate member;
 *       PUT /guests/{owner}/.../members/{memberId} role travelCoordinator</li>
 *   <li>act: GET /guests/{member}/businesses/{accountId}/members/{memberId}</li>
 * </ol>
 * <p>Configuration: none per story. Per environment: api_config.client_id / client_secret, the
 * project username / password (service token), database.* (one-time code).</p>
 *
 * <p>Assertion messages start with the case and the AC ({@code "TC-001/AC-1: "}); setup helpers
 * assert their own steps as {@code "TC-001/SETUP: "} and return null/false on failure -- then
 * {@code return}. The status the AC names is a literal.</p>
 */
@Epic("API Automation")
@Feature("Program Account Members")
public class StoryTestExample extends StoryTestBase {

    private static final String CASE = "TC-001";
    private static final String ENROLL = "templates/manual/b2b_enroll_guest.json";
    private static final String CREATE_ACCOUNT = "templates/manual/b2b_create_program_account.json";

    @Test(enabled = false, groups = {"example"}, retryAnalyzer = RetryAnalyzer.class,
            dataProvider = "rows", dataProviderClass = PerMethodCsvDataProvider.class)
    @Story("ABC-1234 a travel coordinator reads their own membership")
    @Description("TC-001 covers AC-1, AC-2. AC-1: GET the member as the travel coordinator returns 200. "
            + "AC-2: the member's role is travelCoordinator.")
    public void travelCoordinatorReadsOwnMembership_roleIsTravelCoordinator_200(Map<String, String> row)
            throws Exception {
        requireDb("validate the new account's owner");    // before anything is created
        String svc = serviceToken();

        // Setup: the owner and an active account.
        Actor owner = enrollActor(row, ENROLL, CASE);
        String[] account = owner == null ? null : createAccountFor(owner, CREATE_ACCOUNT, svc, CASE);
        if (account == null) {
            return;                                        // the SETUP assertions say why
        }
        String accountId = account[0];
        if (!confirmOwnerValidation(owner, accountId, account[1], svc, CASE)
                || !activateAccount(accountId, svc, CASE)) {
            return;
        }

        // Setup: a second guest joins, and the owner makes them travel coordinator.
        Actor coordinator = enrollActor(row, ENROLL, CASE);
        if (coordinator == null
                || addMember(coordinator, accountId, "employee", inviteKey(accountId, svc, CASE), svc, CASE) == null
                || !activateMember(accountId, coordinator.memberId, svc, CASE)
                || !setMemberRole(owner, accountId, coordinator, "travelCoordinator", svc, CASE)) {
            return;
        }

        // Act -- as the coordinator: their guest id and member id in the path.
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.GUEST_BUSINESS_MEMBER,
                "guestId", coordinator.guestId, "accountId", accountId, "memberId", coordinator.memberId);
        Response res = step("readAccountMember: GET " + ApiRoutes.GUEST_BUSINESS_MEMBER,
                () -> RestUtilities.getResponseGet(url, apiHeaders(svc)));
        RestUtilities.logResponseBody(CASE, holder, RestUtilities.getResponseAsString(res));

        // Assert -- one assertion (at least) per expected result, tagged with its AC.
        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200",
                "TC-001/AC-1: GET the member as the travel coordinator returns 200");
        softAssert.assertEquals(RestUtilities.safeJsonExtract(res, "role"), "travelCoordinator",
                "TC-001/AC-2: the member's role is travelCoordinator");
    }
}
