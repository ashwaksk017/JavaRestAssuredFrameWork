// =============================================================================
// DemoWebshopLoginTest -- live login against demowebshop.tricentis.com
// -----------------------------------------------------------------------------
// Tricentis' public demo shop uses a classic ASP.NET-style form-encoded login
// with cookie-based session, no JSON tokens. That makes it a great teaching
// example because it exercises three real behaviours the JSON-token tests
// don't cover:
//
//   1. redirect handling  -- success = 302 Found + Location: /
//                            failure = 200 OK + error in HTML body
//   2. cookie assertions  -- successful login sets NOPCOMMERCE.AUTH; missing
//                            cookie on wrong-password is the negative signal
//   3. session reuse      -- carry cookies to a protected GET to prove the
//                            login actually authenticated the client
//
// Contract (probed on 2026-07-16):
//   POST {baseUrl}/login   Content-Type: application/x-www-form-urlencoded
//        body: Email=<...>&Password=<...>&RememberMe=false
//
//   success -> 302, Location: /
//              Set-Cookie: NOPCOMMERCE.AUTH=<hex>; HttpOnly
//              Set-Cookie: Nop.customer=<uuid>; HttpOnly
//   failure -> 200, HTML body contains
//              "Login was unsuccessful. Please correct the errors and try again."
//              (no NOPCOMMERCE.AUTH cookie set)
//
// Credentials: read from Config -- NEVER hard-coded.
//   -Ddemoshop.username=... -Ddemoshop.password=...
//   DEMOSHOP_USERNAME=... DEMOSHOP_PASSWORD=...
// If either is blank, the test SKIPs (does not fail).
// =============================================================================

package com.ak.api.tests;

import java.util.Map;

import org.testng.SkipException;
import org.testng.annotations.Test;

import com.ak.api.config.Config;
import com.ak.api.rest.utilities.Headers;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.RestAssured;
import io.restassured.response.Response;

@Epic("API Automation")
@Feature("Tricentis DemoWebshop Login")
public class DemoWebshopLoginTest extends BaseApiTest {

    private static final String AUTH_COOKIE = "NOPCOMMERCE.AUTH";
    private static final String CUSTOMER_COOKIE = "Nop.customer";
    private static final String LOGIN_ERROR_MARKER = "Login was unsuccessful";

    // -------------------------------------------- positive: valid credentials

    @Test(groups = {"demoshop", "login"})
    @Story("Valid credentials -> 302 redirect + NOPCOMMERCE.AUTH cookie")
    @Description("POSTs form-encoded credentials to /login. Successful login should return HTTP 302 to '/', set a NOPCOMMERCE.AUTH cookie, and finish under 3s.")
    public void login_validCredentials_returns302AndAuthCookie() {
        String user = requireCred("demoshop.username", "DEMOSHOP_USERNAME");
        String pass = requireCred("demoshop.password", "DEMOSHOP_PASSWORD");

        Response res = postLogin(user, pass);
        ExternalService.skipIfUnavailable(res);

        softAssert.assertEquals(res.statusCode(), 302,
                "successful login should return HTTP 302 Found");
        softAssert.assertEquals(res.header("Location"), "/",
                "Location should redirect to home '/'");

        String authCookie = res.cookie(AUTH_COOKIE);
        softAssert.assertNotNull(authCookie,
                AUTH_COOKIE + " cookie must be present on successful login");
        softAssert.assertTrue(authCookie != null && !authCookie.isBlank(),
                AUTH_COOKIE + " cookie must be non-blank");
        softAssert.assertNotNull(res.cookie(CUSTOMER_COOKIE),
                CUSTOMER_COOKIE + " cookie must also be present");

        // Response-time SLO -- login shouldn't take more than 3s on a warm shop.
        softAssert.assertTrue(res.time() < 3000,
                "login should complete under 3000ms, actual=" + res.time() + "ms");
    }

    // ------------------------------------------ negative: wrong password

    @Test(groups = {"demoshop", "login"})
    @Story("Wrong password -> 200 with error text, no auth cookie")
    @Description("Wrong credentials should NOT redirect. The response stays 200, the body contains the standard error message, and no auth cookie is set.")
    public void login_wrongPassword_returns200AndErrorMessage() {
        String user = requireCred("demoshop.username", "DEMOSHOP_USERNAME");

        Response res = postLogin(user, "definitely-not-the-right-password");
        ExternalService.skipIfUnavailable(res);

        softAssert.assertEquals(res.statusCode(), 200,
                "failed login stays on the /login page with 200");
        softAssert.assertTrue(res.body().asString().contains(LOGIN_ERROR_MARKER),
                "response body should contain error marker: '" + LOGIN_ERROR_MARKER + "'");
        softAssert.assertNull(res.cookie(AUTH_COOKIE),
                AUTH_COOKIE + " cookie must NOT be present on failed login");
    }

    // ------------------------------- session reuse: cookie unlocks protected page

    @Test(groups = {"demoshop", "login"})
    @Story("Auth cookie unlocks /customer/info")
    @Description("Reuses the NOPCOMMERCE.AUTH cookie from a valid login on a GET to /customer/info -- a page that requires authentication -- and asserts the response reflects the logged-in state (contains the user's email).")
    public void authCookie_unlocksProtectedPage() {
        String user = requireCred("demoshop.username", "DEMOSHOP_USERNAME");
        String pass = requireCred("demoshop.password", "DEMOSHOP_PASSWORD");

        // 1. Login and capture the auth cookie.
        Response loginRes = postLogin(user, pass);
        ExternalService.skipIfUnavailable(loginRes);
        if (loginRes.statusCode() != 302) {
            throw new SkipException("Login didn't succeed (status=" + loginRes.statusCode()
                    + ") -- can't verify session reuse without a valid cookie.");
        }
        String authCookie = loginRes.cookie(AUTH_COOKIE);
        String customerCookie = loginRes.cookie(CUSTOMER_COOKIE);

        // 2. Hit a protected page with the cookie -- disable redirect-follow so
        //    we see the raw response, not whatever /login would redirect us to
        //    if the cookie were rejected.
        String baseUrl = Config.get("demoshop.baseUrl", "https://demowebshop.tricentis.com");
        Response protectedRes = RestAssured.given()
                .redirects().follow(false)
                .cookie(AUTH_COOKIE, authCookie)
                .cookie(CUSTOMER_COOKIE, customerCookie)
                .when()
                .get(baseUrl + "/customer/info");
        ExternalService.skipIfUnavailable(protectedRes);

        softAssert.assertEquals(protectedRes.statusCode(), 200,
                "authenticated request to /customer/info should return 200");
        softAssert.assertTrue(protectedRes.body().asString().contains(user),
                "customer info page should echo the logged-in user's email");
    }

    // ---- helpers ----

    private Response postLogin(String user, String pass) {
        String baseUrl   = Config.get("demoshop.baseUrl", "https://demowebshop.tricentis.com");
        String loginPath = Config.get("demoshop.loginPath", "/login");

        Map<String, String> headers = Headers.builder()
                .contentTypeFormUrlEncoded()
                .accept("text/html,application/xhtml+xml")
                .userAgent("api-automation-restassured/demoshop-test")
                .build();

        return RestAssured.given()
                .redirects().follow(false)   // we assert on the raw 302
                .headers(headers)
                .formParam("Email", user)
                .formParam("Password", pass)
                .formParam("RememberMe", "false")
                .when()
                .post(baseUrl + loginPath);
    }

    private static String requireCred(String prop, String envHint) {
        String v = Config.get(prop, null);
        if (v == null || v.isBlank()) {
            throw new SkipException(
                    "Credential '" + prop + "' not set -- skipping. "
                            + "Supply via -D" + prop + "=... or " + envHint + " env var.");
        }
        return v;
    }
}
