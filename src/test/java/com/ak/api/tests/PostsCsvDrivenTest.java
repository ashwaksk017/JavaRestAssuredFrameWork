// =============================================================================
// PostsCsvDrivenTest
// -----------------------------------------------------------------------------
// Replaces the reference project's OpenCSV @DataProvider pattern.
// Uses DataProviders.csvData (Jackson-CSV backed).
//
// dataFile / dataStartRow / dataEndRow are supplied by testng.xml <parameter>
// (or -Ddatafile=...) so the same test class can be pointed at different CSVs.
// =============================================================================

package com.ak.api.tests;

import java.io.InputStreamReader;
import java.util.HashMap;
import java.util.Map;

import org.testng.annotations.Test;

import com.ak.api.data.DataProviders;
import com.ak.api.data.Expected;
import com.ak.api.models.Post;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.retry.RetryAnalyzer;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("CSV-Driven Posts API")
public class PostsCsvDrivenTest extends BaseApiTest {

    private static final String TEMPLATE_DIR = "templates/";

    @Test(dataProvider = "csvData",
          dataProviderClass = DataProviders.class,
          groups = {"regression", "posts", "data-driven", "csv"},
          retryAnalyzer = RetryAnalyzer.class)
    @Story("Create post from CSV row")
    @Description("Reads a CSV row via jackson-dataformat-csv, substitutes into createPost.json in strict mode, POSTs, asserts echoed fields via POJO deserialization.")
    public void post_fromCsvRow_echoesFields(Map<String, String> row) throws Exception {
        String testCaseId = "CSV-" + row.getOrDefault("userId", "0");
        Expected exp = expected(row);   // parses row.get("expected")

        InputStreamReader template = RestUtilities.getRequestTemplate(TEMPLATE_DIR, "createPost.json");
        String payload = RestUtilities.mapJsonValues(template, new HashMap<>(row), /* strict = */ true);

        RestUtilities.logRequestBody(testCaseId, holder, payload);
        Response res = RestUtilities.getResponsePost(payload, baseUrl() + "/posts", new HashMap<>());
        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        // Status now comes from the datasheet's `expected` column, falling
        // back to 201 when the row doesn't declare one -- so a single test
        // method can drive both positive (201) and negative (4xx) rows just
        // by editing the CSV.
        int expectedStatus = exp.getInt("statusCode", 201);
        softAssert.assertEquals(res.statusCode(), expectedStatus,
                "expected statusCode " + expectedStatus + " for " + testCaseId);

        Post created = RestUtilities.as(res, Post.class);
        softAssert.assertEquals(created.getTitle(),  row.get("title"),  "title echoed for "  + testCaseId);
        softAssert.assertEquals(created.getBody(),   row.get("body"),   "body echoed for "   + testCaseId);
        softAssert.assertEquals(created.getUserId(), Integer.valueOf(row.get("userId")),
                "userId echoed for " + testCaseId);

        // Optional expectations declared per-row.
        if (exp.has("titleLenMin")) {
            softAssert.assertTrue(created.getTitle() != null
                            && created.getTitle().length() >= exp.getInt("titleLenMin"),
                    "title length >= expected titleLenMin=" + exp.get("titleLenMin"));
        }
    }
}
