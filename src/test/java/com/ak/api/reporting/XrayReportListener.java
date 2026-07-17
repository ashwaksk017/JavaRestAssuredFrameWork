// =============================================================================
// XrayReportListener -- capture jira_xray_id per test, batch POST at suite end
// -----------------------------------------------------------------------------
// Combines two TestNG hook types:
//
//   IInvokedMethodListener
//     beforeInvocation -- scan the test method's parameters. If any is a
//                         Map<?,?> with a non-blank "jira_xray_id" value,
//                         stash it as the current-thread key.
//     afterInvocation  -- read testResult.getStatus() (PASSED/FAILED/SKIPPED),
//                         combine with the stashed key + throwable message,
//                         record into XrayResultsCollector.
//
//   ISuiteListener
//     onFinish         -- drain the collector, hand results to XrayClient
//                         for one batched POST to /api/v2/import/execution.
//
// Everything is a no-op when xray.enabled=false (default), so this listener
// is safe to leave registered in testng.xml even on public-repo runs.
// =============================================================================

package com.ak.api.reporting;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import org.testng.IInvokedMethod;
import org.testng.IInvokedMethodListener;
import org.testng.ISuite;
import org.testng.ISuiteListener;
import org.testng.ITestResult;

import com.ak.api.xray.XrayClient;
import com.ak.api.xray.XrayResult;
import com.ak.api.xray.XrayResultsCollector;
import com.ak.api.xray.XrayTest;

public class XrayReportListener implements IInvokedMethodListener, ISuiteListener {

    private static final String XRAY_ID_COLUMN = "jira_xray_id";

    private Instant suiteStartedAt;

    // =========================================================================
    // Per-invocation hooks
    // =========================================================================

    @Override
    public void beforeInvocation(IInvokedMethod method, ITestResult testResult) {
        if (!method.isTestMethod()) return;

        // 1. Row column takes precedence -- data-driven tests can carry a
        //    different jira_xray_id per row, so this must beat method-level
        //    annotations when both are present.
        Object[] params = testResult.getParameters();
        if (params != null) {
            for (Object p : params) {
                if (p instanceof Map<?, ?> rowMap) {
                    Object idObj = rowMap.get(XRAY_ID_COLUMN);
                    if (idObj != null) {
                        String id = idObj.toString().trim();
                        if (!id.isEmpty()) {
                            XrayResultsCollector.setCurrentKey(id);
                            return;
                        }
                    }
                }
            }
        }

        // 2. Fall back to @XrayTest("PROJ-101") on the test method for
        //    non-data-driven tests (smoke tests, single-shot integration checks,
        //    unit-style assertions). Reflected off the constructor-or-method
        //    handle -- ITestNGMethod doesn't expose annotations directly.
        java.lang.reflect.Method reflectMethod =
                testResult.getMethod().getConstructorOrMethod().getMethod();
        if (reflectMethod != null) {
            XrayTest ann = reflectMethod.getAnnotation(XrayTest.class);
            if (ann != null && ann.value() != null && !ann.value().isBlank()) {
                XrayResultsCollector.setCurrentKey(ann.value().trim());
            }
        }
    }

    @Override
    public void afterInvocation(IInvokedMethod method, ITestResult testResult) {
        if (!method.isTestMethod()) return;
        String key = XrayResultsCollector.getCurrentKey();
        if (key == null) return;   // no jira_xray_id in row -> nothing to sync

        XrayResult.Status status = XrayResult.Status.fromTestNg(testResult.getStatus());
        String comment = null;
        if (testResult.getThrowable() != null) {
            comment = testResult.getThrowable().getMessage();
            if (comment == null) comment = testResult.getThrowable().toString();
        }
        long duration = testResult.getEndMillis() - testResult.getStartMillis();

        XrayResultsCollector.record(new XrayResult(key, status, comment, duration));
        XrayResultsCollector.clearCurrentKey();
    }

    // =========================================================================
    // Suite lifecycle
    // =========================================================================

    @Override
    public void onStart(ISuite suite) {
        suiteStartedAt = Instant.now();
    }

    @Override
    public void onFinish(ISuite suite) {
        Instant finishedAt = Instant.now();
        List<XrayResult> results = XrayResultsCollector.drain();
        // XrayClient handles the disabled / missing-creds case internally
        // and logs a friendly message either way.
        new XrayClient().importResults(results,
                suiteStartedAt == null ? finishedAt : suiteStartedAt,
                finishedAt);
    }
}
