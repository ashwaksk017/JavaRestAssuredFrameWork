// =============================================================================
// HeadersFromFileTests
// -----------------------------------------------------------------------------
// Exercises the three file-backed header loaders on Headers:
//   * fromJson(...)        -> src/test/resources/headers/qa.json
//   * fromProperties(...)  -> src/test/resources/headers/qa.properties
//   * fromText(...)        -> src/test/resources/headers/qa.txt
//   * fromFile(...)        -> auto-detect by extension
//
// The final test proves the file-loaded map actually reaches the server on the
// wire (echoed via httpbin /headers), so this covers both unit-level loader
// correctness AND end-to-end integration.
// =============================================================================

package com.ak.api.tests;

import java.util.Map;

import org.testng.annotations.Test;

import com.ak.api.rest.utilities.Headers;
import com.ak.api.rest.utilities.RestUtilities;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Dynamic Header Loading")
public class HeadersFromFileTests extends BaseApiTest {

    private static final String HTTPBIN = "https://httpbin.org";

    // Expected values common to all three test fixtures.
    private static final String EXP_ENV      = "qa";
    private static final String EXP_API_KEY  = "qa-api-key-abc123";
    private static final String EXP_TENANT   = "acme-qa";
    private static final String EXP_CLIENT   = "api-automation-restassured";

    // ------------------------------------------------------------------ JSON

    @Test(groups = {"headers", "loaders"})
    @Story("Load headers from JSON file")
    @Description("Headers.fromJson reads a classpath JSON file into an ordered map with the four env-scoped headers.")
    public void fromJson_loadsAllFourHeaders() {
        Map<String, String> h = Headers.fromJson("headers/qa.json");
        assertQaFixture(h);
    }

    // ------------------------------------------------------------ Properties

    @Test(groups = {"headers", "loaders"})
    @Story("Load headers from .properties file")
    @Description("Headers.fromProperties reads a classpath .properties file into an ordered map, ignoring # comment lines.")
    public void fromProperties_loadsAllFourHeaders_ignoringComments() {
        Map<String, String> h = Headers.fromProperties("headers/qa.properties");
        assertQaFixture(h);
    }

    // ------------------------------------------------------------------ Text

    @Test(groups = {"headers", "loaders"})
    @Story("Load headers from HTTP-style text file")
    @Description("Headers.fromText reads 'Name: value' lines into an ordered map, ignoring # comment lines and blanks.")
    public void fromText_loadsAllFourHeaders_httpStyle() {
        Map<String, String> h = Headers.fromText("headers/qa.txt");
        assertQaFixture(h);
    }

    // ---------------------------------------------------- Auto-detect by ext

    @Test(groups = {"headers", "loaders"})
    @Story("Auto-detect file format by extension")
    @Description("Headers.fromFile dispatches to the right loader based on .json / .properties / other extension.")
    public void fromFile_autoDetectsByExtension() {
        assertQaFixture(Headers.fromFile("headers/qa.json"));
        assertQaFixture(Headers.fromFile("headers/qa.properties"));
        assertQaFixture(Headers.fromFile("headers/qa.txt"));
    }

    // -------------------------------------------- Round-trip through httpbin

    @Test(groups = {"headers", "loaders"})
    @Story("File-loaded headers reach the server on the wire")
    @Description("Load env headers from qa.json via Headers.builder().fromFile(...), send GET to httpbin /headers, assert every header echoes back.")
    public void fromFile_roundTripsToServer() {
        Map<String, String> h = Headers.builder()
                .acceptJson()
                .fromFile("headers/qa.json")   // env-scoped stack
                .correlationId()               // per-request override
                .build();

        Response res = RestUtilities.getResponseGet(HTTPBIN + "/headers", h);
        ExternalService.skipIfUnavailable(res);

        softAssert.assertEquals(res.statusCode(), 200,
                "expected 200 from /headers");
        softAssert.assertEquals(res.jsonPath().getString("headers.X-Env"),
                EXP_ENV, "X-Env preserved on wire");
        softAssert.assertEquals(res.jsonPath().getString("headers.X-Api-Key"),
                EXP_API_KEY, "X-Api-Key preserved on wire");
        softAssert.assertEquals(res.jsonPath().getString("headers.X-Tenant-Id"),
                EXP_TENANT, "X-Tenant-Id preserved on wire");
        softAssert.assertEquals(res.jsonPath().getString("headers.X-Client-Name"),
                EXP_CLIENT, "X-Client-Name preserved on wire");
    }

    // ---- shared assertion ----

    private void assertQaFixture(Map<String, String> h) {
        softAssert.assertEquals(h.get("X-Env"),         EXP_ENV,      "X-Env");
        softAssert.assertEquals(h.get("X-Api-Key"),     EXP_API_KEY,  "X-Api-Key");
        softAssert.assertEquals(h.get("X-Tenant-Id"),   EXP_TENANT,   "X-Tenant-Id");
        softAssert.assertEquals(h.get("X-Client-Name"), EXP_CLIENT,   "X-Client-Name");
        softAssert.assertEquals(h.size(), 4, "no extra keys in fixture");
    }
}
