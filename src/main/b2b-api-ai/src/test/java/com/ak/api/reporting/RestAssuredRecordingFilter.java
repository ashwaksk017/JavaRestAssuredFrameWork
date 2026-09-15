// =============================================================================
// RestAssuredRecordingFilter
// -----------------------------------------------------------------------------
// Rest Assured Filter that records every HTTP exchange into ReportBuffer,
// which the ExtentReportListener drains at test end.
//
// Wired globally in BaseApiTest -- no per-test code needed.
// =============================================================================

package com.ak.api.reporting;

import com.ak.api.security.Secrets;

import io.restassured.filter.Filter;
import io.restassured.filter.FilterContext;
import io.restassured.response.Response;
import io.restassured.specification.FilterableRequestSpecification;
import io.restassured.specification.FilterableResponseSpecification;

public class RestAssuredRecordingFilter implements Filter {

    @Override
    public Response filter(FilterableRequestSpecification requestSpec,
                           FilterableResponseSpecification responseSpec,
                           FilterContext ctx) {

        String requestBody = safeStringBody(requestSpec);

        Response response = ctx.next(requestSpec, responseSpec);

        logAuthDiagnostic(requestSpec, response);

        // Redact HERE, at buffer-entry, rather than in each listener:
        // ExtentReportListener and TestCaseLogListener both read this
        // buffer, so one call covers the Extent HTML and every
        // logs/<Class>__<method>.log file. A third consumer added later
        // inherits the redaction for free.
        ReportBuffer.add(new ReportBuffer.Exchange(
                requestSpec.getMethod(),
                Secrets.redact(requestSpec.getURI()),
                Secrets.redact(requestBody),
                Secrets.redact(response.body().asString()),
                response.statusCode(),
                response.time()
        ));

        // Mid-suite token invalidation: an oauth2-configured suite
        // that receives a 401 has usually had its token revoked (SSO
        // rotation, admin action, absolute-timeout policy). Without
        // this hook, the cached token stays "valid" for its declared
        // TTL and EVERY subsequent test 401s until the cache expires
        // -- a single revocation causes a full-suite failure. Clearing
        // here forces the NEXT auth-requiring test to fetch a fresh
        // token. Safe even for negative-test-of-401 cases: the 401 is
        // already recorded above, so the current test's outcome is
        // preserved; only the next test pays for a token re-fetch.
        if (response.statusCode() == 401
                && "oauth2".equalsIgnoreCase(com.ak.api.config.Config.authType())) {
            com.ak.api.auth.AuthUtilities.invalidateOauth2Cache();
        }

        return response;
    }

    /**
     * Rest Assured stores the body as an Object -- may be a String, an
     * InputStream, or a serializable object. Try safe accessors; fall back to
     * a placeholder when we can't materialize the payload without side-effects.
     */
    /**
     * One compact line when the server rejects us, naming WHICH cause it was.
     *
     * <p>A 401 wave looks identical in a log whether the token was never
     * populated -- an upstream extract produced nothing, so an empty
     * Authorization header went out -- or the token was real and the server
     * refused it (expired, wrong audience, throttled). Those need opposite
     * fixes: the first is a converter/dataflow bug, the second is auth
     * config. Nothing in the raw log separates them without dumping headers,
     * which is both enormous and unsafe.</p>
     *
     * <p>This prints only the decisive bit: whether a bearer value was
     * present and how long it was. Never the value itself.</p>
     *
     * <p>Grep a run with {@code auth-diag} to get just these lines.</p>
     */
    private static void logAuthDiagnostic(FilterableRequestSpecification requestSpec,
                                          Response response) {
        int code = response.statusCode();
        if (code != 401 && code != 403) {
            return;
        }
        if (com.ak.api.rest.utilities.AuthDiagnostics.isExpectedStatus(code)) {
            // The step asked for this status -- a negative auth test passing,
            // not a rejected token. Counting it inflated the digest's totals.
            return;
        }
        String sent = null;
        if (requestSpec.getHeaders() != null
                && requestSpec.getHeaders().hasHeaderWithName("Authorization")) {
            sent = requestSpec.getHeaders().getValue("Authorization");
        }
        // `bucket` is the stable key the digest counts; `verdict` is the
        // human line. Kept as two values rather than regexing the length back
        // out of one -- that needed an escape sequence Java rejects.
        String bucket;
        String verdict;
        if (sent == null || sent.trim().isEmpty()) {
            bucket = "NO-TOKEN-SENT (upstream extract empty -- dataflow bug)";
            verdict = bucket;
        } else if (sent.trim().equalsIgnoreCase("Bearer")
                || sent.trim().equalsIgnoreCase("Bearer null")) {
            bucket = "BEARER-PREFIX-ONLY (token value missing after 'Bearer')";
            verdict = bucket;
        } else {
            bucket = "TOKEN-SENT-BUT-REJECTED (expired / audience / throttled)";
            verdict = bucket + " len=" + sent.trim().length();
        }
        System.out.println(" .. [auth-diag] HTTP " + code + " " + requestSpec.getMethod()
                + " " + Secrets.redact(requestSpec.getURI()) + " -- " + verdict);
        // Also fold into the digest, so the answer arrives in the same small
        // file and needs no shell pipeline to extract. The verdict is bucketed
        // without the length so counts group.
        com.ak.api.rest.utilities.AuthDiagnostics.record("header: " + bucket);
    }

    private static String safeStringBody(FilterableRequestSpecification spec) {
        try {
            Object body = spec.getBody();
            if (body == null) return "(no body)";
            if (body instanceof String s) return s;
            // Explicit UTF-8: byte-array bodies come through here for
            // binary uploads and any test that pre-serialized JSON to
            // bytes. `new String(b)` defaulted to the platform charset
            // -- non-ASCII payloads rendered as mojibake in the Extent
            // report on Windows JVMs (CP1252) while looking correct on
            // Linux CI (UTF-8). Modern APIs are UTF-8 by convention.
            if (body instanceof byte[] b) return new String(b, java.nio.charset.StandardCharsets.UTF_8);
            return body.toString();
        } catch (Exception e) {
            return "(body not captured: " + e.getClass().getSimpleName() + ")";
        }
    }
}
