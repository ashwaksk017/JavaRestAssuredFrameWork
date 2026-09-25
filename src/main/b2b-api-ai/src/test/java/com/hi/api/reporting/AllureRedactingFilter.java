// =============================================================================
// AllureRedactingFilter
// -----------------------------------------------------------------------------
// Drop-in replacement for io.qameta.allure.restassured.AllureRestAssured.
//
// AllureRestAssured attaches the request and response VERBATIM -- there is no
// content hook on it -- so a token-request body lands in
// target/allure-results/ (and from there in the published Allure HTML) with
// client_secret and password in clear text. That is the same leak the Extent
// report had.
//
// This filter produces the same two attachments ("Request" / "Response") with
// identical intent, but every field passes through Secrets.redact first.
// =============================================================================

package com.hi.api.reporting;

import com.hi.api.security.Secrets;

import io.qameta.allure.Allure;
import io.restassured.filter.Filter;
import io.restassured.filter.FilterContext;
import io.restassured.http.Header;
import io.restassured.response.Response;
import io.restassured.specification.FilterableRequestSpecification;
import io.restassured.specification.FilterableResponseSpecification;

public class AllureRedactingFilter implements Filter {

    private static final String TEXT = "text/plain";

    @Override
    public Response filter(FilterableRequestSpecification requestSpec,
                           FilterableResponseSpecification responseSpec,
                           FilterContext ctx) {

        // Capture the request BEFORE ctx.next -- Rest Assured may consume a
        // stream body during the exchange.
        String requestDump = renderRequest(requestSpec);

        Response response = ctx.next(requestSpec, responseSpec);

        try {
            Allure.addAttachment("Request", TEXT, requestDump, ".txt");
            Allure.addAttachment("Response", TEXT, renderResponse(response), ".txt");
        } catch (RuntimeException e) {
            // Reporting must never fail the test it is reporting on.
            System.err.println("[AllureRedactingFilter] attachment failed: " + e);
        }
        return response;
    }

    private static String renderRequest(FilterableRequestSpecification spec) {
        StringBuilder sb = new StringBuilder(512);
        try {
            sb.append(spec.getMethod()).append(' ')
              .append(Secrets.redact(spec.getURI())).append('\n');
            sb.append('\n');
            for (Header h : spec.getHeaders()) {
                sb.append(h.getName()).append(": ")
                  .append(Secrets.redactHeader(h.getName(), h.getValue()))
                  .append('\n');
            }
            if (spec.getCookies() != null && spec.getCookies().exist()) {
                sb.append("Cookie: ").append(Secrets.MASK).append('\n');
            }
            sb.append('\n').append(bodyOf(spec));
        } catch (RuntimeException e) {
            sb.append("(request not captured: ").append(e.getClass().getSimpleName()).append(')');
        }
        return sb.toString();
    }

    private static String renderResponse(Response response) {
        StringBuilder sb = new StringBuilder(512);
        try {
            sb.append(response.getStatusLine()).append('\n')
              .append("responseTime=").append(response.time()).append("ms\n\n");
            for (Header h : response.getHeaders()) {
                sb.append(h.getName()).append(": ")
                  .append(Secrets.redactHeader(h.getName(), h.getValue()))
                  .append('\n');
            }
            sb.append('\n').append(Secrets.redact(response.body().asString()));
        } catch (RuntimeException e) {
            sb.append("(response not captured: ").append(e.getClass().getSimpleName()).append(')');
        }
        return sb.toString();
    }

    /** Same defensive body extraction as RestAssuredRecordingFilter, redacted. */
    private static String bodyOf(FilterableRequestSpecification spec) {
        try {
            Object body = spec.getBody();
            if (body == null) {
                return "(no body)";
            }
            if (body instanceof String s) {
                return Secrets.redact(s);
            }
            if (body instanceof byte[] b) {
                return Secrets.redact(new String(b, java.nio.charset.StandardCharsets.UTF_8));
            }
            return Secrets.redact(body.toString());
        } catch (Exception e) {
            return "(body not captured: " + e.getClass().getSimpleName() + ")";
        }
    }
}
