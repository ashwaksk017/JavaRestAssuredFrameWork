package com.hi.api.rest.utilities;

/**
 * The last non-2xx step seen in the current test.
 *
 * <p>A "Broken REST path -- EMPTY path segment" failure says an id was
 * missing but not WHY. Two causes need opposite fixes: the value had not
 * arrived yet (wait for it) or the call that should have minted it was
 * rejected (there is nothing to wait for). Guessing between them cost a
 * whole round -- a poll was added for ~115 such failures and moved 40 to 39,
 * because the ids were never created rather than late.</p>
 *
 * <p>Recorded from {@code RestStep}, which every request passes through, and
 * read by the failure digest so each broken-path line names the call that
 * actually failed first.</p>
 *
 * <p>Thread-scoped: TestNG runs classes in parallel, so one test's upstream
 * failure must not be attributed to another's.</p>
 */
public final class StepOutcomes {

    /**
     * FIRST non-2xx, not the last.
     *
     * <p>This used to overwrite on every failing call, so it held the LAST
     * one -- while the digest printed it as "first bad call". A reader
     * reasoning about ordering from that column was reasoning from the
     * opposite of what it contained. First-failure is also the more useful
     * signal for the broken-path question this class exists to answer: the
     * call that failed EARLIEST is the one that did not mint the id, and
     * everything after it is a symptom.</p>
     */
    private static final ThreadLocal<String> FIRST_BAD = new ThreadLocal<>();

    /** Response body of {@link #FIRST_BAD}, capped and masked by the caller. */
    private static final ThreadLocal<String> FIRST_BAD_BODY = new ThreadLocal<>();

    private StepOutcomes() {
    }

    private static final ThreadLocal<String> FIRST_BAD_URL = new ThreadLocal<>();
    private static final ThreadLocal<String> FIRST_BAD_REQ = new ThreadLocal<>();

    public static void record(String stepName, int statusCode) {
        record(stepName, statusCode, -1, null);
    }

    public static void record(String stepName, int statusCode, int expectedStatus) {
        record(stepName, statusCode, expectedStatus, null);
    }

    /**
     * @param expectedStatus the status the step asserts ({@code <= 0}: none).
     *        A response matching it is the step PASSING -- a negative test
     *        that asked for 400 and got 400 -- not an upstream failure.
     *        Counting those blamed tests on calls that worked and inflated
     *        the digest's "first failing call" totals.
     */
    /**
     * @param body the response body, already capped AND masked by the caller.
     *        Kept only for the first failure, so a 44-row data-driven test
     *        does not accumulate 44 copies. Null is fine.
     */
    public static void record(String stepName, int statusCode, int expectedStatus,
                              String body) {
        if (statusCode >= 200 && statusCode < 300) {
            return;
        }
        if (expectedStatus > 0 && statusCode == expectedStatus) {
            return;
        }
        if (FIRST_BAD.get() != null) {
            return;   // keep the EARLIEST failure, not the latest
        }
        FIRST_BAD.set((stepName == null ? "?" : stepName) + " -> HTTP " + statusCode);
        if (body != null && !body.isEmpty()) {
            FIRST_BAD_BODY.set(body);
        }
        // The REQUEST behind that failure, captured now rather than
        // reconstructed later.
        //
        // The digest could quote the server's complaint but never what we
        // sent, so "Invalid JSON Parameter Value, fields:[startDate]" left
        // the actual value invisible -- and the value was the whole bug
        // (a date arriving as `2026-12-17 00:00:00`). One round trip per
        // failure was spent asking for it.
        //
        // LastExchange already holds the URI and a REDACTED body keyed by
        // step, so this is a lookup, not new plumbing. Guarded: a step
        // that failed before any exchange was filed simply records
        // nothing.
        try {
            com.hi.api.rest.utilities.LastExchange.Recorded r =
                    com.hi.api.rest.utilities.LastExchange.recordedOf(stepName);
            if (r != null) {
                if (r.uri() != null && !r.uri().isEmpty()) {
                    FIRST_BAD_URL.set(r.method() + " " + r.uri());
                }
                if (r.requestBody() != null && !r.requestBody().isEmpty()) {
                    FIRST_BAD_REQ.set(r.requestBody());
                }
            }
        } catch (RuntimeException ignored) {
            // Never let a reporting aid break a test run.
        }
    }

    /** First non-2xx on this thread, or null when every call so far succeeded. */
    public static String firstFailure() {
        return FIRST_BAD.get();
    }

    /** @deprecated misleading name -- this was never the last failure. */
    @Deprecated
    public static String lastFailure() {
        return firstFailure();
    }

    /** Response body that came back with {@link #firstFailure()}, or null. */
    public static String firstFailureBody() {
        return FIRST_BAD_BODY.get();
    }

    /** `METHOD uri` of the call behind {@link #firstFailure()}, or null. */
    public static String firstFailureUrl() {
        return FIRST_BAD_URL.get();
    }

    /**
     * REDACTED request body of the call behind {@link #firstFailure()}.
     *
     * <p>Redacted at the source: LastExchange stores what the recording
     * filter passed it, and the token request's body IS the client
     * secret. A digest is pasted into chats and tickets, so it must not
     * become the second place a credential is readable.</p>
     */
    public static String firstFailureRequest() {
        return FIRST_BAD_REQ.get();
    }

    /** Called at test start so a previous test's failure is not inherited. */
    public static void reset() {
        FIRST_BAD.remove();
        FIRST_BAD_BODY.remove();
        FIRST_BAD_URL.remove();
        FIRST_BAD_REQ.remove();
    }
}
