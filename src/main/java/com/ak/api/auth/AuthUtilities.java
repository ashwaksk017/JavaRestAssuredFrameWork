// =============================================================================
// AuthUtilities
// -----------------------------------------------------------------------------
// Auth header helpers. Wire into the RequestSpecBuilder in BaseApiTest so every
// test picks up the configured auth mode -- no per-test boilerplate.
//
// Supported modes (Config.authType()):
//   * none    -- no header injected
//   * bearer  -- Authorization: Bearer <auth.bearer.token>
//   * basic   -- Authorization: Basic base64(user:pass)
//   * oauth2  -- client credentials grant, token cached until 60s before expiry
// =============================================================================

package com.ak.api.auth;

import static io.restassured.RestAssured.given;

import java.time.Instant;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;

import com.ak.api.config.Config;

import io.restassured.response.Response;

public final class AuthUtilities {

    private static volatile String cachedOauth2Token;
    private static volatile Instant cachedOauth2Expiry;

    private AuthUtilities() {
    }

    /**
     * Compute the Authorization header value for the currently configured
     * auth mode. Returns null when auth is disabled or misconfigured.
     */
    public static String authHeaderValue() {
        String mode = Config.authType();
        return switch (mode.toLowerCase()) {
            case "bearer" -> bearer(Config.bearerToken());
            case "basic"  -> basic(Config.basicUsername(), Config.basicPassword());
            case "oauth2" -> "Bearer " + oauth2ClientCredentialsToken();
            case "none", "" -> null;
            default -> throw new IllegalArgumentException("Unknown auth.type: " + mode);
        };
    }

    public static Map<String, String> authHeaderMap() {
        Map<String, String> h = new HashMap<>();
        String v = authHeaderValue();
        if (v != null && !v.isBlank()) {
            h.put("Authorization", v);
        }
        return h;
    }

    // ---------------------------------------------------------------------
    // Individual builders (public so tests can pin a specific mode)
    // ---------------------------------------------------------------------

    public static String bearer(String token) {
        if (token == null || token.isBlank()) return null;
        return "Bearer " + token;
    }

    public static String basic(String username, String password) {
        if (username == null || username.isBlank()) return null;
        String raw = username + ":" + (password == null ? "" : password);
        return "Basic " + Base64.getEncoder().encodeToString(raw.getBytes());
    }

    /**
     * OAuth2 client credentials grant. Caches the token in-process until 60
     * seconds before its declared expiry. Thread-safe via double-checked
     * locking on volatile fields.
     */
    public static String oauth2ClientCredentialsToken() {
        Instant now = Instant.now();
        if (cachedOauth2Token != null && cachedOauth2Expiry != null
                && now.isBefore(cachedOauth2Expiry.minusSeconds(60))) {
            return cachedOauth2Token;
        }
        synchronized (AuthUtilities.class) {
            if (cachedOauth2Token != null && cachedOauth2Expiry != null
                    && now.isBefore(cachedOauth2Expiry.minusSeconds(60))) {
                return cachedOauth2Token;
            }
            String tokenUrl = Config.oauth2TokenUrl();
            if (tokenUrl == null || tokenUrl.isBlank()) {
                throw new IllegalStateException("auth.oauth2.tokenUrl is not configured");
            }
            Response res = given()
                    .relaxedHTTPSValidation()
                    .contentType("application/x-www-form-urlencoded")
                    .formParam("grant_type", "client_credentials")
                    .formParam("client_id", Config.oauth2ClientId())
                    .formParam("client_secret", Config.oauth2ClientSecret())
                    .formParam("scope", Config.oauth2Scope())
                    .when()
                    .post(tokenUrl);

            if (res.statusCode() < 200 || res.statusCode() >= 300) {
                throw new IllegalStateException(
                        "OAuth2 token request failed: HTTP " + res.statusCode()
                                + " body=" + res.body().asString());
            }

            String accessToken = res.jsonPath().getString("access_token");
            int expiresIn = res.jsonPath().getInt("expires_in");
            if (accessToken == null) {
                throw new IllegalStateException("OAuth2 token response missing access_token");
            }
            cachedOauth2Token = accessToken;
            cachedOauth2Expiry = Instant.now().plusSeconds(expiresIn);
            return cachedOauth2Token;
        }
    }

    /**
     * Force the cache to expire -- useful when a test needs a fresh token
     * or when a 401 is caught mid-suite.
     */
    public static void invalidateOauth2Cache() {
        cachedOauth2Token = null;
        cachedOauth2Expiry = null;
    }
}
