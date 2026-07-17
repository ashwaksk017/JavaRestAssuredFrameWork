// =============================================================================
// TestCaseLogListener -- one .log file per test method, overwritten each run
// -----------------------------------------------------------------------------
// Produces `logs/<ClassName>__<methodName>.log` on every test completion
// (pass, fail, or skip). Contents:
//   - Header:  class, method, status, timestamp, duration, groups
//   - For each HTTP exchange captured by RestAssuredRecordingFilter:
//       -> METHOD URL   (status, response time)
//       -> REQUEST BODY (pretty-printed if JSON)
//       -> RESPONSE BODY (pretty-printed if JSON)
//   - Footer: assertion summary if the test failed
//
// The file is opened with truncate semantics -- the second run of the same
// test overwrites the first, so `logs/` always reflects the LATEST run.
//
// Registration order matters: this listener must come BEFORE
// ExtentReportListener in testng.xml, because it reads via ReportBuffer.snapshot()
// (non-destructive) so Extent can still call drain() after and clear the buffer.
// =============================================================================

package com.ak.api.reporting;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.List;

import org.testng.ITestContext;
import org.testng.ITestListener;
import org.testng.ITestResult;

import io.restassured.path.json.JsonPath;

public final class TestCaseLogListener implements ITestListener {

    private static final String LOG_DIR = "logs";
    private static final DateTimeFormatter TS =
            DateTimeFormatter.ISO_LOCAL_DATE_TIME.withZone(ZoneId.systemDefault());
    private static final String DIVIDER = "=".repeat(80);
    private static final String SUB     = "-".repeat(80);

    @Override
    public void onTestSuccess(ITestResult result) { write(result, "PASSED"); }

    @Override
    public void onTestFailure(ITestResult result) { write(result, "FAILED"); }

    @Override
    public void onTestSkipped(ITestResult result) { write(result, "SKIPPED"); }

    // -----------------------------------------------------------------------

    private void write(ITestResult result, String status) {
        String className  = result.getTestClass().getRealClass().getSimpleName();
        String methodName = result.getMethod().getMethodName();
        Path out = Paths.get(LOG_DIR, className + "__" + methodName + ".log");

        try {
            Files.createDirectories(out.getParent());
        } catch (IOException e) {
            throw new UncheckedIOException("Could not create log dir " + out.getParent(), e);
        }

        // Truncate + write -- overwrite on every run.
        try (BufferedWriter w = Files.newBufferedWriter(out, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING)) {

            writeHeader(w, result, className, methodName, status);
            writeExchanges(w);
            writeFooter(w, result, status);

        } catch (IOException e) {
            throw new UncheckedIOException("Failed to write test-case log " + out, e);
        }
    }

    private static void writeHeader(BufferedWriter w, ITestResult r, String cls, String method,
                                    String status) throws IOException {
        w.write(DIVIDER); w.newLine();
        w.write("TEST      : " + cls + "." + method); w.newLine();
        w.write("STATUS    : " + status); w.newLine();
        w.write("STARTED   : " + TS.format(Instant.ofEpochMilli(r.getStartMillis()))); w.newLine();
        w.write("DURATION  : " + (r.getEndMillis() - r.getStartMillis()) + " ms"); w.newLine();
        String[] groups = r.getMethod().getGroups();
        if (groups != null && groups.length > 0) {
            w.write("GROUPS    : " + String.join(", ", groups)); w.newLine();
        }
        w.write(DIVIDER); w.newLine();
        w.newLine();
    }

    private static void writeExchanges(BufferedWriter w) throws IOException {
        List<ReportBuffer.Exchange> exchanges = ReportBuffer.snapshot();
        if (exchanges.isEmpty()) {
            w.write("(no HTTP exchanges recorded for this test)"); w.newLine();
            return;
        }
        int i = 1;
        for (ReportBuffer.Exchange ex : exchanges) {
            w.write("[" + i + "] " + ex.method + " " + ex.url); w.newLine();
            w.write("     status=" + ex.statusCode + "  responseTime=" + ex.responseTimeMs + "ms");
            w.newLine();
            w.newLine();

            w.write(SUB); w.newLine();
            w.write("REQUEST BODY"); w.newLine();
            w.write(SUB); w.newLine();
            w.write(prettyIfJson(ex.requestBody)); w.newLine();
            w.newLine();

            w.write(SUB); w.newLine();
            w.write("RESPONSE BODY"); w.newLine();
            w.write(SUB); w.newLine();
            w.write(prettyIfJson(ex.responseBody)); w.newLine();
            w.newLine();
            w.write(DIVIDER); w.newLine();
            w.newLine();
            i++;
        }
    }

    private static void writeFooter(BufferedWriter w, ITestResult r, String status) throws IOException {
        if ("FAILED".equals(status) && r.getThrowable() != null) {
            w.write("FAILURE"); w.newLine();
            w.write(SUB); w.newLine();
            w.write(r.getThrowable().toString()); w.newLine();
        } else if ("SKIPPED".equals(status) && r.getThrowable() != null) {
            w.write("SKIP REASON"); w.newLine();
            w.write(SUB); w.newLine();
            w.write(r.getThrowable().getMessage() == null
                    ? r.getThrowable().toString()
                    : r.getThrowable().getMessage());
            w.newLine();
        }
    }

    private static String prettyIfJson(String s) {
        if (s == null || s.isBlank()) return "(empty)";
        try {
            return new JsonPath(s).prettify();
        } catch (Exception ignore) {
            return s;
        }
    }

    // ITestContext hooks unused -- keep no-op defaults from interface.
    @Override public void onStart(ITestContext ctx)  { /* no-op */ }
    @Override public void onFinish(ITestContext ctx) { /* no-op */ }
    @Override public void onTestStart(ITestResult r) { /* no-op */ }
}
