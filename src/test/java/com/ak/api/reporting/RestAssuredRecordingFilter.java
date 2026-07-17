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
            if (body instanceof byte[] b) return new String(b);
            return body.toString();
        } catch (Exception e) {
            return "(body not captured: " + e.getClass().getSimpleName() + ")";
        }
    }
}
