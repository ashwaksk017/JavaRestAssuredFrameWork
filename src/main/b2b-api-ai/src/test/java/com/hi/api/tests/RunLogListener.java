package com.hi.api.tests;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.IConfigurationListener;
import org.testng.ISuite;
import org.testng.ISuiteListener;
import org.testng.ISuiteResult;
import org.testng.ITestContext;
import org.testng.ITestListener;
import org.testng.ITestResult;

import com.hi.api.config.Config;
import com.hi.api.rest.utilities.LastExchange;

/**
 * The full run log (target/b2b-full-run.log) says which test ran with which
 * data, and -- when one fails -- everything needed to see why without
 * running it again.
 *
 * <p>Asked for: a failure in that log was one stack trace among thousands of
 * lines, with no case id, no Xray key and no request beside it. Every failed
 * test now ends with a block:</p>
 * <pre>
 * ==== TEST FAILED: GuestProgramAccountTest.create  [case TC-003 | xray ABC-12 | story ABC-1 | env stg | 2.3 s]
 *   why        : java.lang.AssertionError: The following asserts failed: ...
 *   assertion  : TC-003/AC-2: status expected [201] but found [400]
 *   calls      : tokenRequest -> 200, enrollGuest -> 200, createProgramAccount -> 400
 *   last call  : POST https://.../guests/123/businesses -> 400 (createProgramAccount)
 *   sent       : {"name": ...}
 *   answered   : {"errors": [...]}
 *   at         : GuestProgramAccountTest.java:88
 * ==== end of TEST FAILED
 * </pre>
 *
 * <p>A test of a converted suite already logs its own start and end
 * (ProgressLogListener, and its STARTED/FINISHED lines); only the failure
 * block is added for those. Bodies come from {@link LastExchange}, whose
 * request body is already redacted; the log's own rewrite policy redacts
 * credentials in everything else.</p>
 *
 * <p>Hand-written, beside BaseApiTest -- not in the generated reporting
 * package -- and registered in META-INF/services, which the converter
 * rewrites keeping the listeners it does not own. Never throws: a log line
 * must not be why a test fails.</p>
 */
public class RunLogListener implements ITestListener, ISuiteListener, IConfigurationListener {

    /**
     * RUN START: what the run is, against what -- and from here on, what is
     * printed to the console goes into the run log too (ConsoleToRunLog).
     */
    @Override
    public void onStart(ISuite suite) {
        ConsoleToRunLog.install();
        try {
            String project = Config.projectName();
            LOG.info("RUN START suite {} | env {} | project {} | config {} | base url {} | Java {} ({})",
                    suite.getName(), Config.env(), project,
                    "b2b".equals(project) ? "program_configuration.json" : "program_configuration." + project + ".json",
                    Config.baseUrl(), System.getProperty("java.version"), System.getProperty("java.home"));
        } catch (RuntimeException | Error e) {
            LOG.warn("RUN START suite {} (the configuration could not be read: {})", suite.getName(), e.toString());
        }
    }

    /** RUN END: how many passed, failed, were skipped. */
    @Override
    public void onFinish(ISuite suite) {
        try {
            int passed = 0, failed = 0, skipped = 0;
            for (ISuiteResult r : suite.getResults().values()) {
                ITestContext c = r.getTestContext();
                passed += c.getPassedTests().size();
                failed += c.getFailedTests().size() + c.getFailedButWithinSuccessPercentageTests().size();
                skipped += c.getSkippedTests().size();
            }
            LOG.info("RUN END   suite {} | {} passed, {} failed, {} skipped", suite.getName(), passed, failed, skipped);
        } catch (RuntimeException ignored) {
            // never the reason a run fails
        }
    }

    /**
     * A setup or teardown method that failed (@BeforeClass, @BeforeMethod...):
     * the tests after it are skipped for it. Allure hides these
     * (allure.testng.hide.configuration.failures), so the run log says them.
     */
    @Override
    public void onConfigurationFailure(ITestResult r) {
        try {
            Throwable t = r.getThrowable();
            LOG.error("SETUP METHOD FAILED {}.{} -- {}{}", r.getTestClass().getRealClass().getSimpleName(),
                    r.getMethod().getMethodName(), t == null ? "(no error given)" : t.toString(),
                    t == null || t.getStackTrace().length == 0 ? "" : " at " + t.getStackTrace()[0]);
        } catch (RuntimeException ignored) {
            // never the reason a test fails
        }
    }

    @Override
    public void onConfigurationSkip(ITestResult r) {
        try {
            LOG.warn("SETUP METHOD SKIPPED {}.{} (an earlier setup method failed, or the run stopped)",
                    r.getTestClass().getRealClass().getSimpleName(), r.getMethod().getMethodName());
        } catch (RuntimeException ignored) {
            // never the reason a test fails
        }
    }

    @Override
    public void onConfigurationSuccess(ITestResult r) {
        // nothing: only what went wrong
    }

    private static final Logger LOG = LoggerFactory.getLogger("RUN");
    private static final int SENT_CAP = 1500;
    private static final int ANSWER_CAP = 2500;
    private static final int ASSERT_LINES = 25;

    @Override
    public void onTestStart(ITestResult result) {
        try {
            if (!converted(result)) {
                LOG.info("TEST START {}  [{}]{}", name(result), ids(result, false), description(result));
            }
        } catch (RuntimeException ignored) {
            // never the reason a test fails
        }
    }

    @Override
    public void onTestSuccess(ITestResult result) {
        end(result, "PASSED");
    }

    @Override
    public void onTestSkipped(ITestResult result) {
        try {
            Throwable t = result.getThrowable();
            String why = t == null ? "" : firstLine(t.getMessage());
            if (!converted(result)) {
                LOG.info("TEST END   {}  SKIPPED  [{}]{}", name(result), ids(result, true),
                        why.isEmpty() ? "" : " -- " + why);
            }
        } catch (RuntimeException ignored) {
            // never the reason a test fails
        }
    }

    @Override
    public void onTestFailure(ITestResult result) {
        try {
            failureBlock(result);
        } catch (RuntimeException e) {
            LOG.warn("TEST FAILED {} (the failure summary could not be written: {})", name(result), e.toString());
        }
        end(result, "FAILED");
    }

    @Override
    public void onTestFailedButWithinSuccessPercentage(ITestResult result) {
        onTestFailure(result);
    }

    private void end(ITestResult result, String verdict) {
        try {
            if (!converted(result)) {
                LOG.info("TEST END   {}  {}  [{}]", name(result), verdict, ids(result, true));
            }
        } catch (RuntimeException ignored) {
            // never the reason a test fails
        }
    }

    private void failureBlock(ITestResult result) {
        List<String> out = new ArrayList<>();
        out.add("==== TEST FAILED: " + name(result) + "  [" + ids(result, true) + "]");
        Throwable t = result.getThrowable();
        if (t != null) {
            out.add("  why        : " + t.getClass().getName() + ": " + firstLine(t.getMessage()));
            int shown = 0;
            for (String line : String.valueOf(t.getMessage()).split("\\r?\\n")) {
                String a = line.trim().replaceAll(",$", "");
                if (a.isEmpty() || a.startsWith("The following asserts failed")) {
                    continue;
                }
                if (shown++ >= ASSERT_LINES) {
                    out.add("  assertion  : ... and more (the whole message is in the test report)");
                    break;
                }
                out.add("  assertion  : " + a);
            }
            String at = where(t, result);
            if (!at.isEmpty()) {
                out.add("  at         : " + at);
            }
        }
        List<LastExchange.Recorded> calls = LastExchange.all();
        if (!calls.isEmpty()) {
            StringBuilder trail = new StringBuilder();
            for (LastExchange.Recorded r : calls) {
                trail.append(trail.length() == 0 ? "" : ", ").append(r.step()).append(" -> ").append(r.status());
            }
            out.add("  calls      : " + trail);
            LastExchange.Recorded last = calls.get(calls.size() - 1);
            out.add("  last call  : " + last);
            if (!last.requestBody().isEmpty()) {
                out.add("  sent       : " + cap(last.requestBody(), SENT_CAP));
            }
            String answered = "";
            try {
                answered = last.response() == null ? "" : last.response().asString();
            } catch (RuntimeException e) {
                answered = "(the response body could not be read: " + e.getClass().getSimpleName() + ")";
            }
            if (answered != null && !answered.isEmpty()) {
                out.add("  answered   : " + cap(answered, ANSWER_CAP));
            }
        } else {
            out.add("  calls      : none recorded (the test failed before its first request)");
        }
        out.add("==== end of TEST FAILED");
        LOG.error(String.join(System.lineSeparator(), out));
    }

    // ---- what is said about a test ---------------------------------------------------

    private static boolean converted(ITestResult r) {
        String pkg = r.getTestClass().getRealClass().getPackage() == null ? ""
                : r.getTestClass().getRealClass().getPackage().getName();
        return pkg.contains(".imported") || pkg.contains(".classic");
    }

    private static String name(ITestResult r) {
        return r.getTestClass().getRealClass().getSimpleName() + "." + r.getName();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, String> row(ITestResult r) {
        Object[] params = r.getParameters();
        if (params != null && params.length > 0 && params[0] instanceof Map) {
            return (Map<String, String>) params[0];
        }
        return null;
    }

    private static String ids(ITestResult r, boolean withTime) {
        Map<String, String> row = row(r);
        List<String> bits = new ArrayList<>();
        if (row != null) {
            add(bits, "case", row.get("test_case_id"));
            add(bits, "xray", row.get("jira_xray_id"));
            add(bits, "story", row.get("jira_issue"));
        }
        add(bits, "env", Config.env());
        if (withTime && r.getEndMillis() > 0) {
            bits.add(String.format("%.1f s", (r.getEndMillis() - r.getStartMillis()) / 1000.0));
        }
        return String.join(" | ", bits);
    }

    private static String description(ITestResult r) {
        Map<String, String> row = row(r);
        String d = row == null ? null : row.get("description");
        return d == null || d.trim().isEmpty() ? "" : " -- " + cap(d.trim(), 160);
    }

    private static void add(List<String> bits, String label, String value) {
        if (value != null && !value.trim().isEmpty()) {
            bits.add(label + " " + value.trim());
        }
    }

    /** The first frame in the test's own class (else the first frame of a test package). */
    private static String where(Throwable t, ITestResult r) {
        String cls = r.getTestClass().getRealClass().getName();
        StackTraceElement fallback = null;
        for (StackTraceElement e : t.getStackTrace()) {
            if (e.getClassName().equals(cls) || e.getClassName().startsWith(cls + "$")) {
                return e.getFileName() + ":" + e.getLineNumber();
            }
            if (fallback == null && e.getClassName().startsWith("com.hi.api.tests")) {
                fallback = e;
            }
        }
        return fallback == null ? "" : fallback.getFileName() + ":" + fallback.getLineNumber();
    }

    private static String firstLine(String s) {
        if (s == null) {
            return "";
        }
        String line = s.trim().split("\\r?\\n", 2)[0];
        return cap(line, 400);
    }

    private static String cap(String s, int n) {
        String one = s.replaceAll("\\s*\\r?\\n\\s*", " ");
        return one.length() <= n ? one : one.substring(0, n) + " ... (" + (one.length() - n) + " more characters)";
    }
}
