// =============================================================================
// GoalShopSinglePropLegacyStyleTest
// -----------------------------------------------------------------------------
// The SAME ReadyAPI case as the converted `goal09GetShopTest`, written in the
// classic Rest Assured style: a token fetched by hand, a URL built here, plain
// RestUtilities calls, a CSV row, and explicit assertions. No fluent chain, no
// CaseRegistry, no phase vocabulary, no generated client.
//
// It is the counterpart to B2B722LegacyStyleTest, for the second converted
// project. Everything below depends only on COMMITTED code -- RestUtilities,
// Headers, Config, BaseApiTest -- so it compiles on a bare clone after
// `--bootstrap`, with no generated client and no generated support classes.
//
// One thing this project shows that the b2b one does not: its token endpoint
// is NOT a client-credentials grant. The ReadyAPI case POSTs a JSON body of
// client_id + client_secret + username + password, so AuthHelper (which posts
// form-encoded grant_type/client_id/client_secret) cannot fetch this token.
// A legacy-style test has to do it itself, which is exactly what `token()`
// below does -- and it doubles as executable documentation of the shape.
//
// WHAT YOU GIVE UP versus the chained version
//   * the token, the URL and the query string are assembled here rather than
//     resolved from a generated client and a phase
//   * no ctx, so nothing carries a value from one call to the next
//   * no ManualCleanup; this case creates nothing, so there is none to do
//
// WHAT YOU KEEP
//   * the CSV-per-method data provider, reading the SAME columns the
//     converted test reads (qry_* for the query string, expected_* for the
//     assertions), so both are driven by one datasheet convention
//   * Allure request/response attachments (BaseApiTest installs the filters)
//   * redaction, soft assertions, the failure digest
//
// THE DATASHEET
//   src/test/resources/csv/GoalShopSinglePropLegacyStyleTest/
//       shopSingleProp_returnsAvailability.csv   -- NOT committed.
//   src/test/resources/csv/ is gitignored wholesale (rows carry property
//   codes, dates and ids; this repo is public), so a clone writes its own.
//   Columns read here:
//     description                     free text, shown in the run output
//     test_case_id                    id for the row
//     propCode                        property to shop
//     qry_GET_Shop_arrivalDate        yyyy-MM-dd  -> query string
//     qry_GET_Shop_departureDate      yyyy-MM-dd  -> query string
//     qry_GET_Shop_peakRooms          integer     -> query string
//     expected_GET_Shop_status_code   expected HTTP status (default 200)
//   Every qry_GET_Shop_* column becomes a query parameter named after its
//   suffix, so a new one needs no Java change. Add a ROW for a new variant.
// =============================================================================

package com.hi.api.tests.legacy;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.SkipException;
import org.testng.annotations.Test;

import com.hi.api.config.Config;
import com.hi.api.data.PerMethodCsvDataProvider;
import com.hi.api.rest.utilities.Headers;
import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.tests.BaseApiTest;

import io.qameta.allure.Allure;
import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Group shopping (legacy style)")
public class GoalShopSinglePropLegacyStyleTest extends BaseApiTest {

    /**
     * Base for the shop call.
     *
     * <p>Reads the service key the converter assigns to this endpoint, and
     * falls back to {@code Config.baseUrl()} when no `services` block is
     * configured -- the same resolution the generated client uses, so the
     * legacy test cannot drift to a different host than the chained one.</p>
     */
    private String shopBase() {
        return Config.get("services.hospitality_internal_all_v2", Config.baseUrl());
    }

    /**
     * Fetch a bearer token the way THIS project's ReadyAPI case does.
     *
     * <p>A JSON body carrying client_id, client_secret, username and password
     * -- a password grant, not client credentials. The response field is
     * {@code access_token}.</p>
     *
     * <p>Every value comes from program_configuration.json, which is the one
     * file allowed to hold a credential. Nothing is defaulted: an unset key
     * skips the test rather than POSTing an empty or placeholder credential
     * at a real token endpoint.</p>
     */
    private String token() {
        String clientId = Config.get("api_config.client_id", "");
        String clientSecret = Config.get("api_config.client_secret", "");
        String username = Config.get("username", "");
        String password = Config.get("password", "");
        String tokenBase = Config.get("api_config.token_end_point", "");
        String tokenRoute = Config.get("api_config.token_route", "");

        if (Config.isUnset(clientId) || Config.isUnset(clientSecret)
                || Config.isUnset(username) || Config.isUnset(password)
                || Config.isUnset(tokenBase) || Config.isUnset(tokenRoute)) {
            throw new SkipException(
                    "Token credentials are not configured for env '"
                    + Config.env() + "'. Set api_config.client_id, "
                    + "api_config.client_secret, username and password in "
                    + "src/main/resources/program_configuration.json "
                    + "(gitignored) -- this project keeps them in "
                    + "local-config/<environment>.properties in ReadyAPI.");
        }

        String url = tokenBase.endsWith("/") || tokenRoute.startsWith("/")
                ? tokenBase + tokenRoute
                : tokenBase + "/" + tokenRoute;

        // Built with a map -> JSON rather than string concatenation so a value
        // containing a quote cannot break out of the body.
        Map<String, String> payload = new LinkedHashMap<>();
        payload.put("client_id", clientId);
        payload.put("client_secret", clientSecret);
        payload.put("username", username);
        payload.put("password", password);

        String body;
        try {
            body = new com.fasterxml.jackson.databind.ObjectMapper()
                    .writeValueAsString(payload);
        } catch (com.fasterxml.jackson.core.JsonProcessingException e) {
            throw new IllegalStateException("cannot serialise the token body", e);
        }

        Response res = Allure.step("tokenRequest", () -> RestUtilities.getResponsePost(
                body, url,
                Headers.builder().contentTypeJson().acceptJson().build()));

        if (res.getStatusCode() != 200) {
            throw new IllegalStateException(
                    "token endpoint returned HTTP " + res.getStatusCode()
                    + " -- cannot run the shop call without a bearer");
        }
        String token = res.jsonPath().getString("access_token");
        if (token == null || token.isBlank()) {
            throw new IllegalStateException(
                    "token endpoint returned 200 but no access_token field");
        }
        return token;
    }

    /**
     * The query string, read from the SAME `qry_GET_Shop_*` columns the
     * converted test reads.
     *
     * <p>Taking them off the row rather than hardcoding them is what lets one
     * datasheet drive both styles -- add a row and both gain a variant.</p>
     */
    private Map<String, String> shopQuery(Map<String, String> row) {
        Map<String, String> q = new LinkedHashMap<>();
        for (Map.Entry<String, String> e : row.entrySet()) {
            String k = e.getKey();
            if (k == null || !k.startsWith("qry_GET_Shop_")) {
                continue;
            }
            String v = e.getValue();
            if (v != null && !v.isBlank()) {
                q.put(k.substring("qry_GET_Shop_".length()), v.trim());
            }
        }
        return q;
    }

    /** Expected status for a step, from `expected_<step>_status_code`. */
    private int expectedStatus(Map<String, String> row, String step, int fallback) {
        String v = row.get("expected_" + step + "_status_code");
        if (v == null || v.isBlank()) {
            return fallback;
        }
        try {
            return Integer.parseInt(v.trim());
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException(
                    "expected_" + step + "_status_code is not an int: '" + v + "'", e);
        }
    }

    @Test(dataProvider = "rows",
          dataProviderClass = PerMethodCsvDataProvider.class,
          groups = {"legacy", "goal"})
    @Story("Shop a single property for group availability")
    @Description("""
            The converted goal09 case in the classic style: fetch a token,
            GET /props/{propCode}/groups with the dates and peak rooms from
            the datasheet, and assert the availability fields the ReadyAPI
            case asserted.
            """)
    public void shopSingleProp_returnsAvailability(Map<String, String> row) {
        String propCode = row.get("propCode");
        if (propCode == null || propCode.isBlank()) {
            throw new IllegalArgumentException(
                    "the datasheet must supply a propCode column");
        }

        String bearer = token();
        Map<String, String> query = shopQuery(row);
        String url = shopBase() + "/props/" + propCode + "/groups";

        Response res = Allure.step("GET_Shop", () -> RestUtilities.getResponseGet(
                url + toQueryString(query),
                Headers.builder()
                        .contentTypeJson()
                        .acceptJson()
                        .bearer(bearer)
                        .correlationId()
                        .build()));

        softAssert.assertEquals(res.getStatusCode(),
                expectedStatus(row, "GET_Shop", 200),
                "GET_Shop status");

        // The same five existence assertions the ReadyAPI case carries, and
        // the propCode echo. Soft, so one missing field does not hide the
        // others -- the whole point of running this against a live service.
        if (res.getStatusCode() == 200) {
            for (String path : new String[] {
                    "groupId", "taxPeriods", "numRoomsAvail", "ratePlanCode",
                    "roomRates[0].roomTypeCode" }) {
                softAssert.assertNotNull(res.jsonPath().get(path),
                        "GET_Shop response is missing " + path);
            }
            softAssert.assertEquals(res.jsonPath().getString("propCode"),
                    propCode, "GET_Shop echoed a different propCode");
        }
        softAssert.assertAll();
    }

    /** Query string for a URL already carrying no `?`. */
    private String toQueryString(Map<String, String> q) {
        if (q.isEmpty()) {
            return "";
        }
        StringBuilder sb = new StringBuilder("?");
        for (Map.Entry<String, String> e : q.entrySet()) {
            if (sb.length() > 1) {
                sb.append('&');
            }
            sb.append(java.net.URLEncoder.encode(e.getKey(),
                            java.nio.charset.StandardCharsets.UTF_8))
              .append('=')
              .append(java.net.URLEncoder.encode(e.getValue(),
                            java.nio.charset.StandardCharsets.UTF_8));
        }
        return sb.toString();
    }
}
