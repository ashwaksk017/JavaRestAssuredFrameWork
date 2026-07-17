// =============================================================================
// PostsSmokeTests
// -----------------------------------------------------------------------------
// Smoke coverage of every HTTP verb against jsonplaceholder.
//
// Improvements over v1:
//   * JsonPath-based asserts on typed fields (P1.13) -- no more brittle
//     containsStringPattern() substring hunts
//   * Test groups (P1.14) -- @Test(groups = {"smoke", "posts"}), selectable
//     from the suite XML
//   * Allure @Epic/@Feature/@Story/@Description annotations decorate the
//     tree in the Allure report
//   * Automatic retry on transient failures via RetryAnalyzer
//   * JSON schema validation for the single-post GET (P1.8)
//   * Response-time SLO assertion (P1.10)
//   * POJO deserialization example (P1.9)
// =============================================================================

package com.ak.api.tests;

import java.util.HashMap;
import java.util.List;

import org.testng.annotations.Test;

import com.ak.api.models.Post;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.retry.RetryAnalyzer;
import com.ak.api.schema.SchemaValidator;
import com.ak.api.xray.XrayTest;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Severity;
import io.qameta.allure.SeverityLevel;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Posts API")
public class PostsSmokeTests extends BaseApiTest {

    // -----------------------------------------------------------------------
    // GET /posts/1  -- typed POJO + schema validation + response-time SLO
    // -----------------------------------------------------------------------
    // Example of annotation-based Xray sync for a non-data-driven test.
    // When xray.enabled=true and creds are configured, this test's outcome
    // is reported against Jira ticket PROJ-DEMO-1. Replace with your real
    // Jira Xray test key.
    @Test(groups = {"smoke", "posts"}, retryAnalyzer = RetryAnalyzer.class)
    @XrayTest("PROJ-DEMO-1")
    @Story("GET single post")
    @Severity(SeverityLevel.CRITICAL)
    @Description("GET /posts/1 -- shape matches Post POJO, JSON schema valid, response under 3000ms")
    public void get_singlePost_matchesPojoAndSchema() {
        String testCaseId = "SMOKE-GET-01";

        Response res = RestUtilities.getResponseGet(baseUrl() + "/posts/1", new HashMap<>());

        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        // Status + response time
        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200", "status code");
        RestUtilities.assertResponseTimeBelow(res, 3000);

        // JSON schema validation
        SchemaValidator.validate(res, "post-schema.json");

        // POJO deserialization + typed asserts (no substring matching)
        Post post = RestUtilities.as(res, Post.class);
        softAssert.assertEquals(post.getId(),     Integer.valueOf(1), "id");
        softAssert.assertEquals(post.getUserId(), Integer.valueOf(1), "userId");
        softAssert.assertNotNull(post.getTitle(),                    "title present");
        softAssert.assertNotNull(post.getBody(),                     "body present");
    }

    // -----------------------------------------------------------------------
    // POST /posts  -- server echoes back with id=101
    // -----------------------------------------------------------------------
    @Test(groups = {"smoke", "posts"}, retryAnalyzer = RetryAnalyzer.class)
    @Story("Create post")
    @Severity(SeverityLevel.CRITICAL)
    @Description("POST /posts -- created, server echoes fields, id assigned")
    public void post_newPost_returns201WithEchoedFields() {
        String testCaseId = "SMOKE-POST-01";

        Post payload = new Post(null, 1, "hello", "world");
        String body = RestUtilities.toJson(payload);

        RestUtilities.logRequestBody(testCaseId, holder, body);

        Response res = RestUtilities.getResponsePost(body, baseUrl() + "/posts", new HashMap<>());
        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "201", "expected HTTP 201 Created");

        Post created = RestUtilities.as(res, Post.class);
        softAssert.assertEquals(created.getTitle(), "hello",           "title echoed");
        softAssert.assertEquals(created.getBody(),  "world",           "body echoed");
        softAssert.assertEquals(created.getUserId(), Integer.valueOf(1), "userId echoed");
        softAssert.assertNotNull(created.getId(),                     "server assigned an id");
    }

    // -----------------------------------------------------------------------
    // PUT /posts/1
    // -----------------------------------------------------------------------
    @Test(groups = {"smoke", "posts"}, retryAnalyzer = RetryAnalyzer.class)
    @Story("Full update")
    @Severity(SeverityLevel.NORMAL)
    public void put_updatePost_returns200WithUpdatedTitle() {
        String testCaseId = "SMOKE-PUT-01";

        Post payload = new Post(1, 1, "updated", "updated body");
        String body = RestUtilities.toJson(payload);

        RestUtilities.logRequestBody(testCaseId, holder, body);

        Response res = RestUtilities.getResponsePut(body, baseUrl() + "/posts/1", new HashMap<>());
        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200", "status code");

        Post updated = RestUtilities.as(res, Post.class);
        softAssert.assertEquals(updated.getTitle(), "updated", "title");
        softAssert.assertEquals(updated.getBody(),  "updated body", "body");
    }

    // -----------------------------------------------------------------------
    // PATCH /posts/1
    // -----------------------------------------------------------------------
    @Test(groups = {"smoke", "posts"})
    @Story("Partial update")
    public void patch_partialUpdate_returns200() {
        String testCaseId = "SMOKE-PATCH-01";

        String body = "{\"title\":\"only-title-changed\"}";
        RestUtilities.logRequestBody(testCaseId, holder, body);

        Response res = RestUtilities.getResponsePatch(body, baseUrl() + "/posts/1", new HashMap<>());
        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200", "status code");
        // JsonPath-typed assert on the field we actually changed
        softAssert.assertEquals(res.jsonPath().getString("title"),
                "only-title-changed", "title reflects PATCH");
    }

    // -----------------------------------------------------------------------
    // DELETE /posts/1
    // -----------------------------------------------------------------------
    @Test(groups = {"smoke", "posts"})
    @Story("Delete")
    public void delete_post_returns200() {
        String testCaseId = "SMOKE-DELETE-01";

        Response res = RestUtilities.getResponseDelete(baseUrl() + "/posts/1", new HashMap<>());
        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200", "status code");
    }

    // -----------------------------------------------------------------------
    // GET /posts?userId=1  -- typed list + count assertion
    // -----------------------------------------------------------------------
    @Test(groups = {"regression", "posts"})
    @Story("Filter by userId")
    @Description("GET /posts?userId=1 -- typed list of 10 items, all belonging to user 1")
    public void get_filteredByUserId_returnsExpectedCount() {
        String testCaseId = "REGRESSION-GET-02";

        Response res = RestUtilities.getResponseGet(baseUrl() + "/posts?userId=1", new HashMap<>());
        RestUtilities.logResponseBody(testCaseId, holder, RestUtilities.getResponseAsString(res));

        softAssert.assertEquals(RestUtilities.checkActualResponse(res), "200", "status code");

        List<Integer> userIds = res.jsonPath().getList("userId", Integer.class);
        softAssert.assertEquals(userIds.size(), 10, "expected 10 posts for userId=1");
        softAssert.assertTrue(userIds.stream().allMatch(u -> u == 1), "all posts belong to userId=1");
    }
}
