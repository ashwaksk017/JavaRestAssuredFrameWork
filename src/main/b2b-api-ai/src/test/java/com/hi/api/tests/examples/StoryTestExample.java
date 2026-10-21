package com.hi.api.tests.examples;

import java.util.List;
import java.util.Map;

import org.testng.annotations.Test;

import com.hi.api.auth.JsonTokens;
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
 * THE SHAPE OF A STORY TEST -- the example the workbench shows Cursor. It is
 * never run (enabled = false): its story key, case and values are made up.
 *
 * <p>DATA_INVENTORY (TC-001, made-up story ABC-1234)</p>
 * <ol>
 *   <li>setup: resource-owner token for the owner persona -> TOKEN accessToken, guestId
 *       ({@code ownerSession}: config jira.abc-1234.owner.username / .password)</li>
 *   <li>setup: POST /guests/{guestId}/businesses -> EXTRACT accountId ({@code createProgramAccount})</li>
 *   <li>act: GET /guests/{guestId}/businesses -> the list the AC is about</li>
 * </ol>
 *
 * <p>What it shows: extend {@link StoryTestBase} and write none of its
 * helpers again; every assertion message starts with the case and the
 * acceptance criterion it proves ({@code "TC-001/AC-1: "}), setup with
 * {@code "TC-001/SETUP: "}; the status the AC names is a literal, not a
 * cell a CSV could change; a check over a list first asserts the list is
 * not empty, so it cannot pass on nothing.</p>
 */
@Epic("API Automation")
@Feature("Program Accounts")
public class StoryTestExample extends StoryTestBase {

    private static final String CASE = "TC-001";

    @Test(enabled = false, groups = {"example"}, retryAnalyzer = RetryAnalyzer.class,
            dataProvider = "rows", dataProviderClass = PerMethodCsvDataProvider.class)
    @Story("ABC-1234 an owner lists the program accounts of a guest")
    @Description("TC-001 covers AC-1, AC-2. AC-1: GET /guests/{guestId}/businesses returns 200. "
            + "AC-2: the list holds the account the owner just created.")
    public void ownerListsProgramAccounts_holdsTheNewAccount_200(Map<String, String> row) throws Exception {
        // Setup -- every piece from StoryTestBase, none written here.
        JsonTokens.Session owner = ownerSession(row, "jira.abc-1234.owner");
        String guestId = owner.requireGuestId();
        String accountId = createProgramAccount(row, "templates/manual/abc-1234_create_program_account.json",
                guestId, owner.accessToken, CASE);
        if (accountId == null) {
            return;                                   // the SETUP assertions say why
        }

        // Act.
        String url = baseUrl() + ApiRoutes.fill(ApiRoutes.GUEST_BUSINESSES, "guestId", guestId);
        Response res = step("listProgramAccounts: GET " + ApiRoutes.GUEST_BUSINESSES,
                () -> RestUtilities.getResponseGet(url, apiHeaders(owner.accessToken)));
        RestUtilities.logResponseBody(CASE, holder, RestUtilities.getResponseAsString(res));

        // Assert -- one assertion (at least) per expected result, tagged with its AC.
        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200",
                "TC-001/AC-1: GET the guest's program accounts returns 200");
        List<Object> ids = listAt(res, "accountId");
        softAssert.assertFalse(ids.isEmpty(), "TC-001/AC-2: the list is not empty");
        softAssert.assertTrue(ids.stream().anyMatch(id -> accountId.equals(String.valueOf(id))),
                "TC-001/AC-2: the list holds the account just created");
    }
}
