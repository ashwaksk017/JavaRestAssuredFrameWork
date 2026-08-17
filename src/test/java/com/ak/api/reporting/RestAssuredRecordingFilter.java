// =============================================================================
// RestAssuredRecordingFilter
// -----------------------------------------------------------------------------
// Rest Assured Filter that records every HTTP exchange into ReportBuffer,
// which the ExtentReportListener drains at test end.
//
// Wired globally in BaseApiTest -- no per-test code needed.
// =============================================================================

package com.ak.api.reporting;

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

        ReportBuffer.add(new ReportBuffer.Exchange(
                requestSpec.getMethod(),
                requestSpec.getURI(),
                requestBody,
                response.body().asString(),
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
