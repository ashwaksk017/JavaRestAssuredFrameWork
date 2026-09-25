package com.hi.api.auth;

import java.util.concurrent.atomic.AtomicReference;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.hi.api.config.Config;
import com.hi.api.rest.ApiRoutes;
import com.hi.api.rest.utilities.RestUtilities;

import io.restassured.builder.ResponseBuilder;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

/**
 * Suite-scoped client-credentials token. Safe to reuse across tests
 * (not user identity). Disable with {@code -Dauth.tokenCache.enabled=false}.
 */
public final class TokenCache {

    private static final Logger LOG = LoggerFactory.getLogger(TokenCache.class);

    private static final AtomicReference<Entry> HELD = new AtomicReference<>();

    private TokenCache() {}

    public static boolean enabled() {
        return Config.getBool("auth.tokenCache.enabled", true);
    }

    /**
     * Reuse a cached Hilton token for setup {@code tokenRequest} when
     * the step expects success (not a negative-auth case).
     */
    public static boolean canReuseForSetup(String stepName, int expectedStatus,
                                           String resolvedPath) {
        if (!enabled() || !Config.getBool("auth.tokenCache.skipRepeatFetch", true)) {
            return false;
        }
        if (expectedStatus > 0 && expectedStatus != 200) {
            return false;
        }
        String n = stepName == null ? "" : stepName.toLowerCase();
        if (n.contains("salesforce") || n.contains("sf_token") || n.contains("sftoken")) {
            return false;
        }
        if (!ApiRoutes.isTokenPath(resolvedPath) && !n.contains("tokenrequest")) {
            return false;
        }
        return getAccessToken() != null;
    }

    public static String getAccessToken() {
        Entry e = HELD.get();
        if (e == null || e.accessToken == null || e.accessToken.isEmpty()) {
            return null;
        }
        if (System.currentTimeMillis() >= e.expiresAtMs) {
            HELD.compareAndSet(e, null);
            LOG.info("TokenCache: expired -- next tokenRequest will hit the wire");
            return null;
        }
        return e.accessToken;
    }

    public static Response cachedAsResponse() {
        String access = getAccessToken();
        if (access == null) {
            return null;
        }
        LOG.info("TokenCache: reusing client-credentials token (len={})", access.length());
        String body = "{\"access_token\":\"" + jsonEscape(access)
                + "\",\"token_type\":\"Bearer\"}";
        return new ResponseBuilder()
                .setStatusCode(200)
                .setContentType(ContentType.JSON)
                .setBody(body)
                .build();
    }

    public static void storeFrom(Response res) {
        if (!enabled() || res == null || res.getStatusCode() != 200) {
            return;
        }
        String access = RestUtilities.safeJsonExtract(res, "access_token");
        if (access == null || access.isEmpty()) {
            return;
        }
        long ttlMs = Config.getInt("auth.tokenCache.ttlMs", 50 * 60 * 1000);
        String expiresIn = RestUtilities.safeJsonExtract(res, "expires_in");
        if (expiresIn != null && !expiresIn.isEmpty()) {
            try {
                long sec = Long.parseLong(expiresIn.trim());
                if (sec > 30) {
                    ttlMs = (sec - 30) * 1000L;
                }
            } catch (NumberFormatException ignored) {
                // keep default ttl
            }
        }
        HELD.set(new Entry(access, System.currentTimeMillis() + ttlMs));
        LOG.info("TokenCache: stored access_token ttlMs={}", ttlMs);
    }

    public static void putAccessToken(String accessToken) {
        if (!enabled() || accessToken == null || accessToken.isEmpty()) {
            return;
        }
        if (isRejected(accessToken)) {
            LOG.info("TokenCache: not caching a token the server already rejected (len={})",
                    accessToken.length());
            return;
        }
        String raw = accessToken.startsWith("Bearer ")
                ? accessToken.substring("Bearer ".length())
                : accessToken;
        long ttlMs = Config.getInt("auth.tokenCache.ttlMs", 50 * 60 * 1000);
        HELD.set(new Entry(raw, System.currentTimeMillis() + ttlMs));
    }

    /**
     * Copy the cached client-credentials token onto a per-test ctx so a
     * {@code tokenRequest} cache hit still populates
     * {@code tokenId.GeneratedTokenID}. Without this, Groovy extract can
     * miss the synthetic body and later enrolls go out with an empty
     * Authorization header (HTTP 401).
     */
    public static void applyToCtx(java.util.Map<String, String> ctx) {
        if (ctx == null) {
            return;
        }
        String access = getAccessToken();
        if (access == null || access.isEmpty()) {
            return;
        }
        String raw = access.startsWith("Bearer ")
                ? access.substring("Bearer ".length())
                : access;
        // Fill an empty key, or replace a token the server already rejected.
        // Filling only empty keys left a dead token in ctx after the cache
        // was refreshed, so every later step in the test sent it again.
        String current = ctx.get("accessToken");
        if (current == null || current.isEmpty() || isRejected(current)) {
            ctx.put("accessToken", raw);
        }
        String existing = ctx.get("tokenId.GeneratedTokenID");
        if (existing == null || existing.isEmpty() || isRejected(existing)) {
            if (existing != null && !existing.isEmpty()) {
                LOG.info("TokenCache: replacing rejected ctx token (len={}) with cached token (len={})",
                        existing.length(), raw.length());
            }
            ctx.put("tokenId.GeneratedTokenID", "Bearer " + raw);
        }
    }

    public static void clear() {
        HELD.set(null);
    }

    /**
     * Fingerprints of tokens the server rejected this run -- a hash, never
     * the value. Without this, clearing the cache did not stick: AuthHelper
     * put the ctx copy of the dead token straight back into the cache.
     */
    private static final java.util.Set<Integer> REJECTED =
            java.util.concurrent.ConcurrentHashMap.newKeySet();

    private static int fingerprint(String token) {
        String t = token.trim();
        if (t.startsWith("Bearer ")) {
            t = t.substring("Bearer ".length()).trim();
        }
        return t.hashCode();
    }

    public static void markRejected(String token) {
        if (token != null && !token.trim().isEmpty() && !"Bearer".equals(token.trim())) {
            REJECTED.add(fingerprint(token));
        }
    }

    public static boolean isRejected(String token) {
        return token != null && !token.trim().isEmpty() && REJECTED.contains(fingerprint(token));
    }

    /** Unit tests only: forget the held token and the rejection history. */
    public static void resetForTest() {
        HELD.set(null);
        REJECTED.clear();
    }

    private static String jsonEscape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static final class Entry {
        final String accessToken;
        final long expiresAtMs;

        Entry(String accessToken, long expiresAtMs) {
            this.accessToken = accessToken;
            this.expiresAtMs = expiresAtMs;
        }
    }
}
