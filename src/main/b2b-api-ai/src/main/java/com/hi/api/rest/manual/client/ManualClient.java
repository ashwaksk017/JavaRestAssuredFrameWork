package com.hi.api.rest.manual.client;

import java.util.Map;

import io.restassured.RestAssured;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

import com.hi.api.rest.utilities.Headers;
import com.hi.api.support.ImportedRestClient;

/**
 * Hand-written client for tests authored BEFORE -- or without -- converting a
 * ReadyAPI XML.
 *
 * <p>Generated clients live in {@code rest/clients/}, which is gitignored and
 * only exists after a convert. This one is yours: committed, shared, and never
 * overwritten (a later bootstrap or convert skips it because the file exists).</p>
 *
 * <h2>Three contracts that are easy to get wrong</h2>
 * <ul>
 *   <li>the {@code (String baseUrl)} constructor -- SharedClients builds the
 *       instance reflectively through it;</li>
 *   <li>a class name ending in {@code Client} -- SuiteName derives the suite
 *       from it, so {@code ManualClient} means the {@code manual} suite and
 *       therefore {@code templates/manual/};</li>
 *   <li>{@code implements ImportedRestClient} -- bind() rejects anything else.</li>
 * </ul>
 *
 * <p>Point your test at it by FULLY QUALIFIED name; a bare name is resolved
 * under {@code rest.clients} instead:</p>
 *
 * <pre>
 * Config.get("manual.client", "com.hi.api.rest.manual.client.ManualClient")
 * </pre>
 *
 * <p>Override only what your chain actually calls. Every other method on
 * ImportedRestClient throws {@code UnsupportedOperationException} naming
 * itself, so run the test and let it tell you what to add next.</p>
 */
public class ManualClient implements ImportedRestClient {

    private final String baseUrl;

    public ManualClient(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    /** Bearer + JSON headers, matching what a generated client sends. */
    protected Map<String, String> headers(String token) {
        return Headers.builder()
                .contentTypeJson()
                .acceptJson()
                .header("Authorization",
                        token == null || token.isEmpty() || token.startsWith("Bearer ")
                                ? token : "Bearer " + token)
                .correlationId()
                .build();
    }

    /** The base URL this client was built for. */
    protected String baseUrl() {
        return baseUrl;
    }

    // Worked example -- uncomment and adjust the path for your endpoint.
    // The imports above are already in place for it.
    //
    // @Override
    // public Response hHonorsEnroll(String token, String requestBody) {
    //     return RestAssured.given().headers(headers(token))
    //             .contentType(ContentType.JSON).body(requestBody)
    //             .post(baseUrl() + "/realms/guests/enroll");
    // }
}
