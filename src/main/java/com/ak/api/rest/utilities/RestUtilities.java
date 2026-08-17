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

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class RestUtilities {

    private static final Logger LOG = LoggerFactory.getLogger(RestUtilities.class);

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper()
            // Real APIs add fields over time -- don't fail deserialization when
            // the server sends back more than the POJO knows about.
            .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);

    // -------- Placeholder regexes (P0.2 rewrite) --------
    private static final Pattern P_HASH   = Pattern.compile("#([A-Za-z0-9_.-]+)#");
    private static final Pattern P_PCT_Q  = Pattern.compile("\"%([A-Za-z0-9_.-]+)%\"");
    private static final Pattern P_AT_Q   = Pattern.compile("\"@([A-Za-z0-9_.-]+)@\"");
    // Bare `@X@` (no surrounding double-quotes) covers SQL / URL /
    // plain-text contexts where the ref is single-quoted (`'@X@'` in
    // SQL WHERE clauses) or unquoted (URL path segments). Runs AFTER
    // P_AT_Q so JSON scalar substitution still takes precedence when
    // the ref is inside a double-quoted string (`"@42@"` -> `42`,
    // not `"42"`). Symptom without this: SQL like
    //   UPDATE account SET status='L' WHERE account_id='@Properties_accountID@'
    // hits driver [22P02] `invalid input syntax for type bigint`
    // because `@Properties_accountID@` stayed literal.
    //
    // Boundary anchors: `(?<![A-Za-z0-9])` before + `(?![A-Za-z0-9])`
    // after ensure the ref sits at an identifier boundary. Prior naive
    // pattern greedy-matched `@id@` inside `guest@id@partner` (unusual
    // but possible in email chains / composite keys) and silently
    // substituted → fallback `"0"` planted in the output. The anchors
    // don't break the intended usage: SQL `'@X@'` has `'` (non-alnum)
    // on both sides; URLs `/foo/@X@/bar` have `/`. Whitespace, punctuation,
    // and start/end of string all pass the negative-lookahead check.
    private static final Pattern P_AT_BARE = Pattern.compile(
            "(?<![A-Za-z0-9])@([A-Za-z0-9_.-]+)@(?![A-Za-z0-9])");

    // -------- Counters (P2.18: suite-level via listener; kept here for callers) --------
    private static AtomicInteger failed = new AtomicInteger(0);
    private static AtomicInteger passed = new AtomicInteger(0);

    // Thread-local record of the # of unresolved placeholders left after
    // the most recent mapJsonValues call on THIS thread. Used by
    // mapSqlValues to flag Db.NULL_FALLBACK_TRIPPED so unsafeSqlReason
    // can distinguish `WHERE X='null'` from a real fallback vs a
    // legitimate literal against a varchar audit column. ThreadLocal so
    // parallel="classes" runs don't cross-contaminate.
    private static final ThreadLocal<Integer> LAST_UNRESOLVED_COUNT =
            ThreadLocal.withInitial(() -> 0);

    /** Framework-internal: last mapJsonValues unresolved-placeholder count on this thread. */
    public static int lastUnresolvedCount() {
        return LAST_UNRESOLVED_COUNT.get();
    }

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
        return mapJsonValues(schema, dataMap, /* strict = */ false, /* jsonEscape = */ true);
    }

    /**
     * @param strict when true, throws UnresolvedPlaceholderException on any unresolved
     *               placeholder (recommended). When false, mimics the reference's fallback
     *               behavior: #x# -> null, %x% -> false, @x@ -> 0.
     */
    public static String mapJsonValues(String schema, Map<String, String> dataMap, boolean strict) throws Exception {
        return mapJsonValues(schema, dataMap, strict, /* jsonEscape = */ true);
    }

    /**
     * SQL / URL / plain-text callers pass {@code jsonEscape = false} so a
     * substituted value containing {@code "} or {@code \} isn't JSON-escaped
     * (which would land as {@code \"} / {@code \\} in the SQL literal and
     * blow up the DB driver's syntax check). JSON body callers keep the
     * default (true).
     *
     * @param strict when true, throws UnresolvedPlaceholderException on any unresolved
     *               placeholder (recommended). When false, mimics the reference's fallback
     *               behavior: #x# -> null, %x% -> false, @x@ -> 0.
     * @param jsonEscape when true, substituted values are JSON-string-escaped
     *               (correct for `"#X#"` inside a JSON body). When false,
     *               values pass through verbatim (correct for SQL / URL /
     *               shell / query-string contexts where JSON escaping would
     *               corrupt the output).
     */
    public static String mapJsonValues(String schema, Map<String, String> dataMap,
                                        boolean strict, boolean jsonEscape) throws Exception {
        if (dataMap == null) dataMap = Map.of();

        List<String> unresolved = new ArrayList<>();

        // #hash# lands inside "..." in the template, so any raw value the caller
        // supplies must be JSON-string-escaped (quotes, backslashes, newlines,
        // control chars) or the resulting payload is invalid JSON. The %pct%
        // and @at@ variants replace the entire "..."-token and expect scalar
        // literals (true/false/0.5/42), so they pass through untouched.
        //
        // Iterate until the schema is STABLE: the ra_converter's CSV
        // sometimes stores nested placeholder values (e.g. a
        // `tpl_username` cell that literally holds `#Properties_username#`,
        // which in turn resolves from another CSV column). Without
        // recursion, the outer placeholder gets replaced with the inner
        // placeholder verbatim and Hilton's API sees `"username":
        // "#Properties_username#"` and rejects with a regex validation
        // failure. Bounded at 8 iterations to prevent runaway self-refs.
        //
        // Loop body always passes null as `fallback` so unresolved
        // placeholders stay literal -- lets a subsequent iteration
        // resolve them if a resolved value itself contained a placeholder.
        // Only after the schema is stable do we (in non-strict mode) do a
        // FINAL pass with the string fallback so truly-unresolved
        // placeholders become `"null"` / `"false"` / `"0"` instead of
        // leaking as literal `#X#` to the server.
        final int MAX_ITERS = 8;
        int iter;
        for (iter = 0; iter < MAX_ITERS; iter++) {
            String prev = schema;
            unresolved.clear();
            schema = substitute(schema, P_HASH,  dataMap, null, unresolved, jsonEscape);
            schema = substitute(schema, P_PCT_Q, dataMap, null, unresolved, /* jsonEscape = */ false);
            schema = substitute(schema, P_AT_Q,  dataMap, null, unresolved, /* jsonEscape = */ false);
            schema = substitute(schema, P_AT_BARE, dataMap, null, unresolved, /* jsonEscape = */ false);
            if (schema.equals(prev)) break;
        }
        // Snapshot the unresolved set BEFORE the fallback pass -- the
        // fallback pass calls substitute() with a non-null fallback,
        // which doesn't populate unresolvedSink (see substitute() line
        // where `if (fallback == null) unresolvedSink.add(...)`). Without
        // the snapshot, the WARN below never fires and truly-unresolved
        // placeholders silently become "null" / "false" / "0" in the
        // outbound body -- exactly the silent-fallback shape we fixed
        // for the CSV double-placeholder bug.
        List<String> unresolvedSnapshot = new ArrayList<>(unresolved);
        // Publish the count to the ThreadLocal so downstream callers
        // (mapSqlValues -> Db) can decide whether to trip the null-
        // literal safety check. Set BEFORE the fallback pass so a
        // subsequent successful pass on this thread can't overwrite
        // this count to 0 mid-flight; the value read here is the
        // definitive "was there anything unresolved this call".
        LAST_UNRESOLVED_COUNT.set(unresolvedSnapshot.size());
        if (!strict && !unresolvedSnapshot.isEmpty()) {
            unresolved.clear();
            schema = substitute(schema, P_HASH,  dataMap, "null",  unresolved, jsonEscape);
            schema = substitute(schema, P_PCT_Q, dataMap, "false", unresolved, /* jsonEscape = */ false);
            schema = substitute(schema, P_AT_Q,  dataMap, "0",     unresolved, /* jsonEscape = */ false);
            schema = substitute(schema, P_AT_BARE, dataMap, "0",   unresolved, /* jsonEscape = */ false);
        }

        if (!unresolvedSnapshot.isEmpty()) {
            if (strict) {
                throw new UnresolvedPlaceholderException(
                        "Unresolved placeholders: " + new HashSet<>(unresolvedSnapshot));
            }
            LOG.warn("mapJsonValues: {} unresolved placeholder(s) after {} iteration(s), substituted fallback: {}",
                    unresolvedSnapshot.size(), (iter + 1),
                    new java.util.TreeSet<>(unresolvedSnapshot));
        }
        return schema;
    }

    public static String mapJsonValues(Reader reader, Map<String, String> dataMap) throws Exception {
        return mapJsonValues(readAll(reader), dataMap, false);
    }

    public static String mapJsonValues(Reader reader, Map<String, String> dataMap, boolean strict) throws Exception {
        return mapJsonValues(readAll(reader), dataMap, strict);
    }

    /**
     * SQL-context placeholder substitution: runs {@link #mapJsonValues} with
     * {@code jsonEscape = false} (SQL literals must not JSON-escape a value
     * containing {@code "} or {@code \}) then pipes the result through
     * {@link com.ak.api.data.PlaceholderResolver#resolveAll} so any
     * {@code <<X>>} faker tokens or leftover {@code ${X}} refs also resolve.
     *
     * <p>SQL emit sites (Groovy sql.execute / sql.rows / sql.firstRow /
     * sql.eachRow / dedicated JDBC steps) all call this instead of the
     * bare {@link #mapJsonValues}. Prior behaviour: JDBC SQL only ran
     * through mapJsonValues, which json-escaped values and never
     * resolved {@code <<X>>} -- so a SoapUI cell like
     * {@code WHERE email='<<uuid>>'} shipped the literal to the DB and
     * blew up with a syntax error.
     */
    public static String mapSqlValues(String sql, Map<String, String> dataMap,
                                       Map<String, String> ctx) throws Exception {
        // Clear the Db fallback flag BEFORE the substitution runs so a
        // stale flag from a prior mapSqlValues call on this thread
        // can't falsely trip unsafeSqlReason on THIS call's clean SQL.
        // (Defense-in-depth; BaseApiTest.newTestHolder also clears at
        // per-test boundary.)
        com.ak.api.db.Db.clearNullFallbackFlag();
        String phase1 = mapJsonValues(sql, dataMap, /* strict = */ false,
                                       /* jsonEscape = */ false);
        // mapJsonValues just published the unresolved count on THIS
        // thread's LAST_UNRESOLVED_COUNT. If any placeholder was
        // unresolved, the fallback pass just wrote the literal `null`
        // in its place -- and any raw `'#X#'` in the SQL is now
        // `'null'`. Flag Db so unsafeSqlReason knows to trip on that
        // pattern (see Db.NULL_FALLBACK_TRIPPED for full rationale).
        if (lastUnresolvedCount() > 0) {
            com.ak.api.db.Db.markNullFallbackTripped();
        }
        return com.ak.api.data.PlaceholderResolver.resolveAll(phase1, ctx);
    }

    private static String substitute(String schema, Pattern pattern, Map<String, String> dataMap,
                                     String fallback, List<String> unresolvedSink,
                                     boolean jsonEscape) {
        Matcher m = pattern.matcher(schema);
        StringBuilder out = new StringBuilder();
        while (m.find()) {
            String key = m.group(1);
            String value = dataMap.get(key);
            // Treat empty-string values as UNRESOLVED. `TestSupport.testData`
            // returns "" when no source has a value for a key; propagating
            // that empty string into the JSON body produces silent "field
            // present but empty" failures on the server (400 "must match
            // regex") with no framework signal. Route empty through the
            // fallback / unresolvedSink path so it's visible in the WARN.
            if (value == null || value.isEmpty()) {
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
     * Safe JSON-path extract for Groovy-translated response reads. Guards
     * against empty / non-JSON / null response bodies so the enclosing test
     * doesn't crash with `JsonPathException: Failed to parse the JSON
     * document` when an upstream call returned HTTP 400/409/500 with an
     * empty or HTML error body. The Groovy translator emits every response
     * extract through this helper so a failed upstream degrades to
     * `ctx.get(key) == ""` downstream (which further callers can then
     * handle) instead of the whole test aborting.
     *
     * @return the extracted value, or {@code ""} if the body is empty or
     *         the path fails to resolve. Never null.
     */
    public static String safeJsonExtract(io.restassured.response.Response res, String jsonPath) {
        if (res == null) return "";
        try {
            String body = res.getBody() == null ? null : res.getBody().asString();
            if (body == null || body.isEmpty()) {
                return "";
            }
            String v = res.jsonPath().getString(jsonPath);
            return v == null ? "" : v;
        } catch (Exception e) {
            LOG.warn("safeJsonExtract(path='{}') failed on HTTP {}: {}",
                    jsonPath, res.getStatusCode(), e.getMessage());
            return "";
        }
    }

    /**
     * Safe JSON-path Object lookup for use in {@code softAssert.assertNotNull(...)}
     * checks and count assertions. Returns null on any parse failure so the
     * assertion fails cleanly ("field not present") instead of the whole
     * test aborting with an unchecked {@code JsonPathException}. Empty body
     * -> null. Path doesn't resolve -> null.
     */
    public static Object safeJsonGet(io.restassured.response.Response res, String jsonPath) {
        if (res == null) return null;
        try {
            String body = res.getBody() == null ? null : res.getBody().asString();
            if (body == null || body.isEmpty()) return null;
            return res.jsonPath().get(jsonPath);
        } catch (Exception e) {
            LOG.warn("safeJsonGet(path='{}') failed on HTTP {}: {}",
                    jsonPath, res.getStatusCode(), e.getMessage());
            return null;
        }
    }

    /**
     * Parse a CSV cell as int, falling back to {@code fallback} on
     * null/empty/malformed. Logs a WARN with {@code context} (typically the
     * CSV column name) when parsing fails so a bad cell is diagnosable
     * without hunting through a NumberFormatException stacktrace.
     *
     * <p>Used by the ra_converter emitter for every {@code expected_..._status_code}
     * and {@code expected_count_...} cell so a stray non-numeric value in
     * one CSV cell doesn't cascade into a per-row 3x retry storm.
     */
    public static int parseIntOrDefault(String raw, int fallback, String context) {
        if (raw == null) return fallback;
        String s = raw.trim();
        if (s.isEmpty()) return fallback;
        try {
            return Integer.parseInt(s);
        } catch (NumberFormatException e) {
            LOG.warn("parseIntOrDefault: CSV cell `{}` = \"{}\" is not a valid int; "
                    + "falling back to {}", context, s, fallback);
            return fallback;
        }
    }

    /**
     * Sanity-check a resolved REST URL right before the HTTP call. Logs a
     * LOUD WARN when the URL still has {@code {…}} placeholders (an
     * upstream substitution missed one) or empty path segments
     * ({@code //} outside the scheme -- from a {@code .replace("{id}",
     * "")} where the ctx value was empty). The call still proceeds so
     * the test sees the target's error (usually 404 / 405), but the
     * WARN gives a single-line attribution instead of debugging why the
     * server rejected an apparently-innocuous URL. Called by every
     * emitted @Test method between path build and client.method(...).
     *
     * <p>Guards against the class of bug where an earlier REST step
     * returned 4xx, the extract-into-ctx yielded empty, and the next
     * step's URL substituted an empty id -- the wire sees
     * {@code /guests//businesses/} and Hilton returns 404 / 405, but
     * the log line shows the template so the root cause is hidden.
     */
    public static void assertPathResolved(String verb, String stepName, String resolvedPath) {
        if (resolvedPath == null || resolvedPath.isEmpty()) {
            LOG.warn("assertPathResolved: {} `{}` produced an EMPTY URL -- "
                    + "check that the client method received non-null path args",
                    verb, stepName);
            return;
        }
        // Lingering `{name}` = a substitution was skipped (path-param
        // list didn't include this key). Rare but possible when the
        // SoapUI resource path had a param name the emitter's regex
        // didn't catch (e.g. `{{name}}` or non-alphanumeric names).
        java.util.regex.Matcher m =
                java.util.regex.Pattern.compile("\\{[A-Za-z0-9_-]+\\}")
                        .matcher(resolvedPath);
        if (m.find()) {
            LOG.warn("assertPathResolved: {} `{}` still has unresolved "
                    + "path placeholder `{}` in URL `{}` -- request will "
                    + "hit whatever endpoint the target maps that literal to",
                    verb, stepName, m.group(), resolvedPath);
            return;
        }
        // Empty path segment -- `//` outside the URI scheme's `://`.
        // Scan for `//` that isn't preceded by `:` (scheme separator).
        int idx = 0;
        while ((idx = resolvedPath.indexOf("//", idx)) >= 0) {
            if (idx == 0 || resolvedPath.charAt(idx - 1) != ':') {
                LOG.warn("assertPathResolved: {} `{}` URL has an EMPTY "
                        + "path segment (upstream extract likely returned "
                        + "empty for a required id): `{}`. Server will "
                        + "likely return 404 / 405.",
                        verb, stepName, resolvedPath);
                return;
            }
            idx += 2;
        }
        // Leftover `#Key#` / `@Key@` from expandPlaceholders' 5-iter cap
        // or a placeholder pointing at a ctx key that was never populated.
        // Without this branch, the placeholder gets URL-encoded to
        // `%23Key%23` and the server returns a bland 404 with no signal
        // that the framework failed to substitute a value.
        java.util.regex.Matcher hm = HASH_PATH_PLACEHOLDER.matcher(resolvedPath);
        if (hm.find()) {
            LOG.warn("assertPathResolved: {} `{}` URL still has unresolved "
                    + "placeholder `{}` in `{}` -- ctx has no non-empty "
                    + "value for that key; upstream Groovy/PropertyTransfer "
                    + "likely didn't run or extracted nothing. Server 404 "
                    + "will follow.",
                    verb, stepName, hm.group(), resolvedPath);
            return;
        }
        java.util.regex.Matcher am = AT_PATH_PLACEHOLDER.matcher(resolvedPath);
        if (am.find()) {
            LOG.warn("assertPathResolved: {} `{}` URL still has unresolved "
                    + "placeholder `{}` in `{}` -- ctx has no non-empty "
                    + "value for that key. Server 404 will follow.",
                    verb, stepName, am.group(), resolvedPath);
            return;
        }
        // Also catch SoapUI-style `${X}` refs that PlaceholderResolver's
        // resolveDollarRefs intentionally leaves literal when the key
        // isn't in ctx -- without this warn, they URL-encode as
        // `%24%7BX%7D` and land as a mystery 404 with no framework
        // attribution.
        java.util.regex.Matcher dm = DOLLAR_PATH_PLACEHOLDER.matcher(resolvedPath);
        if (dm.find()) {
            LOG.warn("assertPathResolved: {} `{}` URL still has unresolved "
                    + "SoapUI ref `{}` in `{}` -- ctx has no non-empty "
                    + "value for that key. Server 404 will follow.",
                    verb, stepName, dm.group(), resolvedPath);
            return;
        }
        // Same for `<<X>>` faker tokens that leaked past resolveAll (usually
        // because the CSV cell wasn't run through resolveRow; rare but
        // worth flagging with a clear message rather than a URL-encoded
        // `%3C%3CX%3E%3E` server-side 404).
        java.util.regex.Matcher fm = FAKER_PATH_PLACEHOLDER.matcher(resolvedPath);
        if (fm.find()) {
            LOG.warn("assertPathResolved: {} `{}` URL still has unresolved "
                    + "faker token `{}` in `{}` -- resolveAll didn't fire "
                    + "or the token isn't in FAKER_TOKEN dispatch.",
                    verb, stepName, fm.group(), resolvedPath);
            return;
        }
    }

    private static final java.util.regex.Pattern HASH_PATH_PLACEHOLDER =
            java.util.regex.Pattern.compile("#[A-Za-z0-9_.-]+#");
    private static final java.util.regex.Pattern AT_PATH_PLACEHOLDER =
            java.util.regex.Pattern.compile("@[A-Za-z0-9_.-]+@");
    private static final java.util.regex.Pattern DOLLAR_PATH_PLACEHOLDER =
            java.util.regex.Pattern.compile("\\$\\{[^}]+\\}");
    private static final java.util.regex.Pattern FAKER_PATH_PLACEHOLDER =
            java.util.regex.Pattern.compile("<<[A-Za-z_][A-Za-z0-9_]*(?:\\([^)]*\\))?>>");

    /** {@link #parseIntOrDefault(String, int, String)} for long values. */
    public static long parseLongOrDefault(String raw, long fallback, String context) {
        if (raw == null) return fallback;
        String s = raw.trim();
        if (s.isEmpty()) return fallback;
        try {
            return Long.parseLong(s);
        } catch (NumberFormatException e) {
            LOG.warn("parseLongOrDefault: CSV cell `{}` = \"{}\" is not a valid long; "
                    + "falling back to {}", context, s, fallback);
            return fallback;
        }
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
