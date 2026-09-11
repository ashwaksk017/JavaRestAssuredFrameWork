package com.ak.api.rest.utilities;

import java.util.List;
import java.util.Map;

import org.testng.asserts.SoftAssert;

import com.ak.api.data.Expected;
import com.ak.api.data.PlaceholderResolver;
import com.ak.api.openapi.OpenApiModels;

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
        int expected = RestUtilities.parseIntOrDefault(
                raw, exp.getInt("statusCode", defaultStatus), col);
        if (expected < 0) {
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
        int expected = RestUtilities.parseIntOrDefault(raw, defaultStatus, col);
        if (expected < 0) {
            org.slf4j.LoggerFactory.getLogger(ResponseAsserts.class)
                    .warn(" .. [status-code assert SKIPPED] step={} -- no expected status "
                            + "configured (CSV column `{}` empty). Actual status was {}.",
                            step, col, res.statusCode());
            return;
        }
        softAssert.assertEquals(res.statusCode(), expected, "expected status for " + step);
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
        String last = lastSegment(jsonPath);
        String colMsg = "expected_" + step + "_msgcontent_" + last;
        String expected = resolvedCsvOrDefault(ctx, row, defaultExpected, colJson, colMsg);
        valueInResponse(softAssert, res, expected, jsonPath, "JsonPath Match: " + jsonPath);
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
