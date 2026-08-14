package com.ak.api.data;

import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.BufferedReader;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.annotations.DataProvider;

/**
 * Convention-based TestNG DataProvider used by every ra_converter-emitted
 * test class. For a test method
 *   {@code com.ak.api.tests.imported.<suite>[.<resource>].<Class>.<methodName>(Map<String,String> row)}
 * this provider loads
 *   {@code classpath:/csv/<suite>[/<resource>]/<Class>/<methodName>.csv}
 * -- the CSV directory tree mirrors the Java sub-package tree so two
 * `CreateTest` classes in different resource sub-packages don't collide
 * (they used to, when we keyed on {@code getSimpleName()} alone).
 *
 * <p>Blank CSV cells are surfaced as {@code ""}; the CSV file itself is required
 * (missing file -> {@link IllegalStateException} so a test won't silently pass
 * with zero rows).</p>
 *
 * <p>Mirrors the reuse pattern of {@code com.hilton.providers.CsvDataProvider}
 * from the reference framework -- one CSV per @Test method, colocated under a
 * class-named folder so authors don't wire {@code -DdataFile} per class.</p>
 */
public final class PerMethodCsvDataProvider {

    private PerMethodCsvDataProvider() { }

    @DataProvider(name = "rows")
    public static Object[][] rows(Method method) {
        // Derive CSV location from the calling class's FQN so the CSV
        // directory tree mirrors the Java sub-package tree exactly. We
        // strip the shared `.tests.imported.` prefix so paths stay
        // relative to the imported-tests root, then translate `.` -> `/`.
        String fqn = method.getDeclaringClass().getName();
        String anchor = ".tests.imported.";
        int idx = fqn.indexOf(anchor);
        String subPath = (idx >= 0
                ? fqn.substring(idx + anchor.length())
                : method.getDeclaringClass().getSimpleName())
                .replace('.', '/');
        String meth = method.getName();
        String resourcePath = "csv/" + subPath + "/" + meth + ".csv";

        InputStream in = Thread.currentThread().getContextClassLoader()
                .getResourceAsStream(resourcePath);
        if (in == null) {
            throw new IllegalStateException(
                    "PerMethodCsvDataProvider: no CSV on classpath at " + resourcePath
                    + " (expected one row per data-driven scenario for @Test "
                    + method.getDeclaringClass().getSimpleName() + "#" + meth + ")");
        }

        List<Map<String, String>> rows = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String headerLine = br.readLine();
            if (headerLine == null) {
                throw new IllegalStateException("PerMethodCsvDataProvider: empty CSV " + resourcePath);
            }
            // Strip UTF-8 BOM (﻿) if present. Excel + Notepad on
            // Windows save CSVs as UTF-8-with-BOM by default; without
            // this strip the first header cell becomes "﻿<name>"
            // and every `row.get("<name>")` returns null, silently
            // routing tests to fallback defaults with no error.
            if (headerLine.length() > 0 && headerLine.charAt(0) == '\uFEFF') {
                headerLine = headerLine.substring(1);
            }
            String[] header = splitCsvLine(headerLine);
            String line;
            while ((line = br.readLine()) != null) {
                if (line.isEmpty()) continue;
                String[] cells = splitCsvLine(line);
                Map<String, String> row = new LinkedHashMap<>();
                for (int i = 0; i < header.length; i++) {
                    row.put(header[i], i < cells.length ? cells[i] : "");
                }
                rows.add(row);
            }
        } catch (Exception e) {
            throw new IllegalStateException("PerMethodCsvDataProvider: failed reading " + resourcePath, e);
        }

        Object[][] out = new Object[rows.size()][1];
        for (int i = 0; i < rows.size(); i++) out[i][0] = rows.get(i);
        return out;
    }

    /**
     * Strict RFC-4180-ish CSV splitter: honors double-quoted fields
     * (with embedded commas and "" escaped quotes). Sufficient for
     * ra_converter-generated CSVs, which are hand-written or exported
     * from SoapUI -- neither introduces multi-line fields.
     *
     * <p>Enter-quotes-only-at-cell-start: previous version toggled
     * {@code inQuotes} on any {@code "} character, which meant a
     * mid-cell stray quote (from a translation bug, an unescaped
     * user-typed value, or a "Bearer abc\"def" style token) flipped
     * the parser into quoted mode for the remainder of the line --
     * every subsequent comma became data, cells shifted left, and
     * downstream {@code Integer.parseInt} on the wrong cell crashed
     * with NumberFormatException, triggering the RetryAnalyzer 2x per
     * bad row. Now: {@code "} only enters quoted mode when it is the
     * FIRST character of a cell; a mid-cell {@code "} is literal.</p>
     */
    private static String[] splitCsvLine(String line) {
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean inQuotes = false;
        boolean atCellStart = true;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (inQuotes) {
                if (c == '"') {
                    if (i + 1 < line.length() && line.charAt(i + 1) == '"') {
                        cur.append('"');
                        i++;
                    } else {
                        inQuotes = false;
                    }
                } else {
                    cur.append(c);
                }
            } else {
                if (c == ',') {
                    out.add(cur.toString());
                    cur.setLength(0);
                    atCellStart = true;
                    continue;
                } else if (c == '"' && atCellStart) {
                    inQuotes = true;
                } else {
                    cur.append(c);
                }
                atCellStart = false;
            }
        }
        out.add(cur.toString());
        return out.toArray(new String[0]);
    }
}
