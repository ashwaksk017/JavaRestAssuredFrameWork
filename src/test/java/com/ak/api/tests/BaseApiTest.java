// =============================================================================
// BaseApiTest  (v2 -- production hardened)
// -----------------------------------------------------------------------------
// One-time suite setup:
//   * RestAssured base URI + timeouts (from Config)
//   * RestAssured global filters:
//       - AllureRestAssured       -> Allure attachments on every call
//       - RestAssuredRecordingFilter -> ReportBuffer feed for Extent listener
//     Both fire for every request, regardless of test outcome.
//
// Per-test:
//   * new RestLoggerUtilityDataHolder + SoftAssert
//   * softAssert.assertAll() in @AfterMethod so pass/fail is decided cleanly
//
// Per-class:
//   * writes a plain-text .log file (banner-separator style from reference)
//     -- retained for backwards compatibility with reference callers
//
// Auth: if Config.authType is set, the corresponding Authorization header is
// applied globally to every request via addHeader.
// =============================================================================

package com.ak.api.tests;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

import org.testng.annotations.AfterClass;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.BeforeSuite;
import org.testng.asserts.SoftAssert;

import com.ak.api.auth.AuthUtilities;
import com.ak.api.config.Config;
import com.ak.api.reporting.RestAssuredRecordingFilter;
import com.ak.api.rest.utilities.RestLoggerUtilityDataHolder;
import com.ak.api.rest.utilities.RestUtilities;

import io.qameta.allure.restassured.AllureRestAssured;
import io.restassured.RestAssured;
import io.restassured.builder.RequestSpecBuilder;
import io.restassured.config.HttpClientConfig;
import io.restassured.config.RestAssuredConfig;
import io.restassured.http.ContentType;
import io.restassured.specification.RequestSpecification;

public abstract class BaseApiTest {

    protected static final String LOG_DIR = "logs";

    /** Per-class buffer of holders, flushed to a .log file in @AfterClass. */
    protected final List<Object> holders = new ArrayList<>();

    /** Current-test holder + softAssert -- reset in @BeforeMethod. */
    protected RestLoggerUtilityDataHolder holder;
    protected SoftAssert softAssert;

    /** Convenience for legacy string-based utilities. */
    protected String baseUrl() {
        return Config.baseUrl();
    }

    // =====================================================================
    // Suite-scoped bootstrap
    // =====================================================================

    /** Idempotency guard -- @BeforeSuite is an INSTANCE method, so TestNG
     *  fires it once per concrete subclass. Without this guard, every one
     *  of the ~22 imported test classes appended its own AllureRestAssured
     *  + RestAssuredRecordingFilter to the global RestAssured.filters()
     *  chain, and each HTTP call was then recorded N times in Extent /
     *  Allure -- reads exactly like the run is "looping". */
    private static final AtomicBoolean BOOTSTRAPPED = new AtomicBoolean(false);

    @BeforeSuite(alwaysRun = true)
    public void bootstrapRestAssured() {
        if (!BOOTSTRAPPED.compareAndSet(false, true)) {
            // Another subclass already ran the bootstrap -- global state
            // is process-wide, no reason to redo it or (worse) chain more
            // filters onto RestAssured.filters().
            return;
        }
        RestAssured.baseURI = Config.baseUrl();
        RestAssured.useRelaxedHTTPSValidation();

        RestAssured.config = RestAssuredConfig.config()
                .httpClient(HttpClientConfig.httpClientConfig()
                        .setParam("http.connection.timeout", Config.connectTimeoutMs())
                        .setParam("http.socket.timeout", Config.socketTimeoutMs()));

        RequestSpecBuilder builder = new RequestSpecBuilder()
                .setBaseUri(Config.baseUrl())
                .setContentType(ContentType.JSON);

        String authHeader = AuthUtilities.authHeaderValue();
        if (authHeader != null && !authHeader.isBlank()) {
            builder.addHeader("Authorization", authHeader);
        }

        RestAssured.requestSpecification = builder.build();

        // Global filters -- Allure attaches automatically, our filter feeds the
        // ReportBuffer that the Extent listener drains at test end.
        // replaceFiltersWith (not filters) so a re-invocation would REPLACE
        // rather than APPEND -- second layer of defense against duplication.
        RestAssured.replaceFiltersWith(
                new AllureRestAssured(),
                new RestAssuredRecordingFilter()
        );

        System.out.printf("[BaseApiTest] env=%s baseUrl=%s authType=%s "
                + "(bootstrap ran once; filters=2)%n",
                Config.env(), Config.baseUrl(), Config.authType());
    }

    // =====================================================================
    // Per-test lifecycle
    // =====================================================================

    @BeforeMethod(alwaysRun = true)
    public void newTestHolder() {
        holder = new RestLoggerUtilityDataHolder();
        softAssert = new SoftAssert();
        holder.setSoftAssertRef(softAssert);
        holders.add(holder);
        // Reset the per-thread HTTP-exchange buffer so both listeners
        // (TestCaseLogListener + ExtentReportListener) can independently
        // snapshot the same buffer without racing on drain().
        com.ak.api.reporting.ReportBuffer.reset();
    }

    /**
     * assertAll() at the tail so soft-asserted violations surface as test
     * failures. Note: pass/fail counters are advanced by TestSuiteListener,
     * not here -- that survives tests that throw before reaching this point.
     */
    @AfterMethod(alwaysRun = true)
    public void assertAll() {
        softAssert.assertAll();
    }

    @AfterClass(alwaysRun = true)
    public void writeLogFile() {
        RestUtilities.createLog(holders, LOG_DIR, this.getClass().getSimpleName());
    }

    // =====================================================================
    // Datasheet helper: parse the standard 'expected' column of a row into
    // an Expected wrapper for typed assertions. Convention: every
    // data-driven row can carry an "expected" column of the form
    //   key1:value1;key2:value2
    // For rows without the column, this returns an empty Expected so the
    // caller can still call .has() safely.
    // =====================================================================

    protected com.ak.api.data.Expected expected(java.util.Map<String, String> row) {
        return com.ak.api.data.Expected.from(row == null ? null : row.get("expected"));
    }

    // =====================================================================
    // Helpers for tests that want a pre-built spec (rather than using the
    // reference RestUtilities.getResponseXxx entry points).
    // =====================================================================

    protected RequestSpecification request() {
        return io.restassured.RestAssured.given();
    }
}
