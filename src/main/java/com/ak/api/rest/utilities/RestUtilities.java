// =============================================================================
// RestUtilities  (v2 -- production-hardened)
// -----------------------------------------------------------------------------
// Ported from com.visa.b2b.connect.rest.utilities.RestUtilities. Method
// signatures preserved so calling code migrates 1:1.
//
// Fixes vs the reference:
//   P0.1  removed the static PrintWriter -- createLog now opens/closes its own
//         writer per call, so parallel classes no longer race
//   P0.2  mapJsonValues rewritten with regex + strict-mode. Unresolved
//         placeholders no longer slip through as false / 0 silently
//   P0.3  getRequestTemplate null-checks the classpath resource and throws
//         with the actual path we tried
//   P0.4  getResponseGet 2-arg overload (no body); 3-arg preserved for compat
//   P1.10 assertResponseTimeBelow() -- ties into per-endpoint SLOs
//   Also: getResponsePut, getResponseDelete, POJO deserialization helpers
//
// Placeholder semantics (unchanged):
//   #key#          -> value from dataMap, else null
//   "%key%"        -> value from dataMap, else false
//   "@key@"        -> value from dataMap, else 0
//
// Strict mode: pass strict=true and mapJsonValues throws
// UnresolvedPlaceholderException listing every #k#, %k%, @k@ that wasn't
// resolvable. Recommended for real suites.
// =============================================================================

package com.ak.api.rest.utilities;

import static io.restassured.RestAssured.given;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.io.Reader;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;

import io.restassured.http.ContentType;
import io.restassured.path.json.JsonPath;
import io.restassured.response.Response;
import io.restassured.specification.RequestSpecification;

public class RestUtilities {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper()
            // Real APIs add fields over time -- don't fail deserialization when
            // the server sends back more than the POJO knows about.
            .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);

    // -------- Placeholder regexes (P0.2 rewrite) --------
    private static final Pattern P_HASH   = Pattern.compile("#([A-Za-z0-9_.-]+)#");
    private static final Pattern P_PCT_Q  = Pattern.compile("\"%([A-Za-z0-9_.-]+)%\"");
    private static final Pattern P_AT_Q   = Pattern.compile("\"@([A-Za-z0-9_.-]+)@\"");

    // -------- Counters (P2.18: suite-level via listener; kept here for callers) --------
    private static AtomicInteger failed = new AtomicInteger(0);
    private static AtomicInteger passed = new AtomicInteger(0);

    public static int totalTestCases() {
        return failed.get() + passed.get();
    }

    public static AtomicInteger getFailed() {
        return failed;
    }

    public static AtomicInteger getPassed() {
        return passed;
    }

    public static void reset() {
        failed.set(0);
        passed.set(0);
    }

    // =====================================================================
    // HTTP verbs
    // =====================================================================

    public static Response getResponsePost(String body, String url, Map<String, String> headers) {
        return applyHeaders(baseRequest(), headers).body(body).when().post(url);
    }

    public static Response getResponsePatch(String body, String url, Map<String, String> headers) {
        return applyHeaders(baseRequest(), headers).body(body).when().patch(url);
    }

    public static Response getResponsePut(String body, String url, Map<String, String> headers) {
        return applyHeaders(baseRequest(), headers).body(body).when().put(url);
    }

    /** DELETE without a body -- most common. */
    public static Response getResponseDelete(String url, Map<String, String> headers) {
        return applyHeaders(baseRequest(), headers).when().delete(url);
    }

    /** GET (2-arg, P0.4 overload). */
    public static Response getResponseGet(String url, Map<String, String> headers) {
        return applyHeaders(baseRequest(), headers).when().get(url);
    }

    /** GET (3-arg, reference-compat). Body attached only if non-empty. */
    public static Response getResponseGet(String body, String url, Map<String, String> headers) {
        RequestSpecification req = applyHeaders(baseRequest(), headers);
        if (body != null && !body.isEmpty()) {
            req = req.body(body);
        }
        return req.when().get(url);
    }

    // Preserved as a no-op for reference compat.
    public static String setHeaders(String headers) {
        return "";
    }

    // -------- Common request builders --------

    private static RequestSpecification baseRequest() {
        return given().relaxedHTTPSValidation().contentType(ContentType.JSON);
    }

    private static RequestSpecification applyHeaders(RequestSpecification req, Map<String, String> headers) {
        if (headers != null) {
            for (Map.Entry<String, String> e : headers.entrySet()) {
                req = req.header(e.getKey(), e.getValue());
            }
        }
        return req;
    }

    // =====================================================================
    // Placeholder substitution (P0.2 rewrite -- regex + strict mode)
    // =====================================================================

    /** Non-strict: unresolved placeholders default to null / false / 0 (reference behavior). */
    public static String mapJsonValues(String schema, Map<String, String> dataMap) throws Exception {
        return mapJsonValues(schema, dataMap, /* strict = */ false);
    }

    /**
     * @param strict when true, throws UnresolvedPlaceholderException on any unresolved
     *               placeholder (recommended). When false, mimics the reference's fallback
     *               behavior: #x# -> null, %x% -> false, @x@ -> 0.
     */
    public static String mapJsonValues(String schema, Map<String, String> dataMap, boolean strict) throws Exception {
        if (dataMap == null) dataMap = Map.of();

        List<String> unresolved = new ArrayList<>();

        // #hash# lands inside "..." in the template, so any raw value the caller
        // supplies must be JSON-string-escaped (quotes, backslashes, newlines,
        // control chars) or the resulting payload is invalid JSON. The %pct%
        // and @at@ variants replace the entire "..."-token and expect scalar
        // literals (true/false/0.5/42), so they pass through untouched.
        schema = substitute(schema, P_HASH,  dataMap, strict ? null : "null",  unresolved, /* jsonEscape = */ true);
        schema = substitute(schema, P_PCT_Q, dataMap, strict ? null : "false", unresolved, /* jsonEscape = */ false);
        schema = substitute(schema, P_AT_Q,  dataMap, strict ? null : "0",     unresolved, /* jsonEscape = */ false);

        if (!unresolved.isEmpty()) {
            throw new UnresolvedPlaceholderException(
                    "Unresolved placeholders: " + new HashSet<>(unresolved));
        }
        return schema;
    }

    public static String mapJsonValues(Reader reader, Map<String, String> dataMap) throws Exception {
        return mapJsonValues(readAll(reader), dataMap, false);
    }

    public static String mapJsonValues(Reader reader, Map<String, String> dataMap, boolean strict) throws Exception {
        return mapJsonValues(readAll(reader), dataMap, strict);
    }

    private static String substitute(String schema, Pattern pattern, Map<String, String> dataMap,
                                     String fallback, List<String> unresolvedSink,
                                     boolean jsonEscape) {
        Matcher m = pattern.matcher(schema);
        StringBuilder out = new StringBuilder();
        while (m.find()) {
            String key = m.group(1);
            String value = dataMap.get(key);
            if (value == null) {
                if (fallback == null) {
                    unresolvedSink.add(m.group());
                    m.appendReplacement(out, Matcher.quoteReplacement(m.group()));
                } else {
                    m.appendReplacement(out, Matcher.quoteReplacement(fallback));
                }
            } else {
                String toInsert = jsonEscape ? jsonEscapeInner(value) : value;
                m.appendReplacement(out, Matcher.quoteReplacement(toInsert));
            }
        }
        m.appendTail(out);
        return out.toString();
    }

    /**
     * JSON-string-escape {@code raw} so it is safe to drop verbatim between
     * literal "..." in a JSON template. Handles quotes, backslashes, newlines,
     * tabs, and control chars via Jackson.
     */
    private static String jsonEscapeInner(String raw) {
        if (raw == null || raw.isEmpty()) return raw;
        try {
            String quoted = OBJECT_MAPPER.writeValueAsString(raw); // wraps in "..." + escapes
            return quoted.substring(1, quoted.length() - 1);
        } catch (IOException e) {
            throw new IllegalStateException("JSON escape failed: " + e.getMessage(), e);
        }
    }

    private static String readAll(Reader reader) throws IOException {
        StringBuilder buf = new StringBuilder();
        try (BufferedReader br = new BufferedReader(reader)) {
            String line;
            while ((line = br.readLine()) != null) {
                buf.append(line).append('\n');
            }
        }
        return buf.toString();
    }

    /** Distinct exception type so callers can catch precisely. */
    public static class UnresolvedPlaceholderException extends Exception {
        public UnresolvedPlaceholderException(String message) {
            super(message);
        }
    }

    // =====================================================================
    // Response helpers  (P1.10 adds response-time assertions)
    // =====================================================================

    public static String checkActualResponse(Response response) {
        return response.getStatusCode() + "";
    }

    public static String getResponseAsString(Response response) {
        return response.body().asString();
    }

    public static boolean containsStringPattern(String pattern, Response response) {
        return response.body().asString().contains(pattern);
    }

    /** Assert response time ceiling. Throws AssertionError with a clear message. */
    public static void assertResponseTimeBelow(Response response, long ceilingMillis) {
        long actual = response.time();
        if (actual > ceilingMillis) {
            throw new AssertionError(
                    "Response time SLO breach: expected <= " + ceilingMillis
                            + "ms, actual " + actual + "ms");
        }
    }

    // -------- POJO (Jackson) --------

    public static <T> T as(Response response, Class<T> type) {
        try {
            return OBJECT_MAPPER.readValue(response.body().asString(), type);
        } catch (IOException e) {
            throw new IllegalStateException(
                    "Failed to deserialize response into " + type.getSimpleName() + ": " + e.getMessage(), e);
        }
    }

    public static String toJson(Object payload) {
        try {
            return OBJECT_MAPPER.writeValueAsString(payload);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to serialize object to JSON: " + e.getMessage(), e);
        }
    }

    // =====================================================================
    // Logging helpers
    // =====================================================================

    public static void logRequestBody(String testCaseId, RestLoggerUtilityDataHolder rpdhl, String payload) {
        String pretty;
        try {
            pretty = new JsonPath(payload).prettify();
        } catch (Exception e) {
            pretty = payload; // non-JSON payloads pass through as-is
        }
        appendJsonLog(testCaseId, rpdhl, pretty, "Request");
    }

    public static void logResponseBody(String testCaseId, RestLoggerUtilityDataHolder rpdhl, String payload) {
        appendJsonLog(testCaseId, rpdhl, payload, "Response ");
    }

    public static void appendJsonLog(String testCaseId, RestLoggerUtilityDataHolder rpdhl, String payload, String type) {
        rpdhl.appendJsonLog("********************* Test Case " + testCaseId + " " + type + " *********************");
        rpdhl.appendJsonLog(System.lineSeparator());
        rpdhl.appendJsonLog(payload);
        rpdhl.appendJsonLog(System.lineSeparator());
        rpdhl.appendJsonLog("********************* Test Case " + testCaseId + " " + type + " *********************");
        rpdhl.appendJsonLog(System.lineSeparator());
    }

    // =====================================================================
    // Template loader  (P0.3 null-check + friendly error)
    // =====================================================================

    public static InputStreamReader getRequestTemplate(String path, String fileName) {
        return getRequestTemplate(path + fileName);
    }

    /**
     * Single-arg overload: load a template from a full classpath-relative
     * path (e.g. {@code "templates/<suite>/<subdir>/<file>.json"}).
     * ra_converter-emitted tests call this via the generated
     * {@code Templates.<NAME>} constants so the file path never appears
     * as a literal string in a test method body.
     */
    public static InputStreamReader getRequestTemplate(String resource) {
        InputStream in = RestUtilities.class.getClassLoader().getResourceAsStream(resource);
        if (in == null) {
            throw new IllegalArgumentException(
                    "Template not found on classpath: " + resource
                            + "  (looked under src/main/resources/" + resource + ")");
        }
        return new InputStreamReader(in);
    }

    // =====================================================================
    // Log-file writing  (P0.1 -- no static writer state; safe for parallel classes)
    // =====================================================================

    public static void createLog(List<Object> rpdlist, String reportDir, String fileName) {
        File dir = new File(reportDir);
        if (!dir.exists() && !dir.mkdirs()) {
            throw new IllegalStateException("Could not create report dir: " + reportDir);
        }
        File out = Paths.get(reportDir, fileName + ".log").toFile();
        if (out.exists() && !out.delete()) {
            // Non-fatal -- most likely locked by an open editor. Continue and append.
        }
        try (PrintWriter writer = new PrintWriter(new BufferedWriter(new FileWriter(out)))) {
            for (Object h : rpdlist) {
                writer.println(((RestLoggerUtilityDataHolder) h).getJsonLog());
            }
        } catch (IOException e) {
            throw new IllegalStateException("Failed to write log " + out, e);
        }
    }
}
