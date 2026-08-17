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
import java.util.Collections;
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

    /**
     * Per-class buffer of holders, flushed to a .log file in @AfterClass.
     *
     * <p>Wrapped in {@link Collections#synchronizedList} so a Suites/*.xml
     * that flips to {@code parallel="methods"} (multiple threads sharing
     * one test-class instance, each running its own @BeforeMethod) can't
     * corrupt the list with concurrent {@code add()}. Under the current
     * {@code parallel="classes"} config, one instance = one thread, so
     * the sync wrapper has zero contention -- pure defense-in-depth for
     * a config change we'd otherwise silently mis-behave on.</p>
     *
     * <p>@AfterClass iteration is safe even without external synchronization
     * because TestNG runs @AfterClass after ALL @Before/@After Method
     * callbacks have completed for that class -- no concurrent mutation
     * during the log write.</p>
     */
    protected final List<Object> holders = Collections.synchronizedList(new ArrayList<>());

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
        // Preflight: catch placeholder / missing config BEFORE the first
        // HTTP call so the failure is ONE clear "edit program_configuration
        // .json" banner instead of N per-test UnknownHostException / 401
        // stacktraces. Non-strict: log LOUDLY and continue (so a user who
        // deliberately runs against a mock endpoint can still proceed).
        java.util.List<String> preflightIssues = Config.preflightIssues();
        if (!preflightIssues.isEmpty()) {
            System.err.println();
            System.err.println("################################################################");
            System.err.println("# CONFIG PREFLIGHT FAILED  (" + preflightIssues.size()
                    + " issue" + (preflightIssues.size() == 1 ? "" : "s") + ")");
            System.err.println("# All tests will likely fail with UnknownHostException / 401.");
            System.err.println("# Fix these in src/main/resources/program_configuration.json:");
            System.err.println("################################################################");
            for (String issue : preflightIssues) {
                System.err.println("#  * " + issue);
            }
            System.err.println("################################################################");
            System.err.println();
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

    /**
     * Tracks whether this @BeforeMethod fire is the FIRST invocation on
     * this class instance -- used to skip the inter-method cool-down for
     * the very first test so we don't add wasted wall-clock time when
     * only one test method runs. TestNG creates one instance per test
     * class, so this scopes correctly per class.
     *
     * <p>AtomicBoolean so a Suites/*.xml that flips to parallel="methods"
     * can't race two @BeforeMethod threads into both seeing `true` and
     * both skipping the cool-down. `compareAndSet(true, false)` returns
     * true exactly ONCE across all concurrent calls -- the winner skips
     * the sleep, all others sleep. Under the current parallel="classes"
     * config this is uncontended -- pure defense-in-depth. Matches the
     * `holders → synchronizedList` and translator-side `ctx →
     * synchronizedMap` pattern for other parallel-methods landmines.</p>
     */
    private final java.util.concurrent.atomic.AtomicBoolean firstTestOnThisInstance =
            new java.util.concurrent.atomic.AtomicBoolean(true);

    @BeforeMethod(alwaysRun = true)
    public void newTestHolder() {
        // ReadyAPI-style test isolation cool-down:
        //   ReadyAPI users typically run test cases INTERACTIVELY (clicking
        //   Run on one case at a time) or via a suite runner with larger
        //   inter-test overhead -- effectively pacing them. Our TestNG
        //   runner fires @Test methods back-to-back in the SAME JVM +
        //   SAME class instance, sometimes with only ~30ms between them.
        //   Hilton stg (and similar shared external environments) has
        //   session-state commit lag -- a business created + activated in
        //   test N may not be fully committed on stg by the time test N+1
        //   fires its own "create business" against the SAME owner email.
        //   Symptom: the second test's POST returns 400 "Member status is
        //   invalid" while the first + fourth pass cleanly.
        //   Fix: sleep briefly before each @Test method (except the very
        //   first) to give the external env time to settle. Configurable
        //   via -Dtest.interMethodCoolDownMs (default 3000ms = 3s).
        //   Set to 0 to opt out entirely for suites that don't need it.
        // compareAndSet(true, false) returns true EXACTLY ONCE across
        // concurrent @BeforeMethod calls on the same instance -- so the
        // "first" test skips the cool-down and all subsequent tests
        // (including retries + parallel="methods" siblings) sleep.
        boolean wasFirst = firstTestOnThisInstance.compareAndSet(true, false);
        if (!wasFirst) {
            int coolDownMs = Config.getInt("test.interMethodCoolDownMs", 3000);
            if (coolDownMs > 0) {
                try {
                    Thread.sleep(coolDownMs);
                } catch (InterruptedException __ie) {
                    Thread.currentThread().interrupt();
                }
            }
        }

        holder = new RestLoggerUtilityDataHolder();
        softAssert = new SoftAssert();
        holder.setSoftAssertRef(softAssert);
        holders.add(holder);
        // Reset the per-thread HTTP-exchange buffer so both listeners
        // (TestCaseLogListener + ExtentReportListener) can independently
        // snapshot the same buffer without racing on drain().
        com.ak.api.reporting.ReportBuffer.reset();
        // Clear Db.NULL_FALLBACK_TRIPPED so a stale flag left by a
        // prior test on the same thread (Surefire reuses threads for
        // classes with parallel="classes") cannot falsely trip
        // unsafeSqlReason on this test's first Db call. mapSqlValues
        // also clears at its own entry -- this is belt-and-suspenders
        // for tests that hit Db directly without going through
        // mapSqlValues (rare but real: fixture-setup queries).
        com.ak.api.db.Db.clearNullFallbackFlag();
    }

    /**
     * assertAll() at the tail so soft-asserted violations surface as test
     * failures. Note: pass/fail counters are advanced by TestSuiteListener,
     * not here -- that survives tests that throw before reaching this point.
     */
    @AfterMethod(alwaysRun = true)
    public void assertAll() {
        // Null-guard: softAssert is created in @BeforeMethod; if that
        // never ran (rare -- BeforeSuite threw, a subclass @BeforeClass
        // threw, or the framework was misconfigured), assertAll would
        // NPE here and TestNG would count it as a config failure that
        // masks the ACTUAL root cause. Skip cleanly if softAssert is
        // still null.
        if (softAssert != null) {
            softAssert.assertAll();
        }
    }

    @AfterClass(alwaysRun = true)
    public void writeLogFile() {
        // FQN with dots -> underscores, so two `GetTest` classes in
        // different sub-packages (common in the ra_converter output --
        // Suite_A.GetTest and Suite_B.GetTest) don't both write to
        // `logs/GetTest.log`. Under parallel="classes" that was a
        // write-race that truncated whichever finished second.
        // Inner-class `$` also gets normalized so the filename is
        // safe across Windows + macOS + Linux without any FS-specific
        // escaping.
        String safeName = this.getClass().getName()
                .replace('.', '_')
                .replace('$', '_');
        RestUtilities.createLog(holders, LOG_DIR, safeName);
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
