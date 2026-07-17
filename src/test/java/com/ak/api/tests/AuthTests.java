// =============================================================================
// AuthTests
// -----------------------------------------------------------------------------
// Exercises AuthUtilities.bearer() / AuthUtilities.basic() end-to-end against
// httpbin.org -- a public HTTP echo service that verifies auth headers and
// echoes them back so we can prove the Authorization header actually reached
// the server.
//
// Endpoints used:
//   GET /bearer                    -> 200 with any Bearer token, 401 without
//   GET /basic-auth/{user}/{pass}  -> 200 when Basic creds match, 401 otherwise
//   GET /headers                   -> echoes every request header back
//
// These tests inherit BaseApiTest so:
//   * the Allure + recording filters attach (auth calls appear in both reports)
//   * the per-test holder + softAssert + banner-log lifecycle is preserved
// They fully-qualify httpbin URLs so RestAssured bypasses the jsonplaceholder
// base URI configured in bootstrap.
//
// Environmental caveat: httpbin.org is external. If someone runs the suite
// with -Dauth.type=... set, the default RequestSpec already carries an
// Authorization header and bearer_negative_missing will (correctly) fail --
// the auth-negative case assumes the framework default of auth.type=none.
// =============================================================================

package com.ak.api.tests;

import java.util.Map;
import java.util.UUID;

import org.testng.annotations.Test;

import com.ak.api.auth.AuthUtilities;
import com.ak.api.rest.utilities.Headers;
import com.ak.api.rest.utilities.RestUtilities;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.RestAssured;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Auth Header Wiring")
public class AuthTests extends BaseApiTest {

    private static final String HTTPBIN = "https://httpbin.org";

    // ---------------------------------------------------------------- Bearer

    @Test(groups = {"auth", "bearer"})
    @Story("Bearer token accepted")
    @Description("AuthUtilities.bearer(token) produces an Authorization header that httpbin /bearer accepts. Asserts 200 + echoed token.")
    public void bearer_positive_validToken() {
        String token = "test-bearer-token-123";
        String header = AuthUtilities.bearer(token);

        Response res = ExternalService.runOrSkip(() -> RestAssured.given()
                .header("Authorization", header)
                .when()
                .get(HTTPBIN + "/bearer"));

        softAssert.assertEquals(res.statusCode(), 200,
                "expected 200 with valid Bearer token");
        softAssert.assertTrue(res.jsonPath().getBoolean("authenticated"),
                "authenticated=true expected");
        softAssert.assertEquals(res.jsonPath().getString("token"), token,
                "token echoed back verbatim");
    }

    @Test(groups = {"auth", "bearer"})
    @Story("Bearer header absent rejected")
    @Description("Confirms /bearer rejects requests missing the Authorization header with 401.")
    public void bearer_negative_missingHeader() {
        Response res = ExternalService.runOrSkip(() -> RestAssured.given()
                .when()
                .get(HTTPBIN + "/bearer"));

        softAssert.assertEquals(res.statusCode(), 401,
                "expected 401 when Authorization header is absent");
    }

    // ----------------------------------------------------------------- Basic

    @Test(groups = {"auth", "basic"})
    @Story("Basic credentials accepted")
    @Description("AuthUtilities.basic(user,pass) produces an Authorization header that /basic-auth/{u}/{p} accepts. Asserts 200 + echoed user.")
    public void basic_positive_matching() {
        String user = "svc_account";
        String pass = "super_secret";
        String header = AuthUtilities.basic(user, pass);

        Response res = ExternalService.runOrSkip(() -> RestAssured.given()
                .header("Authorization", header)
                .when()
                .get(HTTPBIN + "/basic-auth/" + user + "/" + pass));

        softAssert.assertEquals(res.statusCode(), 200,
                "expected 200 with matching Basic creds");
        softAssert.assertTrue(res.jsonPath().getBoolean("authenticated"),
                "authenticated=true expected");
        softAssert.assertEquals(res.jsonPath().getString("user"), user,
                "user echoed back");
    }

    @Test(groups = {"auth", "basic"})
    @Story("Basic wrong password rejected")
    @Description("/basic-auth returns 401 when the password does not match the URL segment.")
    public void basic_negative_wrongPassword() {
        String user = "svc_account";
        String header = AuthUtilities.basic(user, "wrong-password");

        Response res = ExternalService.runOrSkip(() -> RestAssured.given()
                .header("Authorization", header)
                .when()
                .get(HTTPBIN + "/basic-auth/" + user + "/correct-password"));

        softAssert.assertEquals(res.statusCode(), 401,
                "expected 401 with wrong Basic password");
    }

    // ----------------------------------------------- Interceptor round-trip

    @Test(groups = {"auth", "bearer"})
    @Story("Authorization header reaches the server on the wire")
    @Description("Uses httpbin /headers to echo request headers back so we can prove the Bearer header made it out of the JVM (not silently dropped by a filter).")
    public void header_roundTripsToServer() {
        String token = "visible-round-trip-token";
        String header = AuthUtilities.bearer(token);

        Response res = ExternalService.runOrSkip(() -> RestAssured.given()
                .header("Authorization", header)
                .when()
                .get(HTTPBIN + "/headers"));

        softAssert.assertEquals(res.statusCode(), 200,
                "expected 200 from /headers");
        String echoed = res.jsonPath().getString("headers.Authorization");
        softAssert.assertEquals(echoed, "Bearer " + token,
                "Authorization header preserved on the wire");
    }

    // ------------------------------------------- Headers.builder composition

    @Test(groups = {"auth", "headers"})
    @Story("Headers.builder composes a full header map that reaches the server")
    @Description("Uses Headers.builder() to compose Bearer + Accept + X-Correlation-Id and passes the resulting map into RestUtilities.getResponseGet. Asserts all three echo back through /headers.")
    public void headersBuilder_composesAndRoundTrips() {
        String token   = "builder-composed-token";
        UUID   corrId  = UUID.randomUUID();

        Map<String, String> h = Headers.builder()
                .bearer(token)
                .acceptJson()
                .correlationId(corrId)
                .header("X-Tenant-Id", "acme-corp")
                .build();

        Response res = ExternalService.runOrSkip(() ->
                RestUtilities.getResponseGet(HTTPBIN + "/headers", h));

        softAssert.assertEquals(res.statusCode(), 200,
                "expected 200 from /headers");
        softAssert.assertEquals(res.jsonPath().getString("headers.Authorization"),
                "Bearer " + token, "Authorization preserved on wire");
        softAssert.assertEquals(res.jsonPath().getString("headers.Accept"),
                "application/json", "Accept preserved on wire");
        softAssert.assertEquals(res.jsonPath().getString("headers.X-Correlation-Id"),
                corrId.toString(), "X-Correlation-Id preserved on wire");
        softAssert.assertEquals(res.jsonPath().getString("headers.X-Tenant-Id"),
                "acme-corp", "custom X-Tenant-Id preserved on wire");
    }
}
