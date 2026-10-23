package com.hi.api.tests.jira;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.annotations.Test;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hi.api.config.Config;
import com.hi.api.retry.RetryAnalyzer;
import com.hi.api.rest.utilities.Headers;
import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.tests.BaseApiTest;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("H4B Book on Behalf")
public class H4bMemberStaysTest extends BaseApiTest {

    private static final String JOB_PRIOR = "job-20261015-100336";
    private static final String JOB = "job-20261015-161730";
    private static final ObjectMapper JSON = new ObjectMapper();

    /**
     * Resource-owner style token for the account owner (JSON body with
     * client_id, client_secret, username, password).
     */
    private String ownerAccessToken(String jobId) {
        String clientId = Config.get("api_config.client_id", "");
        String clientSecret = Config.get("api_config.client_secret", "");
        String username = rowOrConfig(null, "owner_username",
                "jira." + jobId + ".owner.username");
        String password = rowOrConfig(null, "owner_password",
                "jira." + jobId + ".owner.password");
        String tokenBase = Config.get("api_config.token_end_point", "");
        String tokenRoute = Config.get("api_config.token_route", "");

        if (Config.isUnset(clientId) || Config.isUnset(clientSecret)
                || Config.isUnset(tokenBase) || Config.isUnset(tokenRoute)) {
            throw new org.testng.SkipException(
                    "Token endpoint credentials are not configured for env '"
                    + Config.env() + "'. Set api_config.client_id, "
                    + "api_config.client_secret, api_config.token_end_point and "
                    + "api_config.token_route in program_configuration.json.");
        }

        Map<String, String> payload = new LinkedHashMap<>();
        payload.put("client_id", clientId);
        payload.put("client_secret", clientSecret);
        payload.put("username", username);
        payload.put("password", password);
        String body;
        try {
            body = JSON.writeValueAsString(payload);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("cannot serialise the token body", e);
        }

        String url = tokenBase.endsWith("/") || tokenRoute.startsWith("/")
                ? tokenBase + tokenRoute
                : tokenBase + "/" + tokenRoute;

        Map<String, String> tokenHeaders = Headers.builder()
                .contentTypeJson()
                .acceptJson()
                .build();
        Response tokenRes = RestUtilities.getResponsePost(body, url, tokenHeaders);
        if (tokenRes.getStatusCode() != 200) {
            throw new IllegalStateException(
                    "token endpoint returned HTTP " + tokenRes.getStatusCode());
        }
        String token = RestUtilities.safeJsonExtract(tokenRes, "access_token");
        if (token == null || token.isBlank()) {
            throw new IllegalStateException(
                    "token endpoint returned 200 but no access_token field");
        }
        return token;
    }

    @Test(groups = {"jira", "regression", "smoke"}, retryAnalyzer = RetryAnalyzer.class)
    @Story(JOB + " Owner lists member stays; H4B row 6 BOB stay visible, view filter applied")
    @Description("AC: GET /guests/{guestId}/businesses/{accountId}/members/{memberId}/stays as owner "
            + "returns the BOB stay from H4B Permission table row 6 (another member's BOB booking) "
            + "and omits stays for which the owner lacks view access.")
    public void ownerListsMemberStays_h4bRow6BobIncluded_viewFilterApplied() {
        String testCaseId = "TC-001";

        String guestId = rowOrConfig(null, "guest_id",
                "jira." + JOB + ".owner.guest_id");
        String accountId = rowOrConfig(null, "account_id",
                "jira." + JOB + ".program_account.account_id");
        String memberId = rowOrConfig(null, "member_id",
                "jira." + JOB + ".target_member.member_id");
        String bobStayConfirmation = rowOrConfig(null, "bob_stay_confirmation_number",
                "jira." + JOB + ".bob_stay.confirmation_number");
        String stayWithoutViewAccess = rowOrConfig(null,
                "stay_without_view_access_confirmation_number",
                "jira." + JOB + ".view_filter.stay_without_view_access_confirmation_number");

        String token = ownerAccessToken(JOB);

        Map<String, String> headers = Headers.fromJson("headers/qa.json");
        headers.put("Authorization", "Bearer " + token);

        String url = baseUrl() + "/guests/" + guestId + "/businesses/" + accountId
                + "/members/" + memberId + "/stays";
        Response res = RestUtilities.getResponseGet(url, headers);

        RestUtilities.logResponseBody(testCaseId, holder,
                RestUtilities.getResponseAsString(res));

        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200",
                "GET member stays status");

        assertConfirmationNumberPresent(res, bobStayConfirmation,
                "H4B row 6 BOB stay must appear in member stays list");
        assertStayWithoutViewAccessIsAbsent(res, stayWithoutViewAccess);
    }

    @Test(groups = {"jira", "regression", "smoke"}, retryAnalyzer = RetryAnalyzer.class)
    @Story(JOB_PRIOR + " Owner lists member stays filtered by H4B view access")
    @Description("AC: GET /guests/{guestId}/businesses/{accountId}/members/{memberId}/stays "
            + "returns only stays for which the owner has view access; stays without view "
            + "access are absent from the list.")
    public void ownerListsMemberStays_onlyViewAccessibleStays() {
        String testCaseId = "TC-001-prior-job";

        String guestId = rowOrConfig(null, "guest_id",
                "jira." + JOB_PRIOR + ".owner.guest_id");
        String accountId = rowOrConfig(null, "account_id",
                "jira." + JOB_PRIOR + ".program_account.account_id");
        String memberId = rowOrConfig(null, "member_id",
                "jira." + JOB_PRIOR + ".target_member.member_id");
        String stayWithoutViewAccess = rowOrConfig(null,
                "stay_without_view_access_confirmation_number",
                "jira." + JOB_PRIOR + ".view_filter.stay_without_view_access_confirmation_number");

        String token = ownerAccessToken(JOB_PRIOR);

        Map<String, String> headers = Headers.fromJson("headers/qa.json");
        headers.put("Authorization", "Bearer " + token);

        String url = baseUrl() + "/guests/" + guestId + "/businesses/" + accountId
                + "/members/" + memberId + "/stays";
        Response res = RestUtilities.getResponseGet(url, headers);

        RestUtilities.logResponseBody(testCaseId, holder,
                RestUtilities.getResponseAsString(res));

        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200",
                "GET member stays status");

        assertStayWithoutViewAccessIsAbsent(res, stayWithoutViewAccess);
    }

    private void assertConfirmationNumberPresent(Response res, String confirmationNumber,
            String message) {
        List<String> fromStaysArray = safeConfirmationList(res, "stays.confirmationNumber");
        if (fromStaysArray != null) {
            softAssert.assertTrue(fromStaysArray.contains(confirmationNumber), message
                    + " (stays.confirmationNumber)");
            return;
        }
        List<String> fromRootArray = safeConfirmationList(res, "confirmationNumber");
        if (fromRootArray != null) {
            softAssert.assertTrue(fromRootArray.contains(confirmationNumber), message
                    + " (confirmationNumber)");
            return;
        }
        String body = RestUtilities.getResponseAsString(res);
        softAssert.assertTrue(body.contains("\"" + confirmationNumber + "\""),
                message + " (response body)");
    }

    /**
     * The acceptance criterion is negative: a stay the caller cannot view must not
     * appear in the list. Test data supplies that confirmation number via config.
     */
    private void assertStayWithoutViewAccessIsAbsent(Response res, String confirmationNumber) {
        List<String> fromStaysArray = safeConfirmationList(res, "stays.confirmationNumber");
        if (fromStaysArray != null) {
            softAssert.assertFalse(fromStaysArray.contains(confirmationNumber),
                    "stays without view access must be absent (stays.confirmationNumber)");
            return;
        }
        List<String> fromRootArray = safeConfirmationList(res, "confirmationNumber");
        if (fromRootArray != null) {
            softAssert.assertFalse(fromRootArray.contains(confirmationNumber),
                    "stays without view access must be absent (confirmationNumber)");
            return;
        }
        String body = RestUtilities.getResponseAsString(res);
        softAssert.assertFalse(body.contains("\"" + confirmationNumber + "\""),
                "stays without view access must be absent from response body");
    }

    private static List<String> safeConfirmationList(Response res, String jsonPath) {
        try {
            return res.jsonPath().getList(jsonPath);
        } catch (Exception e) {
            return null;
        }
    }
}
