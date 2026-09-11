package com.ak.api.rest.utilities;

import java.util.Locale;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.ak.api.auth.AuthUtilities;
import com.ak.api.auth.TokenCache;
import com.ak.api.config.Config;
import com.ak.api.data.PlaceholderResolver;
import com.ak.api.support.ImportedRestClient;
import com.ak.api.support.ImportedScenario;
import com.ak.api.support.ImportedTemplates;

import io.restassured.response.Response;

/**
 * Re-fetch the Hilton OAuth token mid-test when a business call fails
 * with an expired / invalid bearer. {@link RestStep} calls
 * {@link #shouldAttempt} + {@link #refreshHiltonToken} once, then
 * retries the original {@code Exchange} so the lambda re-reads
 * {@code tokenId.GeneratedTokenID} from {@code ctx}.
 *
 * <p>Does <b>not</b> treat every 401 as expiry: TOTP lockouts and
 * negative auth cases must stay failures. Matchers are token-specific
 * (body or {@code WWW-Authenticate}), and refresh is skipped when the
 * step itself is {@code tokenRequest}, Salesforce, or an expected 401.</p>
 */
public final class TokenRefresh {

    private static final Logger LOG = LoggerFactory.getLogger(TokenRefresh.class);

    static final String CTX_TOKEN = "tokenId.GeneratedTokenID";
    static final String CTX_ACCESS = "accessToken";

    private static final ThreadLocal<Boolean> IN_PROGRESS =
            ThreadLocal.withInitial(() -> Boolean.FALSE);
    private static final ThreadLocal<Refresher> TEST_REFRESHER = new ThreadLocal<>();

    @FunctionalInterface
    public interface Refresher {
        boolean refresh(Map<String, String> ctx);
    }

    private TokenRefresh() {}

    /**
     * True when the response is an auth-material failure we can fix by
     * fetching a new Hilton token. Public so unit tests cover matchers
     * without firing HTTP.
     */
    public static boolean isAuthTokenFailure(Response res) {
        if (res == null) {
            return false;
        }
        int status = res.getStatusCode();
        if (status != 401 && status != 403) {
            return false;
        }
        String blob = authFailureText(res);
        if (blob.isEmpty()) {
            return false;
        }
        if (blob.contains("member status is invalid")
                || blob.contains("totp code is invalid")
                || blob.contains("otp is invalid")) {
            return false;
        }
        return blob.contains("token expired")
                || blob.contains("token has expired")
                || blob.contains("expired token")
                || blob.contains("access token expired")
                || blob.contains("access_token expired")
                || blob.contains("invalid_token")
                || blob.contains("invalid token")
                || blob.contains("token is invalid")
                || blob.contains("token is expired")
                || blob.contains("jwt expired")
                || blob.contains("jwt is expired")
                || blob.contains("not a valid token")
                || blob.contains("malformed token")
                || blob.contains("token revoked")
                || blob.contains("revoked token");
    }

    /**
     * Whether RestStep should try a Hilton token refresh for this
     * exchange. False for token fetch itself, Salesforce steps,
     * negative tests that expect this status, and when disabled.
     */
    public static boolean shouldAttempt(String stepName, int expectedStatus,
                                        Response res) {
        if (!Config.getBool("auth.tokenRefresh.enabled", true)) {
            return false;
        }
        if (Boolean.TRUE.equals(IN_PROGRESS.get())) {
            return false;
        }
        if (res == null) {
            return false;
        }
        if (expectedStatus == res.getStatusCode()) {
            return false;
        }
        if (isTokenFetchOrSalesforceStep(stepName)) {
            return false;
        }
        return isAuthTokenFailure(res);
    }

    /**
     * Fetch a new Hilton token into {@code ctx}. Returns true only when
     * {@code tokenId.GeneratedTokenID} was overwritten with a non-empty
     * Bearer value. Test code may install a hook via
     * {@link #overrideRefresherForTest}.
     */
    public static boolean refreshHiltonToken(Map<String, String> ctx) {
        if (ctx == null) {
            return false;
        }
        if (!Config.getBool("auth.tokenRefresh.enabled", true)) {
            return false;
        }
        Refresher hook = TEST_REFRESHER.get();
        if (hook != null) {
            return hook.refresh(ctx);
        }
        if (Boolean.TRUE.equals(IN_PROGRESS.get())) {
            return false;
        }
        if (hasBoundImportedClient()) {
            return refreshFromBoundClient(ctx);
        }
        return refreshViaAuthHelper(ctx);
    }

    public static void overrideRefresherForTest(Refresher refresher) {
        TEST_REFRESHER.set(refresher);
    }

    public static void clearRefresherOverrideForTest() {
        TEST_REFRESHER.remove();
    }

    static boolean isTokenFetchOrSalesforceStep(String stepName) {
        if (stepName == null || stepName.isEmpty()) {
            return false;
        }
        String n = stepName.toLowerCase(Locale.ROOT);
        if (n.contains("invalid_token") || n.contains("invalidtoken")) {
            return true;
        }
        if (n.contains("tokenrequest") || n.contains("token_request")) {
            return true;
        }
        if (n.contains("sf_token") || n.contains("sftoken")) {
            return true;
        }
        return n.contains("salesforce");
    }

    private static String authFailureText(Response res) {
        StringBuilder sb = new StringBuilder();
        try {
            String www = res.getHeader("WWW-Authenticate");
            if (www != null) {
                sb.append(www).append('\n');
            }
        } catch (RuntimeException ignored) {
            // RestAssured mock responses may have no header map.
        }
        try {
            String body = res.getBody() == null ? "" : res.getBody().asString();
            if (body != null) {
                sb.append(body);
            }
        } catch (RuntimeException ignored) {
            // empty
        }
        return sb.toString().toLowerCase(Locale.ROOT);
    }

    private static boolean hasBoundImportedClient() {
        try {
            return ImportedScenario.current().client instanceof ImportedRestClient;
        } catch (IllegalStateException e) {
            return false;
        }
    }

    private static boolean refreshFromBoundClient(Map<String, String> ctx) {
        ImportedScenario.Session s;
        try {
            s = ImportedScenario.current();
        } catch (IllegalStateException e) {
            return false;
        }
        if (!(s.client instanceof ImportedRestClient)) {
            return false;
        }
        ImportedRestClient client = (ImportedRestClient) s.client;
        String template = resolveTokenTemplate();
        if (template == null || template.isEmpty()) {
            LOG.warn(" .. [token-refresh] no REALMS_TOKENREQUEST template for bound suite");
            return false;
        }
        IN_PROGRESS.set(Boolean.TRUE);
        try {
            String mapped = RestUtilities.mapJsonValues(
                    RestUtilities.getRequestTemplate(template),
                    ImportedScenario.mergedRow(s.row, ctx), false);
            String body = PlaceholderResolver.resolveAll(mapped, ctx);
            Response tokenRes = client.tokenRequest("", body);
            int status = tokenRes == null ? -1 : tokenRes.getStatusCode();
            if (status != 200) {
                LOG.warn(" .. [token-refresh] tokenRequest returned HTTP {}", status);
                return false;
            }
            String access = RestUtilities.safeJsonExtract(tokenRes, "access_token");
            if (access == null || access.isEmpty()) {
                LOG.warn(" .. [token-refresh] tokenRequest 200 but no access_token");
                return false;
            }
            applyToken(ctx, access);
            return true;
        } catch (UnsupportedOperationException e) {
            LOG.warn(" .. [token-refresh] client has no tokenRequest: {}", e.getMessage());
            return false;
        } catch (Exception e) {
            LOG.warn(" .. [token-refresh] failed: {}", e.toString());
            return false;
        } finally {
            IN_PROGRESS.set(Boolean.FALSE);
        }
    }

    private static boolean refreshViaAuthHelper(Map<String, String> ctx) {
        ctx.remove(CTX_ACCESS);
        AuthHelper.primeClientCredentialsToken(ctx);
        String access = ctx.get(CTX_ACCESS);
        if (access == null || access.isEmpty()) {
            return false;
        }
        if (access.startsWith("Bearer ")) {
            access = access.substring("Bearer ".length());
        }
        applyToken(ctx, access);
        return true;
    }

    private static void applyToken(Map<String, String> ctx, String accessToken) {
        String bearer = accessToken.startsWith("Bearer ")
                ? accessToken
                : "Bearer " + accessToken;
        ImportedScenario.putExtracted(ctx, CTX_TOKEN, bearer);
        ctx.put(CTX_ACCESS, accessToken.startsWith("Bearer ")
                ? accessToken.substring("Bearer ".length())
                : accessToken);
        TokenCache.putAccessToken(ctx.get(CTX_ACCESS));
        AuthUtilities.invalidateOauth2Cache();
        LOG.info(" .. [token-refresh] wrote {} (len={})", CTX_TOKEN, bearer.length());
    }

    private static String resolveTokenTemplate() {
        String preferred = tryTemplate("REALMS_TOKENREQUEST_2");
        if (preferred != null) {
            return preferred;
        }
        return tryTemplate("REALMS_TOKENREQUEST");
    }

    private static String tryTemplate(String name) {
        try {
            return ImportedTemplates.get(name);
        } catch (RuntimeException e) {
            return null;
        }
    }
}
