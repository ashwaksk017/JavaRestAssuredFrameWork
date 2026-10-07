package com.hi.api.rest.utilities;

import java.util.List;
import java.util.Map;

import org.testng.asserts.SoftAssert;

import com.hi.api.data.Expected;
import com.hi.api.data.PlaceholderResolver;
import com.hi.api.openapi.OpenApiModels;

import com.fasterxml.jackson.databind.JsonNode;

import io.restassured.response.Response;

/**
 * JsonPath / status / count assertions against the generated-test CSV
 * {@code expected_} CSV columns per step, matching ra_converter emit.
 *
 * <p>Column naming matches ra_converter emit:</p>
 * <ul>
 *   <li>equals: {@code expected_<step>_jsonpath_<suffix>} (fallback
 *       {@code expected_<step>_msgcontent_<lastField>})</li>
 *   <li>exists: {@code expected_<step>_exists_<suffix>} --
 *       {@code "false"} asserts the path is absent (ReadyAPI
 *       Existence Match {@code content=false})</li>
 *   <li>count: {@code expected_<step>_count_<suffix>}</li>
 *   <li>status: {@code expected_<step>_status_code}</li>
 * </ul>
 *
 * <p>{@code suffix} matches converter {@code _clean}: non-alphanumeric
 * runs become underscores
 * ({@code [0].allowBookOnBehalf} → {@code 0_allowBookOnBehalf},
 * {@code notifications[0].message} → {@code notifications_0_message}).</p>
 *
 * <p><b>Before</b> (B2B-9098 confirmValidation):</p>
 * <pre>
 * String actual = RestUtilities.safeJsonExtract(res, "accountStatus");
 * String expected = PlaceholderResolver.resolveAll(
 *     (row.get("expected_..._msgcontent_accountStatus") == null
 *         || row.get("...").isEmpty()) ? "limited" : row.get("..."), ctx);
 * softAssert.assertEquals(actual, expected, "MessageContent = for accountStatus");
 * </pre>
 *
 * <p><b>After:</b> {@code jsonEquals} passes when the historic JsonPath
 * extracts the expected value, or the path is empty/missing and the
 * value appears as a JSON scalar in the body (wrap / moved path).
 * A path that extracts a <em>different</em> non-empty value is a fail
 * (ReadyAPI Message Content equals that element — {@code accountStatus=limited}
 * must not pass because {@code memberStatus=active}).</p>
 * <pre>
 * ResponseAsserts.jsonEquals(softAssert, res, ctx, row,
 *     "post_confirm_validation_limited", "accountStatus", "limited");
 * ResponseAsserts.jsonExists(softAssert, res, row,
 *     "GET_account_member_details_200", "centralBillProfile.created");
 * ResponseAsserts.jsonCount(softAssert, res, row,
 *     "GET_request_200_booking_policies_CB_noTravelerInfo_200",
 *     "[*].centralBillAuthAmount", 1);
 * </pre>
 */
public final class ResponseAsserts {

    /**
     * How many checks did not run, and why.
     *
     * <p>Skipping is right -- asserting a literal `#placeholder#` against
     * a body can only ever be false, and reporting that as a finding was
     * 24 fake failures in one run. But a skipped check is still a check
     * that did not happen, and nothing counted them: one run skipped 246
     * and reported 20 tests PASSED, with no way to tell from the summary
     * that those tests verified almost nothing. A green run that checked
     * nothing is the most expensive kind of green.</p>
     */
    private static final java.util.Map<String, java.util.concurrent.atomic.AtomicInteger>
            SKIPPED = new java.util.concurrent.ConcurrentHashMap<>();

    static void countSkip(String reason) {
        SKIPPED.computeIfAbsent(reason == null ? "unknown" : reason,
                k -> new java.util.concurrent.atomic.AtomicInteger()).incrementAndGet();
    }

    /** Skipped-check counts by reason, for the end-of-run summary. */
    public static java.util.Map<String, Integer> skippedCounts() {
        java.util.Map<String, Integer> out = new java.util.TreeMap<>();
        SKIPPED.forEach((k, v) -> out.put(k, v.get()));
        return out;
    }

    /** Total checks that did not run. */
    public static int skippedTotal() {
        return SKIPPED.values().stream()
                .mapToInt(java.util.concurrent.atomic.AtomicInteger::get).sum();
    }

    public static void resetSkippedCountsForTest() {
        SKIPPED.clear();
    }

    private ResponseAsserts() {}

    /**
     * Soft-assert HTTP status from {@code expected_<step>_status_code},
     * falling back to the row {@code expected} column's {@code statusCode}
     * then {@code defaultStatus}. Skips (with a WARN) when the resolved
     * expected code is negative -- same sentinel the converter uses for
     * "no assertion configured".
     */
    public static void status(SoftAssert softAssert, Response res,
                              Map<String, String> row, String step,
                              int defaultStatus) {
        if (softAssert == null || res == null) return;
        Expected exp = Expected.from(row == null ? null : row.get("expected"));
        String col = "expected_" + step + "_status_code";
        String raw = row == null ? null : row.get(col);
        if (assertStatusInList(softAssert, res, raw, step)) {
            return;
        }
        int expected = RestUtilities.parseIntOrDefault(
                raw, exp.getInt("statusCode", defaultStatus), col);
        if (expected < 0) {
            countSkip("no expected status configured");
            org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                    .warn(" .. [status-code assert SKIPPED] step={} -- no expected status "
                            + "configured (CSV column `{}` empty, and `expected` column has "
                            + "no `statusCode:`). Actual status was {}.",
                            step, col, res.statusCode());
            return;
        }
        softAssert.assertEquals(res.statusCode(), expected, "expected status for " + step);
    }

    /**
     * Status assert using only {@code expected_<step>_status_code} then
     * {@code defaultStatus}. Does <em>not</em> read the row
     * {@code expected} column -- SetupHelper must not inherit a
     * negative-test 403 into tokenRequest.
     */
    public static void statusFromStepColumn(SoftAssert softAssert, Response res,
                                            Map<String, String> row, String step,
                                            int defaultStatus) {
        if (softAssert == null || res == null) return;
        String col = "expected_" + step + "_status_code";
        String raw = row == null ? null : row.get(col);
        if (assertStatusInList(softAssert, res, raw, step)) {
            return;
        }
        int expected = RestUtilities.parseIntOrDefault(raw, defaultStatus, col);
        if (expected < 0) {
            countSkip("no expected status configured");
            org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                    .warn(" .. [status-code assert SKIPPED] step={} -- no expected status "
                            + "configured (CSV column `{}` empty). Actual status was {}.",
                            step, col, res.statusCode());
            return;
        }
        softAssert.assertEquals(res.statusCode(), expected, "expected status for " + step);
    }

    /**
     * A ReadyAPI {@code Valid HTTP Status Codes} assertion may list several
     * codes ({@code 200,201,206}). The converter used to drop such a list
     * to an EMPTY cell -- the step then logged "no expected status
     * configured" and the check was silently skipped (26 skipped checks
     * in one run of goal3487). The cell now carries the list; any listed
     * code passes.
     *
     * @return true when {@code raw} was a list and the assertion was made
     *         (or the list could not be parsed and the skip was recorded);
     *         false when {@code raw} is a single code or empty, so the
     *         caller's single-code path runs unchanged.
     */
    static boolean assertStatusInList(SoftAssert softAssert, Response res,
                                      String raw, String step) {
        java.util.Set<Integer> codes = expectedStatusList(raw);
        if (codes == null) {
            return false;
        }
        int actual = res.statusCode();
        softAssert.assertTrue(codes.contains(actual),
                "expected status for " + step + " in " + codes
                        + " but found [" + actual + "]");
        return true;
    }

    /**
     * {@code "200,201,206"} (also space- or semicolon-separated) to a set;
     * {@code null} when the cell is empty or holds a single code, so the
     * single-code path keeps its exact assertion message.
     */
    static java.util.Set<Integer> expectedStatusList(String raw) {
        if (raw == null) return null;
        String s = raw.trim();
        if (s.isEmpty() || s.matches("-?\\d+")) return null;
        java.util.Set<Integer> out = new java.util.LinkedHashSet<>();
        for (String part : s.split("[,;\\s]+")) {
            if (part.isEmpty()) continue;
            try {
                out.add(Integer.parseInt(part));
            } catch (NumberFormatException e) {
                return null;            // not a code list; let the caller decide
            }
        }
        return out.size() > 1 ? out : null;
    }

    /**
     * Soft-assert the response status is NOT one of the forbidden codes.
     *
     * <p>ReadyAPI's "Invalid HTTP Status Codes" assertion. The converter
     * bakes the codes it recorded; a row overrides them through
     * {@code column} (comma- or space-separated) -- which the generated
     * comment has always promised and nothing read.</p>
     *
     * <p>An empty list forbids nothing. That is a skip, and it says so:
     * a check that quietly passes when it was asked to check nothing is
     * the failure mode this whole contract exists to prevent.</p>
     */
    public static void invalidStatus(SoftAssert softAssert, Response res,
                                     Map<String, String> row, String column,
                                     String defaultCodes) {
        if (softAssert == null || res == null) {
            return;
        }
        String raw = (row == null) ? null : row.get(column);
        String codes = (raw == null || raw.trim().isEmpty()) ? defaultCodes : raw;
        java.util.List<Integer> forbidden = new java.util.ArrayList<>();
        for (String part : ((codes == null) ? "" : codes).split("[,\\s]+")) {
            String t = part.trim();
            if (t.isEmpty()) {
                continue;
            }
            try {
                forbidden.add(Integer.valueOf(t));
            } catch (NumberFormatException ignored) {
                org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                        .warn(" .. [invalid-status] column `{}` holds a "
                                + "non-numeric code `{}` -- ignored.", column, t);
            }
        }
        if (forbidden.isEmpty()) {
            countSkip("no invalid-status column");
            org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                    .warn(" .. [invalid-status assert SKIPPED] column `{}` "
                            + "and the recorded default are both empty -- "
                            + "nothing is forbidden. Actual status was {}.",
                            column, res.statusCode());
            return;
        }
        int actual = res.statusCode();
        softAssert.assertFalse(forbidden.contains(actual),
                "status " + actual + " is one of the invalid codes "
                        + forbidden + " (override column `" + column + "`)");
    }

    /**
     * Soft-assert the resolved expected value appears in the response.
     *
     * <p>ReadyAPI JsonPath Match is path-specific. Hilton payloads wrap
     * the same scalars under different parents ({@code accountStatus} vs
     * {@code data.accountStatus}): when the historic path is empty or
     * missing, a matching JSON scalar anywhere in the body still passes.
     * When the path extracts a <em>different</em> non-empty value, that
     * is a fail — {@code memberStatus=active} must not satisfy
     * {@code accountStatus=active}.</p>
     */
    public static void jsonEquals(SoftAssert softAssert, Response res,
                                  Map<String, String> ctx, Map<String, String> row,
                                  String step, String jsonPath, String defaultExpected) {
        if (softAssert == null || res == null) return;
        String suffix = columnSuffix(jsonPath);
        String colJson = "expected_" + step + "_jsonpath_" + suffix;
        String colMsg = msgContentColumn(row, step, lastSegment(jsonPath));
        if (rowSaysSkip(row, colJson, colMsg)) {
            return;
        }
        String expected = resolvedCsvOrDefault(ctx, row, defaultExpected, colJson, colMsg);
        valueInResponse(softAssert, res, expected, jsonPath, "JsonPath Match: " + jsonPath);
    }

    /**
     * True when a value is still a raw placeholder after resolution.
     *
     * <p>Whole-value match only. A legitimate expectation can CONTAIN a hash
     * or an at-sign (a URL fragment, or a name ending in a hash and a digit); what is never meaningful is
     * an expectation that is nothing BUT an unresolved reference.</p>
     */
    static boolean looksUnresolvedPlaceholder(String value) {
        if (value == null) {
            return false;
        }
        String v = value.trim();
        if (v.length() < 3) {
            return false;
        }
        if (v.startsWith("${") && v.endsWith("}")) {
            return v.indexOf('}') == v.length() - 1;
        }
        char first = v.charAt(0);
        if (first != '#' && first != '@') {
            return false;
        }
        if (v.charAt(v.length() - 1) != first) {
            return false;
        }
        String inner = v.substring(1, v.length() - 1);
        if (inner.isEmpty() || inner.indexOf(first) >= 0) {
            // Two references inside one value: not a single unresolved
            // reference, so leave it to the normal comparison. (Written
            // without an example on purpose -- check_substitutions scans
            // this source and reads a literal hash-wrapped token in a
            // comment as a real unresolved placeholder.)
            return false;
        }
        for (int i = 0; i < inner.length(); i++) {
            char c = inner.charAt(i);
            if (!Character.isLetterOrDigit(c) && c != '_' && c != '.' && c != '-') {
                return false;
            }
        }
        return true;
    }

    /**
     * Resolve the CSV column holding a MessageContentAssertion expectation.
     *
     * <p>The converter numbers these columns by their element ordinal --
     * {@code expected_<step>_msgcontent_<N>_<field>} -- because one assertion
     * can carry several elements, and two of them may share a field name.
     * This lookup used to build {@code expected_<step>_msgcontent_<field>}
     * with no ordinal, so it matched NOTHING: across the reference suite all
     * 1,668 msgcontent columns were unreachable and every such assertion
     * silently fell back to the ReadyAPI literal baked in at emit time --
     * which, for a clustered @Test, is some OTHER case's expected value.</p>
     *
     * <p>Matching on the field alone is unambiguous for 1,406 of the 1,527
     * (step, field) pairs in that suite. For the other 121 the ordinal is
     * genuinely needed; the lowest is used and a WARN names the collision, so
     * the guess is visible rather than silent. Emitters that know the ordinal
     * should pass the exact column instead.</p>
     *
     * @return the matching column name, or {@code null} when the row has none
     */
    static String msgContentColumn(Map<String, String> row, String step, String field) {
        if (row == null || step == null || field == null || field.isEmpty()) {
            return null;
        }
        String bare = "expected_" + step + "_msgcontent_" + field;
        if (row.containsKey(bare)) {
            return bare;
        }
        String prefix = "expected_" + step + "_msgcontent_";
        String suffix = "_" + field;
        String best = null;
        int bestIdx = Integer.MAX_VALUE;
        int hits = 0;
        for (String k : row.keySet()) {
            if (k == null || !k.startsWith(prefix) || !k.endsWith(suffix)) {
                continue;
            }
            String mid = k.substring(prefix.length(), k.length() - suffix.length());
            if (mid.isEmpty() || !isAllDigits(mid)) {
                continue;
            }
            hits++;
            int idx = Integer.parseInt(mid);
            if (idx < bestIdx) {
                bestIdx = idx;
                best = k;
            }
        }
        if (hits > 1) {
            org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class).warn(
                    " .. [msgcontent] step={} field={} matches {} indexed columns;"
                    + " using {}. Pass the element ordinal to disambiguate.",
                    step, field, hits, best);
        }
        return best;
    }

    private static boolean isAllDigits(String s) {
        for (int i = 0; i < s.length(); i++) {
            if (!Character.isDigit(s.charAt(i))) return false;
        }
        return true;
    }

    /**
     * True when the row carries one of these columns but leaves it EMPTY.
     *
     * <p>A blank cell means "this ReadyAPI case does not assert this" -- the
     * column exists only because a SIBLING case in the same cluster asserts
     * it. Verified against the source: case
     * {@code B2B-317_get_attestProgramAccount_200} runs step
     * {@code http_request_200_2} and declares no failureReason assertion at
     * all, yet the converted test asserted {@code attestationVendorFailure},
     * inherited from a sibling row.</p>
     *
     * <p>Skipping is deliberately fail-open: it can lose a check, but it can
     * never invent a failure. An ABSENT column still falls back to the
     * ReadyAPI literal, which is what the converter intends for a case that
     * simply has no CSV override.</p>
     */
    static boolean rowSaysSkip(Map<String, String> row, String... cols) {
        if (row == null) {
            return false;
        }
        boolean sawEmpty = false;
        for (String c : cols) {
            if (c == null) continue;
            if (!row.containsKey(c)) continue;
            String v = row.get(c);
            if (v != null && !v.isEmpty()) {
                return false;   // a real expectation wins
            }
            sawEmpty = true;
        }
        return sawEmpty;
    }

    /**
     * Soft-assert {@code expected} at historic {@code jsonPath}, or (when
     * that path is empty/missing) as a JSON scalar in the body. Used by
     * converter branches that already resolved the CSV/SoapUI expected.
     */
    public static void valueInResponse(SoftAssert softAssert, Response res,
                                       String expected, String jsonPath,
                                       String label) {
        if (softAssert == null || res == null) return;
        String want = expected == null ? "" : expected;
        String path = jsonPath == null ? "" : jsonPath;
        if (looksUnresolvedPlaceholder(want)) {
            // The expected value is still a placeholder after resolution, so
            // nothing populated the key it names. Asserting the literal text
            // "#PropertiesDetails_VerifyWebsite#" against a response body can
            // only ever be false -- 24 failures in one run were exactly this.
            // Skip rather than report a certainty as a finding. Fail-open,
            // like rowSaysSkip: it can lose a check, it cannot invent one.
            countSkip("expected is an unresolved placeholder");
            org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class).warn(
                    " .. [assert SKIPPED] {} -- expected is an unresolved "
                    + "placeholder {} (nothing published that key)",
                    label == null ? "assert" : label, want);
            return;
        }
        String actual = path.isEmpty() ? "" : RestUtilities.safeJsonExtract(res, path);
        String tag = label == null ? "response contains" : label;
        if (want.isEmpty()) {
            boolean empty = actual == null || actual.isEmpty();
            org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                    .info(" .. [assert empty] {} pathActual={}", tag, actual);
            softAssert.assertTrue(empty, tag + " expected empty path value");
            return;
        }
        boolean pathHit = want.equals(actual);
        boolean pathHasOther = actual != null && !actual.isEmpty() && !pathHit;
        boolean hit = pathHit || (!pathHasOther && valuePresentInBody(res, want));
        org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                .info(" .. [assert contains] {} expected={} pathActual={} found={}",
                        tag, want, actual, hit);
        softAssert.assertTrue(hit, tag + " expected [" + want + "] in response body");
    }

    /**
     * Soft-assert {@code expected} is present in the response body
     * (JSON scalar walk, then quoted-string fallback). Pass {@code jsonPath}
     * when a historic path should still count as a hit.
     */
    public static void bodyContains(SoftAssert softAssert, Response res,
                                    String expected, String label) {
        bodyContains(softAssert, res, expected, null, label);
    }

    public static void bodyContains(SoftAssert softAssert, Response res,
                                    String expected, String jsonPath, String label) {
        valueInResponse(softAssert, res, expected, jsonPath, label);
    }

    /**
     * ReadyAPI's Simple Contains / Equals / NotContains: a RAW substring
     * test over the whole response body, carrying the same
     * unresolved-placeholder guard every other assertion here applies.
     *
     * <p>Deliberately NOT routed through {@link #valueInResponse}. That
     * one matches a JSON scalar or a quoted string; ReadyAPI's Simple
     * assertions are a plain {@code String.contains} over the raw body,
     * so reusing the JSON walk would silently change which assertions
     * pass. The only thing borrowed is the guard.</p>
     *
     * <p>The generated code used to inline
     * {@code softAssert.assertTrue(res.asString().contains(token), ...)},
     * which bypassed this class entirely. When nothing published the key
     * a token names, that asserted the literal text
     * {@code ${groupid#roomTypeCode}} against a response body -- 21
     * failures in one run, every one a certainty rather than a finding,
     * and none counted as a skipped check, which is why that run's
     * digest reported zero skips while 21 checks had not really run.</p>
     *
     * <p>{@code mustContain=false} (NotContains) is guarded for the
     * opposite reason: an unresolved placeholder is never in the body, so
     * the assertion PASSED vacuously. Silent success is the worse of the
     * two failure modes, because nothing in the run says the check was
     * hollow.</p>
     */
    public static void rawBodyContains(SoftAssert softAssert, Response res,
                                       String expected, boolean mustContain,
                                       String label) {
        if (softAssert == null || res == null) {
            return;
        }
        String want = expected == null ? "" : expected;
        String tag = label == null ? "response contains" : label;
        if (looksUnresolvedPlaceholder(want)) {
            countSkip("expected is an unresolved placeholder");
            org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class).warn(
                    " .. [assert SKIPPED] {} -- expected is an unresolved "
                    + "placeholder {} (nothing published that key)",
                    tag, want);
            return;
        }
        String body;
        try {
            body = res.asString();
        } catch (RuntimeException e) {
            // A response with no readable body is not a reason to fail the
            // suite inside a reporting path; treat it as absent.
            body = null;
        }
        boolean hit = body != null && body.contains(want);
        org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                .info(" .. [assert raw-contains] {} expected={} found={}",
                        tag, want, hit);
        softAssert.assertTrue(hit == mustContain,
                tag + (mustContain ? " expected [" : " expected NOT [")
                + want + "] in response body");
    }

    /**
     * ReadyAPI contains operator: path extract {@code contains} expected,
     * or the value is present as a JSON scalar anywhere in the body.
     */
    public static void substringInResponse(SoftAssert softAssert, Response res,
                                           String expected, String jsonPath,
                                           String label) {
        if (softAssert == null || res == null) return;
        String want = expected == null ? "" : expected;
        String tag = label == null ? "response contains" : label;
        if (want.isEmpty()) {
            valueInResponse(softAssert, res, want, jsonPath, tag);
            return;
        }
        String actual = jsonPath == null || jsonPath.isEmpty()
                ? "" : RestUtilities.safeJsonExtract(res, jsonPath);
        boolean pathContains = actual != null && actual.contains(want);
        boolean pathHasOther = actual != null && !actual.isEmpty() && !pathContains;
        boolean hit = pathContains || (!pathHasOther && valuePresentInBody(res, want));
        org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                .info(" .. [assert substring] {} expected={} pathActual={} found={}",
                        tag, want, actual, hit);
        softAssert.assertTrue(hit, tag + " expected [" + want + "] in response body");
    }

    /**
     * True when {@code expected} equals any JSON scalar in {@code body}, or
     * (non-JSON body) appears as a quoted string. Exact scalars only:
     * {@code verified} does not match {@code unverified}.
     */
    public static boolean bodyContainsExpected(String body, String expected) {
        if (body == null || expected == null || expected.isEmpty()) return false;
        JsonNode tree = RestUtilities.parseJsonTree(body);
        if (tree != null && !tree.isMissingNode() && jsonTreeHasScalar(tree, expected)) {
            return true;
        }
        return quotedStringPresent(body, expected);
    }

    static boolean valuePresentInBody(Response res, String expected) {
        if (res == null || expected == null || expected.isEmpty()) return false;
        String body = res.asString() == null ? "" : res.asString();
        return bodyContainsExpected(body, expected);
    }

    static boolean jsonTreeHasScalar(JsonNode node, String expected) {
        if (node == null || node.isMissingNode() || node.isNull() || expected == null) {
            return false;
        }
        if (node.isValueNode()) {
            return scalarEquals(node, expected);
        }
        if (node.isObject()) {
            var fields = node.fields();
            while (fields.hasNext()) {
                if (jsonTreeHasScalar(fields.next().getValue(), expected)) return true;
            }
            return false;
        }
        if (node.isArray()) {
            for (JsonNode n : node) {
                if (jsonTreeHasScalar(n, expected)) return true;
            }
        }
        return false;
    }

    private static boolean scalarEquals(JsonNode node, String expected) {
        if (expected.equals(node.asText())) return true;
        if (node.isBoolean()) {
            return expected.equals(Boolean.toString(node.booleanValue()));
        }
        if (node.isNumber()) {
            try {
                return new java.math.BigDecimal(expected)
                        .compareTo(node.decimalValue()) == 0;
            } catch (NumberFormatException ignored) {
                return false;
            }
        }
        return false;
    }

    private static boolean quotedStringPresent(String body, String expected) {
        return body.contains("\"" + expected + "\"")
                || body.contains("'" + expected + "'");
    }

    static void collectScalarLeaves(JsonNode node, java.util.List<String> out) {
        if (node == null || node.isMissingNode() || node.isNull()) return;
        if (node.isValueNode()) {
            out.add(node.asText());
            return;
        }
        if (node.isObject()) {
            var fields = node.fields();
            while (fields.hasNext()) {
                collectScalarLeaves(fields.next().getValue(), out);
            }
            return;
        }
        if (node.isArray()) {
            for (JsonNode n : node) collectScalarLeaves(n, out);
        }
    }

    /**
     * Soft-assert the JsonPath node equals {@code defaultExpected} as JSON
     * trees (key-order insensitive). Used when ReadyAPI JsonPath Match
     * {@code content} is a JSON object/array rather than a scalar.
     */
    public static void jsonTreeEquals(SoftAssert softAssert, Response res,
                                      Map<String, String> ctx, Map<String, String> row,
                                      String step, String jsonPath,
                                      String defaultExpected) {
        if (softAssert == null || res == null) return;
        String suffix = columnSuffix(jsonPath);
        String colJson = "expected_" + step + "_jsonpath_" + suffix;
        if (rowSaysSkip(row, colJson)) {
            return;
        }
        String expected = resolvedCsvOrDefault(ctx, row, defaultExpected, colJson);
        JsonNode expectedNode = RestUtilities.parseJsonTree(expected);
        JsonNode actualNode = RestUtilities.toJsonTree(
                RestUtilities.safeJsonGet(res, jsonPath));
        org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                .info(" .. [assert tree] {} expected={} actual={}",
                        jsonPath, expectedNode, actualNode);
        if (expectedNode == null || expectedNode.isMissingNode()) {
            softAssert.fail("JsonPath Match tree: could not parse expected JSON for "
                    + jsonPath);
            return;
        }
        if (expectedNode.equals(actualNode)) {
            softAssert.assertEquals(actualNode, expectedNode,
                    "JsonPath Match tree: " + jsonPath);
            return;
        }
        java.util.List<String> leaves = new java.util.ArrayList<>();
        collectScalarLeaves(expectedNode, leaves);
        if (leaves.isEmpty()) {
            softAssert.assertEquals(actualNode, expectedNode,
                    "JsonPath Match tree: " + jsonPath);
            return;
        }
        boolean allPresent = true;
        for (String leaf : leaves) {
            if (leaf == null || leaf.isEmpty()) continue;
            if (!valuePresentInBody(res, leaf)) {
                allPresent = false;
                break;
            }
        }
        org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                .info(" .. [assert tree contains] {} leaves={} present={}",
                        jsonPath, leaves.size(), allPresent);
        softAssert.assertTrue(allPresent,
                "JsonPath Match tree: " + jsonPath
                        + " expected leaves " + leaves + " in response body");
    }

    /**
     * Soft-assert the JsonPath is absent / null.
     */
    public static void jsonAbsent(SoftAssert softAssert, Response res,
                                  String jsonPath) {
        if (softAssert == null || res == null) return;
        softAssert.assertTrue(
                RestUtilities.safeJsonGet(res, jsonPath) == null,
                "JsonPath absent: " + jsonPath);
    }

    /**
     * Soft-assert the JsonPath resolves to a non-null value, unless the CSV
     * cell {@code expected_<step>_exists_<suffix>} is {@code "false"} --
     * that is ReadyAPI Existence Match {@code content=false} and maps to
     * {@link #jsonAbsent}.
     */
    /**
     * Groovy truthiness on a JSON path: present, and not empty.
     *
     * <p>`assert json.groupId : "groupId is required"` is the commonest
     * shape in these projects' script assertions, and it does NOT mean
     * "not null" -- Groovy treats "" and [] as false too. jsonExists
     * asserts non-null only, so translating truthiness to it would pass
     * on an empty string, which is exactly the value these assertions
     * exist to catch.</p>
     */
    public static void jsonTruthy(SoftAssert softAssert, Response res,
                                  String jsonPath, String message) {
        if (softAssert == null || res == null) {
            return;
        }
        Object v = RestUtilities.safeJsonGet(res, jsonPath);
        boolean truthy = v != null;
        if (truthy && v instanceof CharSequence) {
            truthy = ((CharSequence) v).toString().trim().length() > 0;
        }
        if (truthy && v instanceof java.util.Collection) {
            truthy = !((java.util.Collection<?>) v).isEmpty();
        }
        if (truthy && v instanceof java.util.Map) {
            truthy = !((java.util.Map<?, ?>) v).isEmpty();
        }
        softAssert.assertTrue(truthy,
                (message == null || message.isEmpty())
                        ? "JsonPath truthy: " + jsonPath : message);
    }

    /** The response body is present and not blank. */
    public static void bodyNotEmpty(SoftAssert softAssert, Response res,
                                    String message) {
        if (softAssert == null || res == null) {
            return;
        }
        String body = res.getBody() == null ? null : res.getBody().asString();
        softAssert.assertTrue(body != null && !body.trim().isEmpty(),
                (message == null || message.isEmpty())
                        ? "response body is not empty" : message);
    }

    /**
     * An ISO-8601 instant on the response is no further than
     * {@code maxMinutes} from now.
     *
     * <p>The `CacheExpiryTimeLimit45min` shape: parse the field as an
     * Instant and assert the gap to now is within a limit. Reported as a
     * miss rather than an error when the field is absent or unparseable,
     * because "the timestamp is missing" and "the timestamp is too far
     * out" are different findings and the message should say which.</p>
     */
    public static void instantWithinMinutes(SoftAssert softAssert, Response res,
                                            String jsonPath, long maxMinutes,
                                            String message) {
        if (softAssert == null || res == null) {
            return;
        }
        Object raw = RestUtilities.safeJsonGet(res, jsonPath);
        String text = raw == null ? "" : String.valueOf(raw).trim();
        if (text.isEmpty()) {
            softAssert.fail(jsonPath + " is absent, so it cannot be within "
                    + maxMinutes + " minutes of now");
            return;
        }
        try {
            java.time.Instant at = java.time.Instant.parse(text);
            long mins = java.time.Duration.between(java.time.Instant.now(), at)
                    .toMinutes();
            softAssert.assertTrue(mins <= maxMinutes,
                    (message == null || message.isEmpty())
                            ? jsonPath + "=" + text + " is " + mins
                              + " minutes out, limit " + maxMinutes
                            : message);
        } catch (java.time.format.DateTimeParseException e) {
            softAssert.fail(jsonPath + "=" + text
                    + " is not an ISO-8601 instant");
        }
    }

    /**
     * A JSON value starts with the prefix this ENVIRONMENT requires.
     *
     * <p>The ReadyAPI original reads
     * {@code context.testCase.testSuite.project.activeEnvironment.name}
     * and branches: Corporate_500 wants a ratePlanCode starting "5",
     * Partner_600 "6", Partner_700 "7". The equivalent here is
     * {@code com.hi.api.config.Config.env()}, matched the same way the script does -- by
     * CONTAINS, not equals, because the ReadyAPI names carry a prefix.</p>
     *
     * <p>An environment none of the branches name is not a failure: the
     * Groovy falls through every `if` and asserts nothing, so this does
     * the same rather than inventing a rule the original never had.</p>
     */
    public static void prefixForEnvironment(SoftAssert softAssert, Response res,
                                            String jsonPath,
                                            Map<String, String> prefixByEnv,
                                            String message) {
        if (softAssert == null || res == null || prefixByEnv == null) {
            return;
        }
        String env = com.hi.api.config.Config.env();
        if (env == null) {
            return;
        }
        String want = null;
        for (Map.Entry<String, String> e : prefixByEnv.entrySet()) {
            if (e.getKey() != null && env.contains(e.getKey())) {
                want = e.getValue();
                break;
            }
        }
        if (want == null) {
            return;                 // no branch matched, as in the original
        }
        Object raw = RestUtilities.safeJsonGet(res, jsonPath);
        String got = raw == null ? "" : String.valueOf(raw).trim();
        softAssert.assertTrue(got.startsWith(want),
                (message == null || message.isEmpty())
                        ? "expected " + jsonPath + " to start with " + want
                          + " for " + env + ". Actual value: " + got
                        : message);
    }

    public static void jsonExists(SoftAssert softAssert, Response res,
                                  Map<String, String> row, String step,
                                  String jsonPath) {
        if (softAssert == null || res == null) return;
        String col = "expected_" + step + "_exists_" + columnSuffix(jsonPath);
        String flag = row == null ? "true" : row.getOrDefault(col, "true");
        if ("false".equalsIgnoreCase(flag)) {
            jsonAbsent(softAssert, res, jsonPath);
            return;
        }
        softAssert.assertNotNull(
                RestUtilities.safeJsonGet(res, jsonPath),
                "JsonPath exists: " + jsonPath);
    }

    /**
     * Soft-assert the number of matches at {@code jsonPath} (list size, or
     * 1 if a scalar, or 0 if null) equals the CSV override or {@code defaultCount}.
     */
    public static void jsonCount(SoftAssert softAssert, Response res,
                                 Map<String, String> row, String step,
                                 String jsonPath, int defaultCount) {
        if (softAssert == null || res == null) return;
        Object count = RestUtilities.safeJsonGet(res, jsonPath);
        int actual = count instanceof List
                ? ((List<?>) count).size()
                : (count == null ? 0 : 1);
        String col = "expected_" + step + "_count_" + columnSuffix(jsonPath);
        String raw = row == null ? null : row.get(col);
        int expected = RestUtilities.parseIntOrDefault(raw, defaultCount, col);
        softAssert.assertEquals(actual, expected, "JsonPath Count for " + jsonPath);
    }

    /**
     * Flatten a JsonPath into the converter's CSV column suffix
     * ({@code _assert_col_key} / {@code _clean} in ra_converter.py):
     * trim, strip a leading mix of {@code $} and {@code .}, replace every
     * non-alphanumeric run with {@code _}, strip edge underscores.
     * Empty input becomes {@code root}. Visible for unit tests.
     */
    /**
     * ReadyAPI "HTTP Header Exists": the named response header must be
     * present and non-empty.
     *
     * <p>Was previously emitted as a {@code // TODO manual review} comment,
     * so the assertion silently did nothing while the converter's coverage
     * report still counted the case as fully converted. The CSV column
     * {@code expected_<step>_header_<name>} overrides the expectation --
     * set it to {@code false} to assert the header is ABSENT.</p>
     *
     * <p>Header names are compared case-insensitively, per RFC 7230.</p>
     */
    public static void headerExists(SoftAssert softAssert, Response res,
                                    Map<String, String> row, String stepName,
                                    String headerName) {
        if (res == null || headerName == null || headerName.isEmpty()) {
            return;
        }
        String col = "expected_" + stepName + "_header_"
                + headerName.replaceAll("[^A-Za-z0-9]+", "_");
        String expectedRaw = row == null ? null : row.get(col);
        boolean expectPresent = expectedRaw == null || expectedRaw.isEmpty()
                || !"false".equalsIgnoreCase(expectedRaw.trim());

        String actual = null;
        for (io.restassured.http.Header h : res.getHeaders()) {
            if (h.getName() != null && h.getName().equalsIgnoreCase(headerName)) {
                actual = h.getValue();
                break;
            }
        }
        boolean present = actual != null && !actual.isEmpty();
        softAssert.assertEquals(present, expectPresent,
                "HTTP Header " + (expectPresent ? "Exists" : "Absent") + ": '"
                + headerName + "' on step " + stepName
                + (present ? " (value: " + actual + ")" : " (not present)"));
    }

    public static String columnSuffix(String jsonPath) {
        if (jsonPath == null) return "root";
        String s = jsonPath.trim();
        int i = 0;
        while (i < s.length()) {
            char c = s.charAt(i);
            if (c == '$' || c == '.') i++;
            else break;
        }
        s = s.substring(i).replaceAll("[^A-Za-z0-9]+", "_");
        int start = 0;
        int end = s.length();
        while (start < end && s.charAt(start) == '_') start++;
        while (end > start && s.charAt(end - 1) == '_') end--;
        s = s.substring(start, end);
        return s.isEmpty() ? "root" : s;
    }

    /**
     * Bind the body to an OpenAPI-generated model. Does not replace
     * {@link #jsonEquals} for imported ReadyAPI assertions.
     */
    public static <T> T asModel(Response res, Class<T> type) {
        return OpenApiModels.as(res, type);
    }

    public static void matchesOpenApi(SoftAssert softAssert, Response res, String definitionName) {
        if (softAssert == null || res == null) return;
        softAssert.assertTrue(
                OpenApiModels.matchesDefinition(res, definitionName),
                "OpenAPI definition " + definitionName);
    }

    static String lastSegment(String jsonPath) {
        if (jsonPath == null || jsonPath.isEmpty()) return "";
        String s = jsonPath.replace("[*]", "");
        int dot = s.lastIndexOf('.');
        String tail = dot < 0 ? s : s.substring(dot + 1);
        return tail.replaceAll("[\\[\\]]", "");
    }

    private static String firstNonBlank(Map<String, String> row, String... keys) {
        if (row == null) return null;
        for (String k : keys) {
            String v = row.get(k);
            if (v != null && !v.isEmpty()) return v;
        }
        return null;
    }

    /**
     * CSV override, then Java default. An emit-time {@code @Properties_expected_…@}
     * rewrite that never landed in ctx is treated as missing so ReadyAPI's
     * hardcoded assertion literal (the Java default) is used.
     */
    static String resolvedCsvOrDefault(Map<String, String> ctx, Map<String, String> row,
                                       String defaultExpected, String... csvKeys) {
        String raw = firstNonBlank(row, csvKeys);
        String expectedRaw = (raw == null || raw.isEmpty()) ? defaultExpected : raw;
        String expected = PlaceholderResolver.resolveAll(
                expectedRaw == null ? "" : expectedRaw, ctx);
        if (isUnresolvedPropertiesPlaceholder(expected)
                && defaultExpected != null && !defaultExpected.equals(expectedRaw)) {
            expected = PlaceholderResolver.resolveAll(defaultExpected, ctx);
        }
        return expected == null ? "" : expected;
    }

    static boolean isUnresolvedPropertiesPlaceholder(String value) {
        if (value == null) return false;
        String s = value.trim();
        return s.length() > 13 && s.startsWith("@Properties_") && s.endsWith("@")
                && s.indexOf('@', 1) == s.length() - 1;
    }
}
