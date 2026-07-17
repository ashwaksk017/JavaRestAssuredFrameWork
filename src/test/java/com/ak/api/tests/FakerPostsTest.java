// =============================================================================
// FakerPostsTest -- demonstrates FakeData / net.datafaker in the request path
// -----------------------------------------------------------------------------
// Same pipeline as PostsDataDrivenTest (template + dataMap + strict placeholder
// substitution + POST), but the dataMap comes from FakeData.postDataMap() so
// every run posts a different generated payload. The echoed response is
// asserted back against what we generated -- proving the round-trip is
// data-independent, not relying on hardcoded fixtures.
//
// Set -Dfake.seed=<long> if you need repeatable data across runs (e.g., during
// bug triage or when a failure needs to be reproduced deterministically).
// =============================================================================

package com.ak.api.tests;

import java.io.InputStreamReader;
import java.util.HashMap;
import java.util.Map;

import org.testng.annotations.Test;

import com.ak.api.data.FakeData;
import com.ak.api.models.Post;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.retry.RetryAnalyzer;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Faker-Driven Posts API")
public class FakerPostsTest extends BaseApiTest {

    private static final String TEMPLATE_DIR = "templates/";

    @Test(groups = {"regression", "posts", "faker"},
          retryAnalyzer = RetryAnalyzer.class)
    @Story("POST /posts with a Faker-generated body")
    @Description("Uses FakeData.postDataMap() to fabricate title/body/userId/published, substitutes into createPost.json in strict mode, POSTs, and asserts the server echoed back the exact generated values.")
    public void post_fromFakerData_roundTrips() throws Exception {
        Map<String, String> row = FakeData.postDataMap();

        InputStreamReader template = RestUtilities.getRequestTemplate(TEMPLATE_DIR, "createPost.json");
        String payload = RestUtilities.mapJsonValues(template, row, /* strict = */ true);

        String testCaseId = "FAKER-" + row.get("userId");
        RestUtilities.logRequestBody(testCaseId, holder, payload);

        Response res = RestUtilities.getResponsePost(payload, baseUrl() + "/posts", new HashMap<>());
        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "201",
                "expected HTTP 201 Created");

        Post created = RestUtilities.as(res, Post.class);
        softAssert.assertEquals(created.getTitle(), row.get("title"),
                "server echoed the faker-generated title");
        softAssert.assertEquals(created.getBody(), row.get("body"),
                "server echoed the faker-generated body");
        softAssert.assertEquals(created.getUserId(), Integer.valueOf(row.get("userId")),
                "server echoed the faker-generated userId");
    }

    @Test(groups = {"regression", "faker", "unit"})
    @Story("FakeData produces distinct values by default, deterministic with -Dfake.seed")
    @Description("Two calls to FakeData.email() return different values -- proves the RNG is seeded fresh per JVM. To reproduce a failure, run with -Dfake.seed=<long>.")
    public void fakeData_producesDistinctEmailsByDefault() {
        String a = FakeData.email();
        String b = FakeData.email();
        softAssert.assertNotNull(a, "first email non-null");
        softAssert.assertNotNull(b, "second email non-null");
        softAssert.assertNotEquals(a, b,
                "two consecutive fake emails should differ (statistically -- vanishingly small chance of collision)");
    }
}
