// =============================================================================
// XrayClient -- Xray Cloud REST client
// -----------------------------------------------------------------------------
// Two calls, both via Rest Assured so we dogfood the framework's own HTTP
// stack:
//
//   1. POST /api/v2/authenticate  {"client_id":"...", "client_secret":"..."}
//        -> returns a JWT string (Rest Assured extracts as a body string,
//           trimmed of surrounding quotes since Xray responds with a bare
//           quoted JSON string, not an object)
//
//   2. POST /api/v2/import/execution
//        Authorization: Bearer <jwt>
//        {
//          "testExecutionKey": "PROJ-123",   // optional; new execution if absent
//          "info": { "summary": "...", "startDate": "...", "finishDate": "..." },
//          "tests": [ { "testKey": "PROJ-456", "status": "PASSED", "comment": "..." } ]
//        }
//
// Configuration (all read via Config, so -D / env-var / properties layering
// applies uniformly):
//     xray.enabled           = false           # master kill switch (default)
//     xray.baseUrl           = https://xray.cloud.getxray.app
//     xray.clientId          = (required when enabled)
//     xray.clientSecret      = (required when enabled)
//     xray.testExecutionKey  = (optional -- Xray creates a new execution if absent)
//
// Failure semantics: XrayClient NEVER throws. Any HTTP / auth failure is
// logged to stderr and swallowed -- Xray outages must not fail the local
// automation suite.
// =============================================================================

package com.ak.api.xray;

import static io.restassured.RestAssured.given;

import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.ak.api.config.Config;

import io.restassured.http.ContentType;
import io.restassured.response.Response;

public final class XrayClient {

    private static final String DEFAULT_BASE = "https://xray.cloud.getxray.app";
    private static final String AUTH_PATH    = "/api/v2/authenticate";
    private static final String IMPORT_PATH  = "/api/v2/import/execution";

    private final String baseUrl;
    private final String clientId;
    private final String clientSecret;
    private final String testExecutionKey;
    private final boolean enabled;

    public XrayClient() {
        this.enabled          = "true".equalsIgnoreCase(Config.get("xray.enabled", "false"));
        this.baseUrl          = Config.get("xray.baseUrl", DEFAULT_BASE);
        this.clientId         = Config.get("xray.clientId", null);
        this.clientSecret     = Config.get("xray.clientSecret", null);
        this.testExecutionKey = Config.get("xray.testExecutionKey", null);
    }

    /** True only when the master switch is on AND both credentials are present. */
    public boolean isEnabled() {
        return enabled
                && clientId != null && !clientId.isBlank()
                && clientSecret != null && !clientSecret.isBlank();
    }

    /**
     * Import a batch of results in a single POST. Silently no-ops when
     * disabled or misconfigured. Returns true if the import returned 2xx.
     */
    public boolean importResults(List<XrayResult> results, Instant startedAt, Instant finishedAt) {
        if (!isEnabled()) {
            log("skipped -- xray.enabled=false or missing clientId/clientSecret");
            return false;
        }
        if (results == null || results.isEmpty()) {
            log("skipped -- no results with a jira_xray_id to import");
            return false;
        }

        String jwt;
        try {
            jwt = authenticate();
        } catch (RuntimeException authErr) {
            log("auth failed: " + authErr.getMessage());
            return false;
        }
        if (jwt == null || jwt.isBlank()) {
            log("auth failed: empty JWT");
            return false;
        }

        Map<String, Object> body = buildImportBody(results, startedAt, finishedAt);
        try {
            Response res = given()
                    .relaxedHTTPSValidation()
                    .header("Authorization", "Bearer " + jwt)
                    .contentType(ContentType.JSON)
                    .body(body)
                    .when()
                    .post(baseUrl + IMPORT_PATH);
            int code = res.statusCode();
            log("imported " + results.size() + " result(s) -> HTTP " + code
                    + (code >= 200 && code < 300 ? " OK" : " body=" + res.body().asString()));
            return code >= 200 && code < 300;
        } catch (RuntimeException importErr) {
            log("import call failed: " + importErr.getMessage());
            return false;
        }
    }

    // ---- internals ----

    private String authenticate() {
        Map<String, String> credBody = new LinkedHashMap<>();
        credBody.put("client_id", clientId);
        credBody.put("client_secret", clientSecret);

        Response res = given()
                .relaxedHTTPSValidation()
                .contentType(ContentType.JSON)
                .body(credBody)
                .when()
                .post(baseUrl + AUTH_PATH);

        int code = res.statusCode();
        if (code < 200 || code >= 300) {
            throw new RuntimeException("HTTP " + code + " on " + AUTH_PATH
                    + " body=" + res.body().asString());
        }
        // Xray returns the JWT as a bare quoted JSON string: "eyJ..."
        String raw = res.body().asString();
        return trimSurroundingQuotes(raw);
    }

    private static String trimSurroundingQuotes(String s) {
        if (s == null) return null;
        String t = s.trim();
        if (t.length() >= 2 && t.startsWith("\"") && t.endsWith("\"")) {
            return t.substring(1, t.length() - 1);
        }
        return t;
    }

    private Map<String, Object> buildImportBody(List<XrayResult> results,
                                                Instant startedAt, Instant finishedAt) {
        Map<String, Object> body = new LinkedHashMap<>();
        if (testExecutionKey != null && !testExecutionKey.isBlank()) {
            body.put("testExecutionKey", testExecutionKey.trim());
        }

        Map<String, Object> info = new LinkedHashMap<>();
        info.put("summary", "api-automation-restassured -- automated run");
        info.put("startDate",  DateTimeFormatter.ISO_INSTANT.format(startedAt));
        info.put("finishDate", DateTimeFormatter.ISO_INSTANT.format(finishedAt));
        body.put("info", info);

        List<Map<String, Object>> tests = new ArrayList<>(results.size());
        for (XrayResult r : results) {
            Map<String, Object> t = new LinkedHashMap<>();
            t.put("testKey", r.testKey());
            t.put("status", r.status().name());
            if (r.comment() != null && !r.comment().isBlank()) {
                t.put("comment", truncate(r.comment(), 2000));
            }
            tests.add(t);
        }
        body.put("tests", tests);
        return body;
    }

    private static String truncate(String s, int max) {
        if (s == null || s.length() <= max) return s;
        return s.substring(0, max) + "...(truncated)";
    }

    private static void log(String msg) {
        System.out.println("[XrayClient] " + msg);
    }
}
