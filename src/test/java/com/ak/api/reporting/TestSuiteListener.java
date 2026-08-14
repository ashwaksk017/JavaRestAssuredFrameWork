// =============================================================================
// TestSuiteListener -- centralises pass/fail counters and suite-scoped reset
// -----------------------------------------------------------------------------
// Fixes reference gaps:
//   P2.17  counters advance via ITestListener callbacks, not by hand in a test
//          @AfterMethod. That means a test that throws before reaching assertAll
//          still gets counted as failed.
//   P2.18  reset() runs once per suite, not per class.
// =============================================================================

package com.ak.api.reporting;

import java.util.concurrent.atomic.AtomicInteger;

import org.testng.ISuite;
import org.testng.ISuiteListener;
import org.testng.ITestContext;
import org.testng.ITestListener;
import org.testng.ITestResult;

import com.ak.api.rest.utilities.RestUtilities;

public class TestSuiteListener implements ISuiteListener, ITestListener {

    /** Skips deserve their own count -- a suite full of "SKIPPED because
     *  JDBC mutation not translated" hides silently if we only print
     *  pass/fail. Auditors need to see coverage loss loudly. */
    private static final AtomicInteger SKIPPED = new AtomicInteger(0);

    @Override
    public void onStart(ISuite suite) {
        RestUtilities.reset();
        SKIPPED.set(0);
    }

    @Override
    public void onFinish(ISuite suite) {
        int p = RestUtilities.getPassed().get();
        int f = RestUtilities.getFailed().get();
        int s = SKIPPED.get();
        int total = p + f + s;
        System.out.printf("%n=== Suite '%s' -- passed=%d, failed=%d, skipped=%d, total=%d ===%n",
                suite.getName(), p, f, s, total);
    }

    @Override public void onStart(ITestContext context)        { /* no-op */ }
    @Override public void onFinish(ITestContext context)       { /* no-op */ }
    @Override public void onTestStart(ITestResult result)      { /* no-op */ }

    @Override
    public void onTestSuccess(ITestResult result) {
        RestUtilities.getPassed().incrementAndGet();
    }

    @Override
    public void onTestFailure(ITestResult result) {
        RestUtilities.getFailed().incrementAndGet();
    }

    @Override
    public void onTestSkipped(ITestResult result) {
        SKIPPED.incrementAndGet();
    }
}
