package com.hi.api.auth;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hi.api.config.Config;
import com.hi.api.rest.ApiRoutes;
import com.hi.api.rest.utilities.Headers;
import com.hi.api.rest.utilities.RestUtilities;

import io.restassured.response.Response;

/**
 * Tokens from the realms token end point, as JSON: the application's own
 * (client credentials) and a person's (resource owner: username and
 * password), with the guest id the person's token belongs to.
 *
 * <p>Why this exists: every story test the workbench generated wrote these
 * itself -- five copies of the resource-owner request in two flavours, three
 * copies of reading the guest id out of the token. {@link AuthUtilities}
 * and {@code AuthHelper} post form-encoded client credentials only, which
 * the routes that check {@code guestIdInClaimMustMatchGuestIdInPath} do not
 * take. One place now, used by {@code StoryTestBase}.</p>
 *
 * <p>The token URL: {@code api_config.token_end_point} +
 * {@code api_config.token_route} when both are set; else
 * {@code baseUrl() + /realms/applications/token} ({@link ApiRoutes#TOKEN}).
 * Seen in the generated tests: some environments answer on one, some on the
 * other (one test notes the configured one returns 404 on QA). So when the
 * first answers 404 or 405 -- no such route -- the other is asked, and the
 * log says which answered. Any other answer is the answer.</p>
 * <p>Client id and secret: {@code api_config.client_id} /
 * {@code api_config.client_secret}. Nothing here is a literal credential.</p>
 */
public final class JsonTokens {

    private JsonTokens() {
    }

    /** A token, and the guest id it belongs to (null for an application's token). */
    public static final class Session {
        public final String accessToken;
        public final String guestId;

        public Session(String accessToken, String guestId) {
            this.accessToken = accessToken;
            this.guestId = guestId;
        }

        /** The guest id, or an error saying the token did not carry one. */
        public String requireGuestId() {
            if (guestId == null || guestId.isBlank()) {
                throw new IllegalStateException("the token response carried no guest id (looked in the "
                        + "response body and in the access token's claims)");
            }
            return guestId;
        }
    }

    /** Where a guest id is found in a token response body, in this order. */
    static final String[] GUEST_ID_JSON_KEYS = {"guestId", "guestID", "hiltonGuestId", "hilton-guest-id"};

    private static final Pattern GUEST_ID_CLAIM = Pattern.compile(
            "\"(?:guestId|guestID|hiltonGuestId|hilton-guest-id)\"\\s*:\\s*\"?(\\d+)\"?");

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Logger LOG = LoggerFactory.getLogger(JsonTokens.class);

    /** The token URL asked first (see the class comment). */
    public static String tokenUrl() {
        return tokenUrls().get(0);
    }

    /** The token URLs, in the order they are asked: the configured one, then the realms route. */
    public static List<String> tokenUrls() {
        List<String> urls = new ArrayList<>();
        String base = Config.get("api_config.token_end_point", "");
        String route = Config.get("api_config.token_route", "");
        if (!Config.isUnset(base) && !Config.isUnset(route)) {
            urls.add(base.endsWith("/") || route.startsWith("/") ? base + route : base + "/" + route);
        }
        String realms = Config.baseUrl() + ApiRoutes.fill(ApiRoutes.TOKEN);
        if (!urls.contains(realms)) {
            urls.add(realms);
        }
        return urls;
    }

    /** Why a token cannot be asked for here, or "" when it can. */
    public static String missingConfig() {
        if (Config.isUnset(Config.get("api_config.client_id", ""))
                || Config.isUnset(Config.get("api_config.client_secret", ""))) {
            return "Token client credentials are not configured for env '" + Config.env()
                    + "'. Set api_config.client_id and api_config.client_secret in "
                    + "program_configuration.json.";
        }
        return "";
    }

    /** The application's own token (client credentials, JSON). */
    public static String clientCredentials() {
        return request(base()).accessToken;
    }

    /** A person's token (resource owner, JSON), with the guest id it belongs to when it carries one. */
    public static Session resourceOwner(String username, String password) {
        Map<String, String> payload = base();
        payload.put("username", username);
        payload.put("password", password);
        return request(payload);
    }

    private static Map<String, String> base() {
        String why = missingConfig();
        if (!why.isEmpty()) {
            throw new IllegalStateException(why);
        }
        Map<String, String> payload = new LinkedHashMap<>();
        payload.put("client_id", Config.get("api_config.client_id", ""));
        payload.put("client_secret", Config.get("api_config.client_secret", ""));
        return payload;
    }

    private static Session request(Map<String, String> payload) {
        String body;
        try {
            body = JSON.writeValueAsString(payload);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("cannot serialise the token body", e);
        }
        Map<String, String> headers = Headers.builder().contentTypeJson().acceptJson().build();
        List<String> urls = tokenUrls();
        Response res = null;
        String status = "";
        for (int i = 0; i < urls.size(); i++) {
            res = RestUtilities.getResponsePost(body, urls.get(i), headers);
            status = RestUtilities.checkActualResponse(res);
            boolean noSuchRoute = "404".equals(status) || "405".equals(status);
            if (noSuchRoute && i + 1 < urls.size()) {
                LOG.warn("token end point {} answered HTTP {} (no such route here); asking {}",
                        urls.get(i), status, urls.get(i + 1));
                continue;
            }
            if (i > 0) {
                LOG.info("token from {} (the first token URL had no such route)", urls.get(i));
            }
            break;
        }
        if (!"200".equals(status)) {
            throw new IllegalStateException("token end point returned HTTP " + status
                    + " (asked: " + String.join(", ", urls) + ")");
        }
        String token = RestUtilities.safeJsonExtract(res, "access_token");
        if (token == null || token.isBlank()) {
            throw new IllegalStateException("token end point returned 200 but no access_token field");
        }
        return new Session(token, guestIdFrom(res, token));
    }

    /** The guest id in a token response: its body first, else the access token's claims; null if neither. */
    public static String guestIdFrom(Response tokenResponse, String accessToken) {
        if (tokenResponse != null) {
            for (String key : GUEST_ID_JSON_KEYS) {
                String fromBody = RestUtilities.safeJsonExtract(tokenResponse, key);
                if (fromBody != null && !fromBody.isBlank()) {
                    return fromBody.trim();
                }
            }
        }
        return guestIdFromClaims(accessToken);
    }

    /** The guest id claim of a JWT access token (its payload is read, never verified); null if none. */
    public static String guestIdFromClaims(String accessToken) {
        if (accessToken == null || accessToken.isBlank() || accessToken.indexOf('.') < 0) {
            return null;
        }
        String[] parts = accessToken.split("\\.", 3);
        if (parts.length < 2 || parts[1].isEmpty()) {
            return null;
        }
        try {
            String payload = parts[1];
            int pad = (4 - (payload.length() % 4)) % 4;
            if (pad > 0) {
                payload = payload + "====".substring(0, pad);
            }
            String json = new String(Base64.getUrlDecoder().decode(payload), StandardCharsets.UTF_8);
            Matcher m = GUEST_ID_CLAIM.matcher(json);
            return m.find() ? m.group(1) : null;
        } catch (RuntimeException notAJwt) {
            return null;
        }
    }
}
