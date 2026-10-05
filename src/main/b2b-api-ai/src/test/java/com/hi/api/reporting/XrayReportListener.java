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

package com.hi.api.reporting;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import org.testng.IInvokedMethod;
import org.testng.IInvokedMethodListener;
import org.testng.ISuite;
import org.testng.ISuiteListener;
import org.testng.ITestResult;

import com.hi.api.xray.XrayClient;
import com.hi.api.xray.XrayResult;
import com.hi.api.xray.XrayResultsCollector;
import com.hi.api.xray.XrayTest;

public class XrayReportListener implements IInvokedMethodListener, ISuiteListener {

    private static final String XRAY_ID_COLUMN = "jira_xray_id";

    /**
     * An Xray test key, e.g. {@code B2B-8920} or {@code PROJ-101}.
     *
     * <p>Checked because the column is not guaranteed to hold one.
     * Measured on this tree: 1,318 of 1,322 generated rows carry a
     * {@code jira_xray_id} of {@code B2B-8920_LTA_get_accountBooking
     * Metrics_monthToDateBookingMetrics_200} -- the ReadyAPI case name,
     * not a key. The real ticket is in the sibling {@code jira_issue}
     * column.</p>
     *
     * <p>Without this guard, turning {@code xray.enabled=true} on
     * publishes a result for every one of those rows against a key Xray
     * cannot resolve. Xray reports the import, not the rows, so the run
     * would look like it synced. A skip that names the offending value is
     * the only outcome from which someone can tell what happened.</p>
     */
    private static final java.util.regex.Pattern ISSUE_KEY =
            java.util.regex.Pattern.compile("[A-Z][A-Z0-9_]*-\\d+");

    /** Keys already reported as malformed, so the log says each once. */
    private static final java.util.Set<String> WARNED =
            java.util.concurrent.ConcurrentHashMap.newKeySet();

    /** True when this value can be an Xray test key. */
    public static boolean looksLikeIssueKey(String key) {
        return key != null && ISSUE_KEY.matcher(key.trim()).matches();
    }

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
        if (TestInvocations.supersededByRetry(testResult)) {
            XrayResultsCollector.clearCurrentKey();
            return;
        }
        String key = XrayResultsCollector.getCurrentKey();
        if (key == null) return;   // no jira_xray_id in row -> nothing to sync
        if (!looksLikeIssueKey(key)) {
            // Fail closed: publishing this would put a result against a
            // key Xray cannot resolve, and the import would still look
            // like it worked. See ISSUE_KEY above for the measurement.
            if (WARNED.add(key)) {
                System.out.println("[XrayClient] skipped -- " + XRAY_ID_COLUMN
                        + "=\"" + key + "\" is not an issue key like ABC-123. "
                        + "The generated column often holds the ReadyAPI case "
                        + "name; the ticket is in `jira_issue`. Put the real "
                        + "Xray TEST key in " + XRAY_ID_COLUMN + ", or use "
                        + "@XrayTest(\"...\") on the method.");
            }
            XrayResultsCollector.clearCurrentKey();
            return;
        }

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
