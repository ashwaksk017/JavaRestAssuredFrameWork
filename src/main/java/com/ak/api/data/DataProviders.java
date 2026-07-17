// =============================================================================
// DataProviders
// -----------------------------------------------------------------------------
// TestNG @DataProvider entry points that read config from the test context.
// Tests reference these by name:
//   @Test(dataProvider = "csvData",  dataProviderClass = DataProviders.class)
//   @Test(dataProvider = "jsonData", dataProviderClass = DataProviders.class)
//
// Config lookup (highest priority first):
//   1. -DdataFile=... / -DdataStartRow=... / -DdataEndRow=... system properties
//   2. <parameter name="dataFile"/> etc. inside the <test> block in testng.xml
//   3. Sensible defaults (startRow=1, endRow=Integer.MAX_VALUE)
//
// Row range semantics: 1-based, inclusive both ends, header row (CSV) or
// wrapping object (JSON) excluded -- matches the reference project's
// BaseClass.provideCSVData().
// =============================================================================

package com.ak.api.data;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

import org.testng.ITestContext;
import org.testng.annotations.DataProvider;

public final class DataProviders {

    private DataProviders() { }

    @DataProvider(name = "csvData")
    public static Object[][] csvData(ITestContext ctx) {
        String path = required(ctx, "dataFile",
                "csvData needs a CSV path -- pass -DdataFile=<classpath-resource> or"
                        + " add <parameter name=\"dataFile\" value=\"testdata/posts.csv\"/> to testng.xml");
        int startRow = intOr(ctx, "dataStartRow", 1);
        int endRow   = intOr(ctx, "dataEndRow", Integer.MAX_VALUE);
        Set<Integer> skip = skipRowsOr(ctx, "dataSkipRows");
        return applySkip(CsvDataSource.asTestNg(path, startRow, endRow), startRow, skip);
    }

    @DataProvider(name = "jsonData")
    public static Object[][] jsonData(ITestContext ctx) {
        String path = required(ctx, "dataFile",
                "jsonData needs a JSON path -- pass -DdataFile=<classpath-resource> or"
                        + " add <parameter name=\"dataFile\" value=\"testdata/posts.json\"/> to testng.xml");
        String arrayKey = valueOr(ctx, "dataArrayKey", "rows");
        int startRow = intOr(ctx, "dataStartRow", 1);
        int endRow   = intOr(ctx, "dataEndRow", Integer.MAX_VALUE);
        Set<Integer> skip = skipRowsOr(ctx, "dataSkipRows");
        return applySkip(JsonDataSource.asTestNg(path, arrayKey, startRow, endRow), startRow, skip);
    }

    // ---- row-skip filtering ----

    /**
     * Filter out rows whose 1-based data-row index appears in {@code skip}.
     * The index is anchored to {@code startRow} -- if startRow=1 and we
     * received rows [r1, r2, r3, r4, r5], skip={3} drops r3 and returns
     * [r1, r2, r4, r5]. Row numbers here match how the CSV/JSON file counts
     * data rows (header excluded), consistent with dataStartRow / dataEndRow.
     */
    private static Object[][] applySkip(Object[][] rows, int startRow, Set<Integer> skip) {
        if (skip.isEmpty() || rows.length == 0) return rows;
        List<Object[]> kept = new ArrayList<>(rows.length);
        for (int i = 0; i < rows.length; i++) {
            int rowIndex = startRow + i; // absolute 1-based row number in the source file
            if (!skip.contains(rowIndex)) kept.add(rows[i]);
        }
        return kept.toArray(new Object[0][]);
    }

    /**
     * Parse a comma / whitespace-separated list of 1-based row numbers from
     * either -D or testng.xml <parameter>. Empty / missing => empty set.
     * Accepts "3", "3,7,9", "3 7 9" and any mix.
     */
    private static Set<Integer> skipRowsOr(ITestContext ctx, String key) {
        String raw = valueOr(ctx, key, null);
        if (raw == null || raw.isBlank()) return Set.of();
        Set<Integer> out = new TreeSet<>();
        for (String tok : raw.split("[,\\s]+")) {
            if (tok.isBlank()) continue;
            try {
                int n = Integer.parseInt(tok.trim());
                if (n >= 1) out.add(n);
            } catch (NumberFormatException nfe) {
                throw new IllegalArgumentException(
                        key + " must be a comma / space-separated list of positive integers, got: '" + raw + "'");
            }
        }
        return out;
    }

    // ---- lookup helpers ----

    private static String valueOr(ITestContext ctx, String key, String fallback) {
        String sys = System.getProperty(key);
        if (sys != null && !sys.isBlank()) return sys;
        if (ctx != null && ctx.getCurrentXmlTest() != null) {
            String xml = ctx.getCurrentXmlTest().getParameter(key);
            if (xml != null && !xml.isBlank()) return xml;
        }
        return fallback;
    }

    private static String required(ITestContext ctx, String key, String errorMsg) {
        String v = valueOr(ctx, key, null);
        if (v == null) throw new IllegalStateException(errorMsg);
        return v;
    }

    private static int intOr(ITestContext ctx, String key, int fallback) {
        String v = valueOr(ctx, key, null);
        return v == null ? fallback : Integer.parseInt(v);
    }
}
