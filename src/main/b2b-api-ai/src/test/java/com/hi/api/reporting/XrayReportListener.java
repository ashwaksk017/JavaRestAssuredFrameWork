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
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.hi.api.xray.XrayClient;
import com.hi.api.xray.XrayResult;
import com.hi.api.xray.XrayResultsCollector;
import com.hi.api.xray.XrayRowKeys;
import com.hi.api.xray.XrayTest;

public class XrayReportListener implements IInvokedMethodListener, ISuiteListener {

    private static final Logger LOG = LoggerFactory.getLogger(XrayReportListener.class);

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
    /** Keys already reported as malformed, so the log says each once. */
    private static final java.util.Set<String> WARNED =
            java.util.concurrent.ConcurrentHashMap.newKeySet();

    /** True when this value can be an Xray test key. */
    public static boolean looksLikeIssueKey(String key) {
        return XrayRowKeys.looksLikeIssueKey(key);
    }

    private Instant suiteStartedAt;

    /**
     * Which test methods ({@code Class#method}) used each Xray key in this
     * suite. One Xray test is one test method: a key two methods carry is
     * reported for neither.
     *
     * <p>Seen live: story B2B-99454's TC-001 key B2B-99802 was written into
     * the TC-001 rows of two OTHER tests' sheets. The real test skipped, an
     * unrelated one passed, and Xray recorded B2B-99802 as passed. Filled in
     * {@code beforeInvocation} as well as after: a skipped test that will be
     * retried records no result, but it still owns the key.</p>
     */
    private static final Map<String, java.util.Set<String>> OWNERS =
            new java.util.concurrent.ConcurrentHashMap<>();

    private static void noteOwner(String key, ITestResult testResult) {
        if (key == null || testResult == null || testResult.getMethod() == null) return;
        String owner = testResult.getMethod().getRealClass().getName() + "#"
                + testResult.getMethod().getMethodName();
        OWNERS.computeIfAbsent(key.trim().toUpperCase(java.util.Locale.ROOT),
                k -> java.util.concurrent.ConcurrentHashMap.newKeySet()).add(owner);
    }

    /**
     * The results to publish: every one whose key is not used by two or
     * more different test methods. Each key left out is said once, with
     * the methods that share it.
     */
    public static List<XrayResult> withoutSharedKeys(List<XrayResult> results,
                                                     Map<String, ? extends java.util.Collection<String>> owners,
                                                     java.util.function.Consumer<String> say) {
        // Key (upper case) -> the distinct methods that used it.
        Map<String, java.util.Set<String>> byKey = new java.util.TreeMap<>();
        for (Map.Entry<String, ? extends java.util.Collection<String>> e : owners.entrySet()) {
            if (e.getKey() == null || e.getValue() == null) continue;
            byKey.computeIfAbsent(e.getKey().trim().toUpperCase(java.util.Locale.ROOT),
                    k -> new java.util.TreeSet<>()).addAll(e.getValue());
        }
        java.util.Set<String> shared = new java.util.TreeSet<>();
        for (Map.Entry<String, java.util.Set<String>> e : byKey.entrySet()) {
            if (e.getValue().size() > 1) {
                shared.add(e.getKey());
                say.accept("[XrayClient] NOT reported: " + e.getKey() + " is the jira_xray_id of "
                        + e.getValue().size() + " different tests (" + String.join(", ", e.getValue())
                        + "). One Xray test is one test method -- fix the rows' jira_xray_id; "
                        + "nothing was sent for this key.");
            }
        }
        if (shared.isEmpty() || results == null) return results;
        List<XrayResult> kept = new java.util.ArrayList<>();
        for (XrayResult r : results) {
            String k = r.testKey() == null ? "" : r.testKey().trim().toUpperCase(java.util.Locale.ROOT);
            if (!shared.contains(k)) kept.add(r);
        }
        return kept;
    }

    // =========================================================================
    // Per-invocation hooks
    // =========================================================================

    @Override
    public void beforeInvocation(IInvokedMethod method, ITestResult testResult) {
        if (!method.isTestMethod()) return;

        String key = resolveReportingKey(testResult);
        if (key != null) {
            XrayResultsCollector.setCurrentKey(key);
            noteOwner(key, testResult);
        }
    }

    /**
     * Row {@code jira_xray_id} wins over {@link XrayTest} on the method.
     * Called from {@code beforeInvocation} and again from {@code afterInvocation}
     * when the row Map was not on {@link ITestResult#getParameters()} yet
     * (common for data-provider tests under Surefire {@code -Dtest=}).
     */
    static String resolveReportingKey(ITestResult testResult) {
        if (testResult == null) {
            return null;
        }
        String fromRow = keyFromRowParameters(testResult.getParameters());
        if (fromRow != null) {
            return fromRow;
        }
        java.lang.reflect.Method reflectMethod =
                testResult.getMethod().getConstructorOrMethod().getMethod();
        if (reflectMethod == null) {
            return null;
        }
        XrayTest ann = reflectMethod.getAnnotation(XrayTest.class);
        if (ann != null && ann.value() != null && !ann.value().isBlank()) {
            return ann.value().trim();
        }
        return null;
    }

    private static String keyFromRowParameters(Object[] params) {
        if (params == null) {
            return null;
        }
        for (Object p : params) {
            if (p instanceof Map<?, ?> rowMap) {
                @SuppressWarnings("unchecked")
                Map<String, String> row = (Map<String, String>) rowMap;
                XrayRowKeys.fillFromConfig(row);
                Object idObj = row.get(XRAY_ID_COLUMN);
                if (idObj != null) {
                    String id = idObj.toString().trim();
                    if (!id.isEmpty()) {
                        return id;
                    }
                }
            }
        }
        return null;
    }

    @Override
    public void afterInvocation(IInvokedMethod method, ITestResult testResult) {
        if (!method.isTestMethod()) return;
        if (TestInvocations.supersededByRetry(testResult)) {
            XrayResultsCollector.clearCurrentKey();
            return;
        }
        String key = XrayResultsCollector.getCurrentKey();
        if (key == null) {
            key = resolveReportingKey(testResult);
        }
        if (key == null) return;   // no jira_xray_id in row -> nothing to sync
        if (!looksLikeIssueKey(key)) {
            // Fail closed: publishing this would put a result against a
            // key Xray cannot resolve, and the import would still look
            // like it worked. See ISSUE_KEY above for the measurement.
            if (WARNED.add(key)) {
                LOG.info("[XrayClient] skipped -- {}=\"{}\" is not an issue key like ABC-123. "
                        + "The generated column often holds the ReadyAPI case name; the ticket is "
                        + "in `jira_issue`. Put the real Xray TEST key in {}, or use @XrayTest on "
                        + "the method.", XRAY_ID_COLUMN, key, XRAY_ID_COLUMN);
            }
            XrayResultsCollector.clearCurrentKey();
            return;
        }

        noteOwner(key, testResult);
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
        List<XrayResult> results = withoutSharedKeys(XrayResultsCollector.drain(),
                new java.util.HashMap<>(OWNERS), LOG::warn);
        OWNERS.clear();
        // XrayClient handles the disabled / missing-creds case internally
        // and logs a friendly message either way.
        new XrayClient().importResults(results,
                suiteStartedAt == null ? finishedAt : suiteStartedAt,
                finishedAt);
    }
}
