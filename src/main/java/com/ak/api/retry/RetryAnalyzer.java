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

package com.ak.api.retry;

import java.util.Arrays;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import org.testng.IRetryAnalyzer;
import org.testng.ITestResult;

import com.ak.api.config.Config;

public class RetryAnalyzer implements IRetryAnalyzer {

    private static final Map<String, Integer> ATTEMPTS_BY_KEY = new ConcurrentHashMap<>();

    private static String key(ITestResult r) {
        return r.getTestClass().getRealClass().getName()
                + "#" + r.getMethod().getMethodName()
                + "@" + Arrays.deepHashCode(r.getParameters());
    }

    @Override
    public boolean retry(ITestResult result) {
        int max = Config.retryMaxCount();
        String k = key(result);
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
        // Terminal outcome for this invocation: drop the counter so a
        // future re-invocation with the same params (rare, but happens
        // in some TestNG configs) starts fresh.
        ATTEMPTS_BY_KEY.remove(k);
        return false;
    }
}
