// =============================================================================
// RetryAnalyzer -- TestNG IRetryAnalyzer
// -----------------------------------------------------------------------------
// Attach at method level:      @Test(retryAnalyzer = RetryAnalyzer.class)
// Attach globally via listener: see RetryTransformer
//
// Max retry count is driven by Config (retry.maxCount) so it can be tuned
// per environment or dialed to 0 in CI when investigating a flake.
//
// Per-INVOCATION counter (not per-instance): TestNG reuses a single
// IRetryAnalyzer object across every data-provider row of a @Test method.
// A per-instance `int attempts` field leaks across rows -- row 1 burns
// the retry budget, rows 2..N get zero retries and are reported as hard
// fails on the first transient error. Keyed on the ITestResult identity
// (class + method + data-row hash) so each row gets its own counter and
// true retries -- which reuse identity -- share one.
// =============================================================================

package com.hi.api.retry;

import java.util.Arrays;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import org.testng.IRetryAnalyzer;
import org.testng.ITestResult;

import com.hi.api.config.Config;
import com.hi.api.rest.utilities.FlowStopped;

public class RetryAnalyzer implements IRetryAnalyzer {

    private static final Map<String, Integer> ATTEMPTS_BY_KEY = new ConcurrentHashMap<>();

    // Bounded to keep memory finite over long CI runs. A test that
    // fails-once-then-succeeds never has its counter removed (TestNG
    // does not call retry() on success), so each such flake plants an
    // entry that lives forever -- across Surefire fork reuse this
    // grows unbounded. When the map crosses MAX_ENTRIES we clear it
    // wholesale: cheap, and the only user-visible effect is that any
    // in-flight retry sequence gets 1 extra retry (counter reset to
    // 0). Trivial trade-off vs. an OOM in a long CI run.
    private static final int MAX_ENTRIES = 1000;

    private static String key(ITestResult r) {
        return r.getTestClass().getRealClass().getName()
                + "#" + r.getMethod().getMethodName()
                + "@" + Arrays.deepHashCode(r.getParameters());
    }

    @Override
    public boolean retry(ITestResult result) {
        // Bounded-eviction sweep. Cheap size() on ConcurrentHashMap
        // and clear() is O(n) but only triggers at the cap.
        if (ATTEMPTS_BY_KEY.size() > MAX_ENTRIES) {
            ATTEMPTS_BY_KEY.clear();
        }
        int max = Config.retryMaxCount();
        String k = key(result);
        if (settledRejection(result)) {
            // The same refusal twice running: this test sends the same
            // thing every time. Counter to max so the listeners count this
            // attempt as the terminal failure it is.
            ATTEMPTS_BY_KEY.put(k, Math.max(max, 0));
            System.err.printf("[Retry] %s.%s -- not retried again: %s%n",
                    result.getTestClass().getRealClass().getSimpleName(),
                    result.getMethod().getMethodName(),
                    "the server refused the same write the same way twice");
            return false;
        }
        rememberRejection(result);
        int attempts = ATTEMPTS_BY_KEY.getOrDefault(k, 0);
        if (attempts < max) {
            attempts++;
            ATTEMPTS_BY_KEY.put(k, attempts);
            System.err.printf("[Retry] %s.%s -- attempt %d/%d%n",
                    result.getTestClass().getRealClass().getSimpleName(),
                    result.getMethod().getMethodName(),
                    attempts, max);
            return true;
        }
        // Leave the counter at max so moreRetriesRemain() is false for
        // the terminal failure (listeners still need to count it once).
        // Cleared by TestInvocations after the terminal outcome.
        return false;
    }

    /**
     * True when this invocation still has retry budget. Intermediate
     * failures must not increment suite / GitLab / Xray / Extent totals.
     */
    public static boolean moreRetriesRemain(ITestResult result) {
        if (result == null) return false;
        int max = Config.retryMaxCount();
        if (max <= 0) return false;
        if (settledRejection(result)) return false;
        return ATTEMPTS_BY_KEY.getOrDefault(key(result), 0) < max;
    }

    /** The refusal a test's PREVIOUS attempt ended on: {signature, that attempt's start time}. */
    private static final Map<String, Object[]> LAST_REJECTION = new ConcurrentHashMap<>();

    /**
     * Did this attempt end on the SAME refused write, refused the same
     * way, as the attempt before it?
     *
     * <p>One refusal proves little: an enroll answered "Username is not
     * unique" collided on a random value, and the next attempt draws a
     * new one (it passed on attempt 2 in the run this was measured on).
     * The same step refused with the same message twice running is the
     * server rejecting what the test sends -- a third attempt gets a
     * third copy of the answer.</p>
     *
     * <p>Rejections that may clear on their own (5xx, 401/403, 408, 409,
     * 429, known-temporary 400s) are never settled; see
     * {@link FlowStopped#worthRetrying}. {@code test.retryRejectedWrite=true}
     * switches the rule off.</p>
     */
    static boolean settledRejection(ITestResult result) {
        Throwable t = result == null ? null : result.getThrowable();
        if (!(t instanceof FlowStopped) || ((FlowStopped) t).worthRetrying()) {
            return false;
        }
        if (Config.getBool("test.retryRejectedWrite", false)) {
            return false;
        }
        Object[] last = LAST_REJECTION.get(key(result));
        // A different attempt (by start time) with the same signature. The
        // start time keeps this from matching the entry THIS attempt wrote,
        // whichever of retry() and the listeners runs first.
        return last != null
                && ((FlowStopped) t).signature().equals(last[0])
                && !Long.valueOf(result.getStartMillis()).equals(last[1]);
    }

    private static void rememberRejection(ITestResult result) {
        Throwable t = result.getThrowable();
        if (t instanceof FlowStopped && !((FlowStopped) t).worthRetrying()) {
            if (LAST_REJECTION.size() > MAX_ENTRIES) {
                LAST_REJECTION.clear();
            }
            LAST_REJECTION.put(key(result),
                    new Object[] {((FlowStopped) t).signature(), result.getStartMillis()});
        }
    }

    /** Drop the per-invocation counter after a terminal pass/fail. */
    public static void clearAttempts(ITestResult result) {
        if (result == null) return;
        ATTEMPTS_BY_KEY.remove(key(result));
        LAST_REJECTION.remove(key(result));
    }

    /** Test hook. */
    public static void resetAttempts() {
        ATTEMPTS_BY_KEY.clear();
        LAST_REJECTION.clear();
    }
}
