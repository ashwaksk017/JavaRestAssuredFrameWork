// =============================================================================
// ExternalService -- helper for tests that depend on 3rd-party echo services
// -----------------------------------------------------------------------------
// httpbin.org / postman-echo and similar echo services occasionally 5xx or
// rate-limit. Those failures are not real test failures -- they mask real
// framework bugs and rot the pass/fail signal.
//
// skipIfUnavailable(response) turns any 5xx status into a TestNG SkipException,
// which shows up as SKIPPED in reports rather than FAILED. The test is still
// visible; it just doesn't count against the suite's failure count.
// =============================================================================

package com.ak.api.tests;

import java.util.function.Supplier;

import org.testng.SkipException;

import io.restassured.response.Response;

public final class ExternalService {

    private ExternalService() { }

    /**
     * Skip the test (don't fail) if the response is a 5xx -- the external
     * echo service is unavailable, which is not a framework defect.
     */
    public static void skipIfUnavailable(Response res) {
        int code = res.statusCode();
        if (code >= 500 && code < 600) {
            throw new SkipException(
                    "External echo service unavailable (HTTP " + code
                            + ") -- skipping, not failing.");
        }
    }

    /**
     * Execute an HTTP call and treat ANY exception (socket timeout, DNS
     * failure, TLS handshake, etc.) plus any 5xx response as SKIP rather
     * than FAIL. Use for tests hitting a public echo service where the
     * external outage should not count against the framework's signal.
     *
     *     Response res = ExternalService.runOrSkip(() ->
     *         RestAssured.given().get(url));
     */
    public static Response runOrSkip(Supplier<Response> call) {
        Response res;
        try {
            res = call.get();
        } catch (Exception networkError) {
            throw new SkipException(
                    "External service unreachable: " + networkError.getClass().getSimpleName()
                            + " -- " + networkError.getMessage());
        }
        skipIfUnavailable(res);
        return res;
    }
}
