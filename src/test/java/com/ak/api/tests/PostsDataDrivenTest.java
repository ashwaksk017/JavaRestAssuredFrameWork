// =============================================================================
// PostsDataDrivenTest
// -----------------------------------------------------------------------------
// Same placeholder pipeline as the reference:
//   1. Template on classpath (src/main/resources/templates/createPost.json)
//   2. Per-case dataMap (TestNG @DataProvider here; Excel/CSV/DB in real projects)
//   3. RestUtilities.mapJsonValues(reader, dataMap, strict=true)
//        #key#     -> value   (unresolved -> exception in strict mode)
//        "%key%"   -> value   (unresolved -> exception in strict mode)
//        "@key@"   -> value   (unresolved -> exception in strict mode)
//   4. POST resolved payload; assert on typed echoed fields via JsonPath / POJO.
//
// Strict mode is the recommended default now -- unresolved placeholders should
// blow up loudly rather than silently substitute null/false/0.
// =============================================================================

package com.ak.api.tests;

import java.io.InputStreamReader;
import java.util.HashMap;
import java.util.Map;

import org.testng.annotations.DataProvider;
import org.testng.annotations.Test;

import com.ak.api.models.Post;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.retry.RetryAnalyzer;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Data-Driven Posts API")
public class PostsDataDrivenTest extends BaseApiTest {

    private static final String TEMPLATE_DIR = "templates/";

    @DataProvider(name = "postCases")
    public Object[][] postCases() {
        return new Object[][]{
                // testCaseId, title, body, userId, published
                {"DD-01", "First post",     "hello world",         "1", "true"},
                {"DD-02", "Second post",    "another body",        "2", "false"},
                {"DD-03", "Third post",     "with spaces",         "3", "true"},
        };
    }

    @Test(dataProvider = "postCases",
          groups = {"regression", "posts", "data-driven"},
          retryAnalyzer = RetryAnalyzer.class)
    @Story("Create post from template")
    @Description("Loads createPost.json template, substitutes placeholders in strict mode, POSTs, asserts echoed fields via POJO deserialization.")
    public void post_fromTemplate_echoesFields(String testCaseId,
                                               String title,
                                               String body,
                                               String userId,
                                               String published) throws Exception {

        // 1. Build per-case data map
        Map<String, String> dataMap = new HashMap<>();
        dataMap.put("title", title);
        dataMap.put("body", body);
        dataMap.put("userId", userId);
        dataMap.put("published", published);

        // 2. Load template + resolve placeholders in STRICT mode
        InputStreamReader template = RestUtilities.getRequestTemplate(TEMPLATE_DIR, "createPost.json");
        String payload = RestUtilities.mapJsonValues(template, dataMap, /* strict = */ true);

        RestUtilities.logRequestBody(testCaseId, holder, payload);

        // 3. POST
        Response res = RestUtilities.getResponsePost(payload, baseUrl() + "/posts", new HashMap<>());
        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        // 4. Typed asserts
        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "201",
                "expected HTTP 201 Created for testCaseId=" + testCaseId);

        Post created = RestUtilities.as(res, Post.class);
        softAssert.assertEquals(created.getTitle(), title,
                "title echoed for testCaseId=" + testCaseId);
        softAssert.assertEquals(created.getBody(), body,
                "body echoed for testCaseId=" + testCaseId);
        softAssert.assertEquals(created.getUserId(), Integer.valueOf(userId),
                "userId echoed for testCaseId=" + testCaseId);
    }
}
