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

package com.hi.api.auth;

import static io.restassured.RestAssured.given;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;

import com.hi.api.config.Config;

import io.restassured.response.Response;

public final class AuthUtilities {

    private static final org.slf4j.Logger LOG =
            org.slf4j.LoggerFactory.getLogger(AuthUtilities.class);

    /**
     * Last 4 characters only -- enough to tell two client ids apart.
     *
     * <p>Package-private rather than private so it can be tested: this
     * decides what a credential looks like in a log file, which is not
     * something to find out about from a log file.</p>
     */
    static String maskTail(String s) {
        if (s == null || s.isBlank()) {
            return "(unset)";
        }
        String t = s.trim();
        return t.length() <= 4 ? "****" : "****" + t.substring(t.length() - 4);
    }

    static String blankToNone(String s) {
        return (s == null || s.isBlank()) ? "(none)" : s;
    }


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

    /**
     * Authorization value for a token that may ALREADY carry the scheme.
     *
     * <p>{@link #bearer(String)} prefixes unconditionally, which is right
     * for a raw credential. The converter stores the scheme with the
     * token -- {@code putExtracted(ctx, "tokenId.GeneratedTokenID",
     * "Bearer " + id)} -- so prefixing again sends
     * {@code Bearer Bearer ...} and the server answers 401. Every
     * generated {@code *Client} has carried this ternary inline for that
     * reason; naming it keeps the two rules apart where they are easy to
     * confuse.</p>
     *
     * <p>Empty stays empty: a step declaring "No Authorization" must send
     * no credential, and {@code "Bearer "} with nothing after it is still
     * a credential as far as the server is concerned -- it answers 401
     * for the wrong reason and the negative test passes without testing
     * anything.</p>
     */
    public static String bearerOnce(String token) {
        if (token == null || token.isEmpty()) {
            return "";
        }
        return token.startsWith("Bearer ") ? token : "Bearer " + token;
    }

    public static String basic(String username, String password) {
        if (username == null || username.isBlank()) return null;
        String raw = username + ":" + (password == null ? "" : password);
        // Explicit UTF-8: matches RFC 7617's recommended encoding and
        // avoids the platform-default landmine where the same non-ASCII
        // password base64-encodes differently on Windows (CP1252) vs
        // Linux CI (UTF-8). Symptom without this: auth works on the
        // author's laptop, 401s on the CI runner with no other change.
        return "Basic " + Base64.getEncoder().encodeToString(raw.getBytes(StandardCharsets.UTF_8));
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
            // Reported because this call reaches an identity provider and
            // used to do it in complete silence: a run authenticated,
            // cached, and never said where from, so a token minted
            // against the wrong environment looked like no token call at
            // all.

            if (cachedOauth2Token != null && cachedOauth2Expiry != null
                    && now.isBefore(cachedOauth2Expiry.minusSeconds(60))) {
                return cachedOauth2Token;
            }
            String tokenUrl = Config.oauth2TokenUrl();
            if (tokenUrl == null || tokenUrl.isBlank()) {
                throw new IllegalStateException("auth.oauth2.tokenUrl is not configured");
            }
            LOG.info("OAuth2: requesting a client_credentials token from {} "
                    + "(client_id {}, scope {})", tokenUrl,
                    maskTail(Config.oauth2ClientId()),
                    blankToNone(Config.oauth2Scope()));
            long t0 = System.currentTimeMillis();
            Response res = given()
                    .relaxedHTTPSValidation()
                    .contentType("application/x-www-form-urlencoded")
                    .formParam("grant_type", "client_credentials")
                    .formParam("client_id", Config.oauth2ClientId())
                    .formParam("client_secret", Config.oauth2ClientSecret())
                    .formParam("scope", Config.oauth2Scope())
                    .when()
                    .post(tokenUrl);

            long ms = System.currentTimeMillis() - t0;
            if (res.statusCode() < 200 || res.statusCode() >= 300) {
                LOG.warn("OAuth2: token request to {} returned HTTP {} after "
                        + "{} ms", tokenUrl, res.statusCode(), ms);
                throw new IllegalStateException(
                        "OAuth2 token request failed: HTTP " + res.statusCode()
                                + " body=" + res.body().asString());
            }

            String accessToken = res.jsonPath().getString("access_token");
            if (accessToken == null) {
                throw new IllegalStateException("OAuth2 token response missing access_token");
            }
            // expires_in is OPTIONAL in some OAuth2 provider responses
            // (parts of Azure AD send `expires_at` epoch instead;
            // certain Okta configs omit it entirely). Older code called
            // getInt() which throws JsonPathException on missing --
            // that failed the ENTIRE suite with a confusing error.
            // Default to 3600s (1 hour) when absent -- worst case the
            // cache expires ahead of the actual token, we re-fetch,
            // and the run continues.
            int expiresIn;
            try {
                expiresIn = res.jsonPath().getInt("expires_in");
            } catch (Exception e) {
                expiresIn = 3600;
            }
            cachedOauth2Token = accessToken;
            cachedOauth2Expiry = Instant.now().plusSeconds(expiresIn);
            LOG.info("OAuth2: got a token in {} ms, cached for {}s (until {})",
                    ms, expiresIn, cachedOauth2Expiry);
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
