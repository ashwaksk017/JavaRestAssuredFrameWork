// =============================================================================
// ExtentReportListener -- ExtentReports 5.x TestNG listener
// -----------------------------------------------------------------------------
// Writes an HTML report to extent-reports/<timestamp>-Extent.html.
// Report is self-contained -- no CLI required, open the HTML in a browser.
//
// Each test node shows every HTTP exchange the test performed (method, URL,
// status, response time) plus the raw request + response bodies as JSON code
// blocks. Attached for BOTH passed and failed tests -- so a stakeholder can
// see what actually went over the wire even for green cases.
//
// Wire in testng.xml:
//   <listeners>
//     <listener class-name="com.ak.api.reporting.ExtentReportListener"/>
//   </listeners>
//
// Toggle via Config -- report.extent.enabled=false to skip.
// =============================================================================

package com.ak.api.reporting;

import java.io.File;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

import org.testng.ISuite;
import org.testng.ISuiteListener;
import org.testng.ITestContext;
import org.testng.ITestListener;
import org.testng.ITestResult;

import com.ak.api.config.Config;
import com.aventstack.extentreports.ExtentReports;
import com.aventstack.extentreports.ExtentTest;
import com.aventstack.extentreports.Status;
import com.aventstack.extentreports.markuputils.CodeLanguage;
import com.aventstack.extentreports.markuputils.MarkupHelper;
import com.aventstack.extentreports.reporter.ExtentSparkReporter;
import com.aventstack.extentreports.reporter.configuration.Theme;

public class ExtentReportListener implements ITestListener, ISuiteListener {

    private static final String REPORT_DIR = "extent-reports";
    private static ExtentReports extent;

    private static final ThreadLocal<ExtentTest> currentTest = new ThreadLocal<>();

    @Override
    public void onStart(ISuite suite) {
        if (!Config.extentEnabled()) return;

        File dir = new File(REPORT_DIR);
        if (!dir.exists()) dir.mkdirs();

        String ts = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss"));
        String path = REPORT_DIR + File.separator + ts + "-Extent.html";

        ExtentSparkReporter spark = new ExtentSparkReporter(path);
        spark.config().setTheme(Theme.DARK);
        spark.config().setDocumentTitle("API Automation Report");
        spark.config().setReportName("Rest Assured + TestNG");

        extent = new ExtentReports();
        extent.attachReporter(spark);
        extent.setSystemInfo("env", Config.env());
        extent.setSystemInfo("baseUrl", Config.baseUrl());
        extent.setSystemInfo("authType", Config.authType());
        extent.setSystemInfo("suite", suite.getName());
    }

    @Override
    public void onStart(ITestContext context) {
        // no-op
    }

    @Override
    public void onTestStart(ITestResult result) {
        if (extent == null) return;
        ExtentTest test = extent.createTest(
                result.getTestClass().getRealClass().getSimpleName() + "." + result.getMethod().getMethodName(),
                result.getMethod().getDescription());
        for (String group : result.getMethod().getGroups()) {
            test.assignCategory(group);
        }
        currentTest.set(test);
    }

    @Override
    public void onTestSuccess(ITestResult result) {
        ExtentTest t = currentTest.get();
        if (t == null) return;
        renderExchanges(t);
        t.pass("Passed");
    }

    @Override
    public void onTestFailure(ITestResult result) {
        ExtentTest t = currentTest.get();
        if (t == null) return;
        renderExchanges(t);
        t.fail("Failed: " + safeMessage(result.getThrowable()));
        if (result.getThrowable() != null) {
            t.fail(result.getThrowable());
        }
    }

    @Override
    public void onTestSkipped(ITestResult result) {
        ExtentTest t = currentTest.get();
        if (t == null) return;
        renderExchanges(t);
        t.skip("Skipped: " + safeMessage(result.getThrowable()));
    }

    @Override
    public void onFinish(ITestContext context) {
        // no-op
    }

    @Override
    public void onFinish(ISuite suite) {
        if (extent != null) {
            extent.flush();
        }
    }

    // ---------------------------------------------------------------------
    // Render every HTTP exchange (drained from ReportBuffer) into the current
    // ExtentTest node as method/URL summary + request/response JSON code blocks.
    // Called for pass, fail, AND skip so the artifacts are always visible.
    // ---------------------------------------------------------------------
    private void renderExchanges(ExtentTest node) {
        // snapshot() instead of drain() so multiple listeners can independently
        // read the same exchanges; BaseApiTest.@BeforeMethod resets the buffer
        // at the start of every test.
        List<ReportBuffer.Exchange> exchanges = ReportBuffer.snapshot();
        int idx = 0;
        for (ReportBuffer.Exchange ex : exchanges) {
            idx++;
            String header = String.format("[%d] %s %s -- HTTP %d in %dms",
                    idx, ex.method, ex.url, ex.statusCode, ex.responseTimeMs);
            node.log(Status.INFO, header);

            String reqBody = ex.requestBody == null || ex.requestBody.isBlank()
                    ? "(no body)" : ex.requestBody;
            node.log(Status.INFO,
                    MarkupHelper.createLabel("REQUEST BODY", com.aventstack.extentreports.markuputils.ExtentColor.BLUE));
            node.log(Status.INFO, MarkupHelper.createCodeBlock(reqBody, CodeLanguage.JSON));

            String resBody = ex.responseBody == null || ex.responseBody.isBlank()
                    ? "(no body)" : ex.responseBody;
            node.log(Status.INFO,
                    MarkupHelper.createLabel("RESPONSE BODY", com.aventstack.extentreports.markuputils.ExtentColor.GREEN));
            node.log(Status.INFO, MarkupHelper.createCodeBlock(resBody, CodeLanguage.JSON));
        }
    }

    private static String safeMessage(Throwable t) {
        return t == null ? "(no message)" : (t.getMessage() == null ? t.toString() : t.getMessage());
    }
}
