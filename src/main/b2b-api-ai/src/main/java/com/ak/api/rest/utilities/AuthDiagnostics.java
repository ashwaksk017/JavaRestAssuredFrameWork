package com.ak.api.rest.utilities;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Why a 401/403 happened, counted across the run.
 *
 * <p>A 401 wave looks identical in a log whether the token was never
 * populated -- an upstream extract produced nothing -- or a real token was
 * refused (expired, wrong audience, throttled). Those need opposite fixes:
 * the first is a converter/dataflow bug, the second is auth config. Nothing
 * in a raw log separates them without dumping headers, which is enormous and
 * unsafe.</p>
 *
 * <p>Lives in the framework, not in a reporting listener or a test-side
 * filter, so it is reachable from {@code RestStep} (which is on every request
 * path by construction) and survives whether or not the converter has emitted
 * the digest listener. The first attempt recorded only from
 * {@code RestAssuredRecordingFilter}; that filter is registered globally but
 * recorded nothing on a real run, so the counter now has a producer that
 * cannot be bypassed.</p>
 *
 * <p>Values are never stored -- only a verdict and a length.</p>
 */
public final class AuthDiagnostics {

    private static final Map<String, Integer> VERDICTS = new ConcurrentHashMap<>();

    private AuthDiagnostics() {
    }

    public static void record(String verdict) {
        if (verdict == null || verdict.isEmpty()) {
            return;
        }
        VERDICTS.merge(verdict, 1, Integer::sum);
    }

    /** Snapshot, highest count first. */
    public static Map<String, Integer> snapshot() {
        Map<String, Integer> out = new LinkedHashMap<>();
        VERDICTS.entrySet().stream()
                .sorted((a, b) -> b.getValue() - a.getValue())
                .forEach(e -> out.put(e.getKey(), e.getValue()));
        return out;
    }

    public static boolean isEmpty() {
        return VERDICTS.isEmpty();
    }

    /**
     * Status the in-flight {@code RestStep} asserts, for the recording
     * filter, which sees the exchange but not the step. Without it a
     * negative auth test that asked for 403 and got it was counted as a
     * rejected token.
     */
    private static final ThreadLocal<Integer> EXPECTED_STATUS = new ThreadLocal<>();

    public static void expectStatus(int status) {
        if (status > 0) {
            EXPECTED_STATUS.set(status);
        } else {
            EXPECTED_STATUS.remove();
        }
    }

    public static void clearExpectedStatus() {
        EXPECTED_STATUS.remove();
    }

    public static boolean isExpectedStatus(int status) {
        Integer expected = EXPECTED_STATUS.get();
        return expected != null && expected == status;
    }

    /**
     * Whether this response means the token itself is dead, so the cache
     * should be dropped.
     *
     * <p>A 401 does. A 403 usually does NOT -- it is "authenticated, not
     * allowed" (a suspended owner, a travel advisor on an admin call), and
     * the token is fine. Clearing on every 403 re-fetched tokens for
     * permission denials. A 403 counts only when its body or
     * WWW-Authenticate names a token problem. A step that asked for 401/403
     * is a negative test passing, never a dead token.</p>
     */
    public static boolean isDeadTokenSignal(io.restassured.response.Response res,
                                            int expectedStatus) {
        if (res == null) {
            return false;
        }
        int code = res.getStatusCode();
        if (code == expectedStatus || expectedStatus == 401 || expectedStatus == 403) {
            return false;
        }
        if (code == 401) {
            return true;
        }
        return code == 403 && TokenRefresh.isAuthTokenFailure(res);
    }

    private static final java.util.concurrent.atomic.AtomicLong LAST_CLEAR =
            new java.util.concurrent.atomic.AtomicLong(0L);

    /**
     * Drop every cached token after a rejection, so the next auth-requiring
     * step fetches a fresh one.
     *
     * <p>Two bugs made this never happen. The hook lived in
     * RestAssuredRecordingFilter behind
     * {@code "oauth2".equals(Config.authType())}, but {@code auth.type} is
     * unset and defaults to {@code "none"} -- so it never fired. And it
     * cleared {@code AuthUtilities}' cache, while this suite's token lives in
     * {@code TokenCache.HELD}. A single revoked or rotated token therefore
     * stuck for its full declared TTL and every later test 401'd: 7,554
     * rejections in one run, all of them a real token the server refused.</p>
     *
     * <p>Debounced: with thousands of in-flight rejections an undebounced
     * clear would stampede the token endpoint, which is itself a way to get
     * throttled. One clear per window is enough -- the point is to drop a
     * dead token once, not once per victim.</p>
     *
     * @return true when this call actually cleared
     */
    public static boolean invalidateCachedTokens(long debounceMs) {
        long now = System.currentTimeMillis();
        long prev = LAST_CLEAR.get();
        if (now - prev < debounceMs || !LAST_CLEAR.compareAndSet(prev, now)) {
            return false;
        }
        com.ak.api.auth.TokenCache.clear();
        com.ak.api.auth.AuthUtilities.invalidateOauth2Cache();
        record("TOKEN-CACHE-CLEARED after rejection (next step re-fetches)");
        return true;
    }

    /**
     * Verdict derived from the ctx token inventory at the moment of a
     * rejection. `RestStep` cannot see the outgoing Authorization header --
     * the token is passed into the generated client method -- but it CAN see
     * whether the ctx keys that feed those methods hold anything, which is
     * the same question one level up.
     */
    public static String verdictFromCtx(Map<String, String> ctx) {
        if (ctx == null || ctx.isEmpty()) {
            return "NO-CTX (no context at all -- bootstrap problem)";
        }
        int tokenKeys = 0;
        int nonEmpty = 0;
        int longest = 0;
        for (Map.Entry<String, String> e : ctx.entrySet()) {
            String k = e.getKey();
            if (k == null || !k.toLowerCase().contains("token")) {
                continue;
            }
            tokenKeys++;
            String v = e.getValue();
            if (v != null && !v.isEmpty()) {
                nonEmpty++;
                longest = Math.max(longest, v.length());
            }
        }
        if (tokenKeys == 0) {
            return "NO-TOKEN-KEY-IN-CTX (nothing published a token -- dataflow bug)";
        }
        if (nonEmpty == 0) {
            return "TOKEN-KEY-PRESENT-BUT-EMPTY (extract produced nothing -- "
                    + "dataflow bug)";
        }
        return "TOKEN-PRESENT-IN-CTX-BUT-REJECTED (len=" + longest
                + " -- expired / audience / throttled; fix auth, not the extract)";
    }
}
