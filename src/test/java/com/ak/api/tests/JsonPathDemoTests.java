// =============================================================================
// JsonPathDemoTests
// -----------------------------------------------------------------------------
// Focused demo of the harder JsonPath surface -- the shapes teams actually hit
// in real API tests but that the smoke tests don't exercise:
//
//   * Nested-object navigation           -- users/1.address.geo.lat
//   * Wildcard collect                    -- users.email  (implicit list)
//   * Filter by predicate                 -- posts.findAll { it.userId == 1 }
//   * Find first match                    -- posts.find { it.id == 42 }
//   * Aggregations                        -- posts.id.max()  posts*.userId.sum()
//   * groupBy                             -- posts.groupBy { it.userId }.size()
//   * every / any predicates              -- users.every { it.email.contains('@') }
//   * Hamcrest matcher inline             -- .body("users.size()", equalTo(10))
//   * Standard JsonPath spec ($.notation) -- com.jayway.jsonpath.JsonPath
//                                            for deep-scan (..name) and filter
//                                            predicates ($.[?(@.x=='y')])
//
// Two JsonPath flavours coexist in REST Assured -- both are valid, teams pick
// per convention:
//   1. io.restassured.path.json.JsonPath  -- Groovy GPath (default of
//      .jsonPath()). Rich, closure-based, imperative-feeling.
//   2. com.jayway.jsonpath.JsonPath       -- Standard JsonPath spec used by
//      Postman / Katalon / most online JsonPath evaluators. $-anchored.
//
// The tests below intentionally mix both so a reader learns each style side
// by side.
// =============================================================================

package com.ak.api.tests;

import static org.hamcrest.Matchers.equalTo;
import static org.hamcrest.Matchers.greaterThan;
import static org.hamcrest.Matchers.hasItem;

import java.util.List;
import java.util.Map;

import org.testng.annotations.Test;

import com.ak.api.rest.utilities.RestUtilities;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.path.json.JsonPath;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("JsonPath Depth Coverage")
public class JsonPathDemoTests extends BaseApiTest {

    // ------------------------------------------------------------------------
    // 1. Nested-object navigation -- single-user response, dive 3 levels deep.
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("Nested navigation -- address.geo.lat + company.catchPhrase")
    @Description("GET /users/1: assert values 2-3 levels deep using dot-notation JsonPath.")
    public void nested_addressGeoAndCompanyCatchPhrase() {
        Response res = RestUtilities.getResponseGet(baseUrl() + "/users/1", new java.util.HashMap<>());

        softAssert.assertEquals(res.statusCode(), 200, "GET /users/1 -> 200");
        softAssert.assertEquals(res.jsonPath().getString("address.city"), "Gwenborough",
                "address.city");
        softAssert.assertEquals(res.jsonPath().getString("address.geo.lat"), "-37.3159",
                "address.geo.lat");
        softAssert.assertEquals(res.jsonPath().getString("company.name"), "Romaguera-Crona",
                "company.name");
        softAssert.assertEquals(res.jsonPath().getString("company.catchPhrase"),
                "Multi-layered client-server neural-net",
                "company.catchPhrase");
    }

    // ------------------------------------------------------------------------
    // 2. Wildcard collect over a list -- shortcut form users.email == users*.email
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("Wildcard collect -- all emails from /users")
    @Description("GPath shortcut 'users.email' collects the email of every user into a List<String>. Assert count + specific membership.")
    public void wildcardCollect_allUserEmails() {
        Response res = RestUtilities.getResponseGet(baseUrl() + "/users", new java.util.HashMap<>());

        List<String> emails = res.jsonPath().getList("email");
        softAssert.assertEquals(emails.size(), 10, "10 users returned by jsonplaceholder");
        softAssert.assertTrue(emails.contains("Sincere@april.biz"),
                "user 1's email is in the collected list");

        // Equivalent Hamcrest-style assertion via body() -- reads more like a spec:
        res.then().body("email", hasItem("Sincere@april.biz"));
        res.then().body("email.size()", equalTo(10));
    }

    // ------------------------------------------------------------------------
    // 3. Filter by predicate -- findAll {} closure returns matching sublist.
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("Filter -- posts.findAll { it.userId == 1 }")
    @Description("GET /posts. Filter to just user 1's posts using a GPath closure. jsonplaceholder gives each user exactly 10 posts.")
    public void filter_postsByUserIdOne() {
        Response res = RestUtilities.getResponseGet(baseUrl() + "/posts", new java.util.HashMap<>());

        int userOnesPosts = res.jsonPath().getInt("findAll { it.userId == 1 }.size()");
        softAssert.assertEquals(userOnesPosts, 10, "user 1 has 10 posts");

        // Same assertion via body() with a Hamcrest matcher:
        res.then().body("findAll { it.userId == 1 }.size()", equalTo(10));
    }

    // ------------------------------------------------------------------------
    // 4. Find first match -- .find {} returns a single object (or null).
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("Find first -- posts.find { it.id == 42 }")
    @Description("Retrieve one post by id via GPath find, assert its userId + title match jsonplaceholder's fixture.")
    public void findFirst_postById42() {
        Response res = RestUtilities.getResponseGet(baseUrl() + "/posts", new java.util.HashMap<>());

        // .find { closure } returns the first matching map, then we dot into it.
        Integer userId    = res.jsonPath().get("find { it.id == 42 }.userId");
        String  title     = res.jsonPath().getString("find { it.id == 42 }.title");

        softAssert.assertEquals(userId, Integer.valueOf(5),
                "post 42 belongs to user 5 in the jsonplaceholder fixture");
        softAssert.assertNotNull(title, "title of post 42 is not null");
        softAssert.assertFalse(title.isBlank(), "title of post 42 is non-blank");
    }

    // ------------------------------------------------------------------------
    // 5. Aggregation -- max() on a numeric field, spread with *. operator.
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("Aggregation -- posts*.id.max()  +  posts*.userId.sum()")
    @Description("jsonplaceholder ships exactly 100 posts (ids 1..100) across 10 users. Assert max(id)=100 and sum(userId) = 1*10 + 2*10 + ... + 10*10 = 550.")
    public void aggregate_maxIdAndSumOfUserIds() {
        Response res = RestUtilities.getResponseGet(baseUrl() + "/posts", new java.util.HashMap<>());

        int maxId = res.jsonPath().getInt("max { it.id }.id");
        softAssert.assertEquals(maxId, 100, "max post id");

        int sumOfUserIds = res.jsonPath().getInt("collect { it.userId }.sum()");
        softAssert.assertEquals(sumOfUserIds, 550,
                "sum of userIds across 100 posts = (1+2+..+10)*10 = 550");
    }

    // ------------------------------------------------------------------------
    // 6. groupBy -- partition the list by a key, assert bucket count.
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("groupBy -- posts.groupBy { it.userId }")
    @Description("Partition all 100 posts by userId. There should be exactly 10 groups, one per user. Each bucket must have 10 posts.")
    public void groupBy_postsByUserId() {
        Response res = RestUtilities.getResponseGet(baseUrl() + "/posts", new java.util.HashMap<>());

        int groupCount = res.jsonPath().getInt("groupBy { it.userId }.size()");
        softAssert.assertEquals(groupCount, 10, "10 distinct userIds => 10 groups");

        // Every bucket has 10 posts -- Groovy's collect + .every returns a boolean.
        Boolean allBucketsHave10 = res.jsonPath().get(
                "groupBy { it.userId }.values().every { it.size() == 10 }");
        softAssert.assertTrue(Boolean.TRUE.equals(allBucketsHave10),
                "every user has exactly 10 posts");
    }

    // ------------------------------------------------------------------------
    // 7. every / any -- boolean quantifiers over a list.
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("every / any -- users.every { valid email }")
    @Description("Assert every user has a well-formed email (contains '@') and at least one company catchPhrase mentions 'multi-layered'.")
    public void every_and_any_predicatesOverUsers() {
        Response res = RestUtilities.getResponseGet(baseUrl() + "/users", new java.util.HashMap<>());

        Boolean everyEmailHasAt = res.jsonPath().get("every { it.email.contains('@') }");
        softAssert.assertTrue(Boolean.TRUE.equals(everyEmailHasAt),
                "every user's email contains '@'");

        Boolean anyCompanyMultiLayered = res.jsonPath().get(
                "any { it.company.catchPhrase.toLowerCase().contains('multi-layered') }");
        softAssert.assertTrue(Boolean.TRUE.equals(anyCompanyMultiLayered),
                "at least one company catchPhrase mentions 'multi-layered'");
    }

    // ------------------------------------------------------------------------
    // 8. Combined -- filter + collect + max, chained.
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("Chained -- longest title among user 1's posts")
    @Description("Filter to user 1's posts, then find the one with the longest title. Purely GPath, no server-side query.")
    public void chained_longestTitleAmongUserOnePosts() {
        Response res = RestUtilities.getResponseGet(baseUrl() + "/posts", new java.util.HashMap<>());

        int longestTitleLen = res.jsonPath().getInt(
                "findAll { it.userId == 1 }.collect { it.title.length() }.max()");
        softAssert.assertTrue(longestTitleLen > 20,
                "longest title among user 1's posts is over 20 chars (actual=" + longestTitleLen + ")");

        // Retrieve the actual longest-title post -- comparator-based max.
        String longestTitle = res.jsonPath().getString(
                "findAll { it.userId == 1 }.max { it.title.length() }.title");
        softAssert.assertNotNull(longestTitle, "longest-title post exists");
        softAssert.assertEquals(longestTitle.length(), longestTitleLen,
                "max()-by-length picks a post whose title length matches");
    }

    // ------------------------------------------------------------------------
    // 9. Standard JsonPath spec ($.notation) via com.jayway.jsonpath.JsonPath.
    //    Used when you want portability with Postman / Katalon / Insomnia
    //    tests, or when the .$..deep.scan operator is genuinely needed.
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("Standard JsonPath ($.notation) -- deep scan + filter predicate")
    @Description("Same /users payload, evaluated with com.jayway.jsonpath. Deep-scan ..name pulls both user.name and company.name; filter $.[?(@.username=='Bret')] picks the user whose username is Bret.")
    public void jsonPathSpec_deepScanAndFilter() {
        Response res = RestUtilities.getResponseGet(baseUrl() + "/users", new java.util.HashMap<>());
        String raw = res.body().asString();

        // Deep-scan: every "name" field anywhere in the tree. Each user contributes
        // one at user.name plus one at company.name -> 10 users * 2 = 20 names.
        List<Object> allNames = com.jayway.jsonpath.JsonPath.read(raw, "$..name");
        softAssert.assertEquals(allNames.size(), 20,
                "deep scan '$..name' should hit user.name + company.name for all 10 users");

        // Filter predicate: pick the user by username == 'Bret' (that's user 1).
        List<Object> brets = com.jayway.jsonpath.JsonPath.read(raw, "$.[?(@.username == 'Bret')]");
        softAssert.assertEquals(brets.size(), 1, "exactly one Bret");

        // Extract a scalar from a filter result -- Jayway wraps filter output in a
        // JSONArray even when there's only one match, so read the .email list and
        // pull index 0 in Java rather than in the path.
        List<String> bretEmails = com.jayway.jsonpath.JsonPath.read(
                raw, "$.[?(@.username == 'Bret')].email");
        softAssert.assertEquals(bretEmails.size(), 1, "one Bret => one email");
        softAssert.assertEquals(bretEmails.get(0), "Sincere@april.biz", "Bret's email");
    }

    // ------------------------------------------------------------------------
    // 10. body() with Hamcrest -- terse assertion style, no intermediate vars.
    // ------------------------------------------------------------------------
    @Test(groups = {"regression", "jsonpath"})
    @Story("Hamcrest inline -- body('users.size()', equalTo(10))")
    @Description("Prefer this style when the assertion is a one-liner and reads well aloud. Combining multiple body() calls also produces a nicer failure message than manual asserts.")
    public void hamcrest_inlineAssertionsAgainstUsersList() {
        RestUtilities.getResponseGet(baseUrl() + "/users", new java.util.HashMap<>())
                .then()
                .statusCode(200)
                .body("size()", equalTo(10))
                .body("[0].id", equalTo(1))
                .body("[0].address.geo.lat", equalTo("-37.3159"))
                .body("collect { it.userId = null; it.id }.max()", greaterThan(1))
                .body("find { it.username == 'Bret' }.email", equalTo("Sincere@april.biz"));
    }
}
