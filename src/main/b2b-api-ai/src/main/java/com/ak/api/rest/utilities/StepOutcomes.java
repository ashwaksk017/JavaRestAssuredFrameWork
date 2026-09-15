package com.ak.api.rest.utilities;

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

    private static final ThreadLocal<String> LAST_BAD = new ThreadLocal<>();

    private StepOutcomes() {
    }

    public static void record(String stepName, int statusCode) {
        record(stepName, statusCode, -1);
    }

    /**
     * @param expectedStatus the status the step asserts ({@code <= 0}: none).
     *        A response matching it is the step PASSING -- a negative test
     *        that asked for 400 and got 400 -- not an upstream failure.
     *        Counting those blamed tests on calls that worked and inflated
     *        the digest's "first failing call" totals.
     */
    public static void record(String stepName, int statusCode, int expectedStatus) {
        if (statusCode >= 200 && statusCode < 300) {
            return;
        }
        if (expectedStatus > 0 && statusCode == expectedStatus) {
            return;
        }
        LAST_BAD.set((stepName == null ? "?" : stepName) + " -> HTTP " + statusCode);
    }

    /** Last non-2xx on this thread, or null when every call so far succeeded. */
    public static String lastFailure() {
        return LAST_BAD.get();
    }

    /** Called at test start so a previous test's failure is not inherited. */
    public static void reset() {
        LAST_BAD.remove();
    }
}
