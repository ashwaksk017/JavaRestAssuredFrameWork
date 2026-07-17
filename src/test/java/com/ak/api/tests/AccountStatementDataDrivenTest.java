// =============================================================================
// AccountStatementDataDrivenTest
// -----------------------------------------------------------------------------
// Full end-to-end pipeline from a datasheet through a JSON template through
// an HTTP round-trip, modelled after the reference project's accountStatement
// flow:
//
//   1. testng.xml supplies dataFile -> testdata/account_statements.csv
//   2. DataProviders.csvData feeds each row into the test as a Map
//   3. RestUtilities.mapJsonValues substitutes #placeholders# in
//      templates/accountStatement.json with the row's values
//   4. The resolved JSON is POSTed to postman-echo.com/post (a public echo
//      service that returns the parsed request body back under `json.*`)
//   5. Assertions confirm every placeholder resolved AND round-tripped
//      through the wire correctly
//
// All values are per-row -- swap the CSV to change what the tests do; the
// Java code doesn't need to change.
// =============================================================================

package com.ak.api.tests;

import java.io.InputStreamReader;
import java.util.HashMap;
import java.util.Map;

import org.testng.annotations.Test;

import com.ak.api.data.DataProviders;
import com.ak.api.data.Expected;
import com.ak.api.rest.utilities.Headers;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.retry.RetryAnalyzer;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Account Statement -- template + datasheet")
public class AccountStatementDataDrivenTest extends BaseApiTest {

    private static final String TEMPLATE_DIR  = "templates/";
    private static final String TEMPLATE_FILE = "accountStatement.json";
    /** Echoes the parsed JSON body back under `json.*` so we can assert on
     *  what the server saw after substitution. */
    private static final String ECHO_URL = "https://postman-echo.com/post";

    @Test(dataProvider = "csvData",
          dataProviderClass = DataProviders.class,
          groups = {"regression", "account-statement", "data-driven", "csv"},
          retryAnalyzer = RetryAnalyzer.class)
    @Story("Resolve accountStatement.json placeholders from CSV row and POST")
    @Description("For every row of testdata/account_statements.csv, load the accountStatement.json template, substitute #accountId# / #startingSettlementWindowId# / #endingSettlementWindowId# in strict mode, POST to a public echo service, and assert every placeholder round-tripped through the wire.")
    public void accountStatement_fromCsvRow_substitutesAndPosts(Map<String, String> row) throws Exception {
        String testCaseId = "ACCT-" + row.get("accountId");
        Expected exp      = expected(row);

        // 1. Load template + substitute placeholders in STRICT mode.
        //    Unresolved #placeholders# throw UnresolvedPlaceholderException.
        InputStreamReader template = RestUtilities.getRequestTemplate(TEMPLATE_DIR, TEMPLATE_FILE);
        String payload = RestUtilities.mapJsonValues(template, new HashMap<>(row), /* strict = */ true);
        RestUtilities.logRequestBody(testCaseId, holder, payload);

        // 2. POST to a public echo service. The response body's `json` key
        //    contains the parsed request body, letting us assert what the
        //    server saw.
        Map<String, String> headers = Headers.builder()
                .contentTypeJson()
                .acceptJson()
                .correlationId()
                .build();
        Response res = RestUtilities.getResponsePost(payload, ECHO_URL, headers);
        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        // 3. Skip cleanly if the echo service is 5xx (external dependency).
        ExternalService.skipIfUnavailable(res);

        // 4. Assertions -- status + every placeholder round-tripped.
        int expectedStatus = exp.getInt("statusCode", 200);
        softAssert.assertEquals(res.statusCode(), expectedStatus,
                "expected HTTP " + expectedStatus + " for " + testCaseId);

        softAssert.assertEquals(res.jsonPath().getString("json.accountId"),
                row.get("accountId"),
                "accountId placeholder resolved and round-tripped for " + testCaseId);
        softAssert.assertEquals(res.jsonPath().getString("json.startingSettlementWindowId"),
                row.get("startingSettlementWindowId"),
                "startingSettlementWindowId round-tripped for " + testCaseId);
        softAssert.assertEquals(res.jsonPath().getString("json.endingSettlementWindowId"),
                row.get("endingSettlementWindowId"),
                "endingSettlementWindowId round-tripped for " + testCaseId);

        // 5. Optional per-row expectations from the 'expected' column.
        if (exp.has("echoedAccountId")) {
            softAssert.assertEquals(res.jsonPath().getString("json.accountId"),
                    exp.get("echoedAccountId"),
                    "echoedAccountId matches expected column for " + testCaseId);
        }
    }
}
