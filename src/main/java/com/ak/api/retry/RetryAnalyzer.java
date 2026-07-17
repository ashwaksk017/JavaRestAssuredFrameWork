// =============================================================================
// RetryAnalyzer -- TestNG IRetryAnalyzer
// -----------------------------------------------------------------------------
// Attach at method level:      @Test(retryAnalyzer = RetryAnalyzer.class)
// Attach globally via listener: see RetryTransformer
//
// Max retry count is driven by Config (retry.maxCount) so it can be tuned
// per environment or dialed to 0 in CI when investigating a flake.
// =============================================================================

package com.ak.api.retry;

import org.testng.IRetryAnalyzer;
import org.testng.ITestResult;

import com.ak.api.config.Config;

public class RetryAnalyzer implements IRetryAnalyzer {

    private int attempts = 0;

    @Override
    public boolean retry(ITestResult result) {
        int max = Config.retryMaxCount();
        if (attempts < max) {
            attempts++;
            System.err.printf("[Retry] %s.%s -- attempt %d/%d%n",
                    result.getTestClass().getRealClass().getSimpleName(),
                    result.getMethod().getMethodName(),
                    attempts, max);
            return true;
        }
        return false;
    }
}
