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
 * this provider loads the first matching file under
 *   {@code classpath:/csv/<suite>[/<resource>]/<Class>/<methodName>.(csv|xlsx|xls|json)}
 * -- the directory tree mirrors the Java sub-package tree so two
 * `CreateTest` classes in different resource sub-packages don't collide.
 *
 * <p>Search order when more than one file exists: {@code .csv}, then
 * {@code .xlsx}, {@code .xls}, {@code .json}. Override with
 * {@code -DdataFormat=xlsx} (or csv / json / xls) to force one extension.
 * Excel sheet: {@code -DdataSheet=SheetName} (default: first sheet).
 * JSON array key: {@code -DdataArrayKey=rows} (default {@code rows}).</p>
 *
 * <p>Blank cells are {@code ""}. A missing file throws
 * {@link IllegalStateException} so a test will not silently pass with
 * zero rows.</p>
 *
 * <p>Imported ReadyAPI cases still emit CSV; drop an {@code .xlsx} or
 * {@code .json} next to (or instead of) the CSV to data-drive that
 * method from Excel or JSON without changing the {@code @Test}.</p>
 */
public final class PerMethodCsvDataProvider {

    private static final String[] DEFAULT_EXTENSIONS = { "csv", "xlsx", "xls", "json" };

    private PerMethodCsvDataProvider() { }

    @DataProvider(name = "rows")
    public static Object[][] rows(Method method) {
        String resourcePath = resolveResourcePath(method);
        return load(resourcePath);
    }

    /**
     * Classpath resource for this {@code @Test} method. Public so unit
     * tests can assert csv-vs-xlsx-vs-json resolution without firing HTTP.
     */
    public static String resolveResourcePath(Method method) {
        String fqn = method.getDeclaringClass().getName();
        String anchor = ".tests.imported.";
        int idx = fqn.indexOf(anchor);
        String subPath = (idx >= 0
                ? fqn.substring(idx + anchor.length())
                : method.getDeclaringClass().getSimpleName())
                .replace('.', '/');
        String meth = method.getName();
        String dir = "csv/" + subPath + "/" + meth;
        DataFiles.Format forced = DataFiles.formatFromName(System.getProperty("dataFormat"));
        if (forced != null) {
            String[] exts = switch (forced) {
                case CSV -> new String[] { "csv" };
                case EXCEL -> new String[] { "xlsx", "xls" };
                case JSON -> new String[] { "json" };
            };
            for (String ext : exts) {
                String path = dir + "." + ext;
                if (classpathExists(path)) {
                    return path;
                }
            }
            throw new IllegalStateException(
                    "PerMethodCsvDataProvider: dataFormat=" + System.getProperty("dataFormat")
                    + " but no matching file on classpath for @Test "
                    + method.getDeclaringClass().getSimpleName() + "#" + meth
                    + " (tried " + dir + ".{" + String.join(",", exts) + "})");
        }
        for (String ext : DEFAULT_EXTENSIONS) {
            String path = dir + "." + ext;
            if (classpathExists(path)) {
                return path;
            }
        }
        throw new IllegalStateException(
                "PerMethodCsvDataProvider: no CSV/Excel/JSON on classpath at " + dir
                + ".{csv,xlsx,xls,json} (expected one row-file per data-driven "
                + "scenario for @Test "
                + method.getDeclaringClass().getSimpleName() + "#" + meth + ")");
    }

    static Object[][] load(String resourcePath) {
        DataFiles.Format format = DataFiles.formatOf(resourcePath);
        if (format == DataFiles.Format.EXCEL) {
            String sheet = System.getProperty("dataSheet");
            return ExcelDataSource.asTestNg(resourcePath, sheet, 1, Integer.MAX_VALUE);
        }
        if (format == DataFiles.Format.JSON) {
            String arrayKey = System.getProperty("dataArrayKey", "rows");
            return JsonDataSource.asTestNg(resourcePath, arrayKey, 1, Integer.MAX_VALUE);
        }
        return loadCsv(resourcePath);
    }

    private static boolean classpathExists(String resourcePath) {
        return Thread.currentThread().getContextClassLoader()
                .getResource(resourcePath) != null;
    }

    private static Object[][] loadCsv(String resourcePath) {
        InputStream in = Thread.currentThread().getContextClassLoader()
                .getResourceAsStream(resourcePath);
        if (in == null) {
            throw new IllegalStateException(
                    "PerMethodCsvDataProvider: no CSV on classpath at " + resourcePath);
        }

        List<Map<String, String>> rows = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String headerLine = readLogicalCsvRow(br);
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
            int rowIndex = 0;
            org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(PerMethodCsvDataProvider.class);
            while ((line = readLogicalCsvRow(br)) != null) {
                rowIndex++;
                if (line.isEmpty()) continue;
                String[] cells = splitCsvLine(line);
                Map<String, String> row = new LinkedHashMap<>();
                for (int i = 0; i < header.length; i++) {
                    row.put(header[i], i < cells.length ? cells[i] : "");
                }
                // Warn -- do NOT throw -- when the row has MORE cells
                // than the header. Silent drop was the previous
                // behaviour, and it hid the common authoring mistake
                // of adding a value column without adding the header
                // above it (the value ended up as an unattributed
                // trailing cell, invisible to the test). WARN so a
                // scanning eye catches it in surefire output; don't
                // throw so the run still produces useful signal on
                // the columns that DID line up.
                if (cells.length > header.length) {
                    StringBuilder dropped = new StringBuilder();
                    for (int i = header.length; i < cells.length; i++) {
                        if (i > header.length) dropped.append(" | ");
                        dropped.append(cells[i]);
                    }
                    log.warn("PerMethodCsvDataProvider: row {} in {} has {} cells but header has {} columns; "
                            + "extra cells DROPPED: [{}]. Add matching header column(s) or remove trailing cell(s).",
                            rowIndex, resourcePath, cells.length, header.length, dropped);
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
     * Read ONE logical CSV row -- keeps reading physical lines and
     * joining them with newline until the accumulated content has all
     * quoted cells closed. Handles the ra_converter output where
     * request-body CSV cells contain embedded newlines (pretty-printed
     * JSON): without this, {@link BufferedReader#readLine} splits a
     * single logical row into N physical rows, the downstream loop sees
     * N fragmentary rows (mostly empty), TestNG fires the @Test N times
     * against near-duplicate params -- inflating the ProgressLogListener
     * ATTEMPTS counter and causing "attempt 15" banners for a method
     * that should have run 1-2 CSV rows.
     *
     * @return the fully-assembled logical row, or {@code null} at EOF
     */
    private static String readLogicalCsvRow(BufferedReader br) throws java.io.IOException {
        String first = br.readLine();
        if (first == null) return null;
        StringBuilder buf = new StringBuilder(first);
        // Hard cap on physical lines per logical row. Without this,
        // a source CSV with an unclosed `"` (easy authoring mistake:
        // pasted JSON with a stray quote) reads to EOF into one
        // StringBuilder -- OOMs on multi-MB CSVs and TestNG then
        // reports "0 rows" with no diagnostic. 200 is generous for
        // pretty-printed JSON payloads (typical Hilton request body
        // is 20-40 lines) but small enough to abort quickly on
        // malformed input.
        final int MAX_LINES = 200;
        int linesRead = 1;
        while (!balancedQuotes(buf)) {
            if (linesRead >= MAX_LINES) {
                String preview = buf.length() > 200 ? buf.substring(0, 200) + "..." : buf.toString();
                throw new java.io.IOException(
                        "CSV row exceeded " + MAX_LINES + " physical lines without "
                        + "closing a quoted cell -- probable unclosed `\"` in the "
                        + "source. Row starts: " + preview);
            }
            String next = br.readLine();
            if (next == null) break;
            buf.append('\n').append(next);
            linesRead++;
        }
        return buf.toString();
    }

    /**
     * True iff every {@code "} in {@code s} that opens a quoted field
     * has a matching close-quote (per the strict cell-start rule used
     * by {@link #splitCsvLine}). Used to decide whether the logical
     * row is complete after {@code readLine}.
     */
    private static boolean balancedQuotes(CharSequence s) {
        boolean inQuotes = false;
        boolean atCellStart = true;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (inQuotes) {
                if (c == '"') {
                    if (i + 1 < s.length() && s.charAt(i + 1) == '"') {
                        i++;
                    } else {
                        inQuotes = false;
                        atCellStart = false;
                    }
                }
            } else {
                if (c == ',') {
                    atCellStart = true;
                } else if (c == '"' && atCellStart) {
                    inQuotes = true;
                } else {
                    atCellStart = false;
                }
            }
        }
        return !inQuotes;
    }

    /**
     * Strict RFC-4180-ish CSV splitter: honors double-quoted fields
     * (with embedded commas, newlines, and "" escaped quotes).
     * Multi-line quoted fields must be pre-assembled by
     * {@link #readLogicalCsvRow}; this method operates on a single
     * logical row (embedded newlines within cells are preserved).
     *
     * <p>Enter-quotes-only-at-cell-start: a stray {@code "} mid-cell
     * is treated as literal so a malformed cell doesn't shift the
     * remaining cells left.</p>
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
