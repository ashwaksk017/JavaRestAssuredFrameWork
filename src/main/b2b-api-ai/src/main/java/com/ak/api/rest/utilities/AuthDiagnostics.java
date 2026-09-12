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
