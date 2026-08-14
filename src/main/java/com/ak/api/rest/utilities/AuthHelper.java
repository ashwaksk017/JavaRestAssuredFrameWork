package com.ak.api.rest.utilities;

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import io.restassured.RestAssured;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

import com.ak.api.config.Config;

/**
 * One-time OAuth 2.0 bootstrap. Business-area test classes call
 * {@link #primeClientCredentialsToken(Map)} from {@code @BeforeClass}
 * so every {@code @Test} method starts with {@code ctx.get("accessToken")}
 * already populated -- no re-fetch per method.
 *
 * <p>Config keys read (from {@code program_configuration.json} via
 * {@link Config}):</p>
 * <ul>
 *   <li>{@code api_config.client_id}</li>
 *   <li>{@code api_config.client_secret}</li>
 *   <li>{@code api_config.grant_type} (default: {@code client_credentials})</li>
 *   <li>{@code api_config.token_end_point} (base URL)</li>
 *   <li>{@code api_config.token_route} (path appended to base)</li>
 * </ul>
 *
 * <p>Missing / empty creds -> logs WARN, leaves ctx untouched. Individual
 * REST steps that fetch a token inline still run in that case (fallback),
 * so tests never break on absent config -- they just don't benefit from
 * the class-level token reuse.</p>
 *
 * <p>Idempotent: safe to call multiple times. If {@code ctx} already
 * carries a non-empty {@code accessToken}, returns immediately.</p>
 */
public final class AuthHelper {

    private static final Logger LOG = LoggerFactory.getLogger(AuthHelper.class);

    private AuthHelper() {}

    /**
     * Fetch a client-credentials OAuth token and store it in {@code ctx}
     * under the key {@code "accessToken"}. Safe to call from any
     * {@code @BeforeClass} -- becomes a no-op when the key is already set.
     */
    public static void primeClientCredentialsToken(Map<String, String> ctx) {
        if (ctx.containsKey("accessToken")
                && ctx.get("accessToken") != null
                && !ctx.get("accessToken").isEmpty()) {
            LOG.debug("AuthHelper: ctx already has accessToken -- skipping bootstrap");
            return;
        }
        String clientId     = Config.get("api_config.client_id", "");
        String clientSecret = Config.get("api_config.client_secret", "");
        String grantType    = Config.get("api_config.grant_type", "client_credentials");
        if (clientId.isEmpty() || clientSecret.isEmpty()) {
            LOG.warn("AuthHelper: api_config.client_id / client_secret not "
                    + "configured; leaving ctx.accessToken empty. Tests that "
                    + "need auth will fall back to fetching a token inline.");
            return;
        }
        String tokenBase = Config.get("api_config.token_end_point", "");
        String tokenRoute = Config.get("api_config.token_route", "");
        // Landmine: if BOTH keys are empty, refuse to POST creds against
        // baseUrl -- doing so silently uploaded the client_id/secret to
        // the API root, which returns 404/415 and leaves the accessToken
        // empty. That failure mode is easy to blame on the target API
        // when the real cause is missing config.
        if (tokenBase.isEmpty() && tokenRoute.isEmpty()) {
            LOG.warn("AuthHelper: neither `api_config.token_end_point` nor "
                    + "`api_config.token_route` is set in program_configuration.json "
                    + "-- refusing to POST client_id/client_secret to baseUrl "
                    + "(would exfiltrate creds to the wrong endpoint). Set at "
                    + "least one and re-run.");
            return;
        }
        if (tokenBase.isEmpty()) tokenBase = Config.baseUrl();
        String tokenUrl;
        if (tokenRoute.isEmpty()) {
            tokenUrl = tokenBase;
        } else if (tokenBase.endsWith("/") || tokenRoute.startsWith("/")) {
            tokenUrl = tokenBase + tokenRoute;
        } else {
            tokenUrl = tokenBase + "/" + tokenRoute;
        }

        try {
            Response resp = RestAssured.given()
                    .contentType(ContentType.URLENC)
                    .formParam("grant_type",    grantType)
                    .formParam("client_id",     clientId)
                    .formParam("client_secret", clientSecret)
                    .post(tokenUrl);

            int status = resp.getStatusCode();
            if (status != 200) {
                String snippet = resp.asString();
                if (snippet.length() > 200) snippet = snippet.substring(0, 200) + "...";
                LOG.warn("AuthHelper: token endpoint {} returned HTTP {} -- "
                        + "leaving ctx.accessToken empty (inline fallback will run). "
                        + "Body: {}", tokenUrl, status, snippet);
                return;
            }
            String token = com.ak.api.rest.utilities.RestUtilities.safeJsonExtract(resp, "access_token");
            if (token == null || token.isEmpty()) {
                LOG.warn("AuthHelper: token endpoint {} returned 200 but no "
                        + "access_token field in body -- leaving ctx empty", tokenUrl);
                return;
            }
            ctx.put("accessToken", token);
            LOG.info("AuthHelper: primed ctx.accessToken from {}", tokenUrl);
        } catch (Exception e) {
            LOG.warn("AuthHelper: token bootstrap failed against {}: {} "
                    + "(inline fallback will run)", tokenUrl, e.getMessage());
        }
    }
}
