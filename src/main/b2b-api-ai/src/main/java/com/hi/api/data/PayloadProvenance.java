package com.hi.api.data;

import java.util.Map;

/**
 * Where a substituted payload value came from.
 *
 * <p>When a request goes out with the wrong value -- or an empty one --
 * the first question is always "which CSV column fed that?". The answer
 * used to require reading the generated template, finding the
 * placeholder, guessing the column name and grepping the CSV. This
 * carries the answer with the data instead.</p>
 *
 * <p>Each row loaded by {@link PerMethodCsvDataProvider} is stamped with
 * {@link #SOURCE_KEY}, naming the CSV and the row number. The
 * substitution in {@code RestUtilities.substitute} reads it back and logs
 * one line per replacement, so the run log states for every value in
 * every payload which file, which row and which column produced it.</p>
 *
 * <p>The key is deliberately not a legal CSV column name -- it starts
 * with {@code __} and contains no character a ReadyAPI DataSource would
 * emit -- so it cannot collide with real data. Everything that walks a
 * row's keys as if they were columns must skip it; see
 * {@code CtxFields.seedFromRow}.</p>
 */
public final class PayloadProvenance {

    private PayloadProvenance() {
    }

    /** Reserved row key holding {@code <csv resource path>#row<N>}. */
    public static final String SOURCE_KEY = "__csvSource";

    /** True for the reserved key, which is provenance and not data. */
    public static boolean isReserved(String key) {
        return SOURCE_KEY.equals(key);
    }

    /**
     * Where each value of the row last resolved on this thread came from,
     * kept beside the row rather than in it: a key added to the row would
     * reach everything that walks its columns. The substitution that
     * follows on the same thread reads it back by the row's identity.
     */
    private static final ThreadLocal<Object[]> LAST_ORIGINS = new ThreadLocal<>();

    /** Record, for the resolved row {@code data}, where each value came from. */
    public static void noteOrigins(Map<String, String> data, Map<String, String> origins) {
        LAST_ORIGINS.set(new Object[] {data, origins});
    }

    /**
     * Where the value under {@code key} came from, in words: {@code csv},
     * {@code generated <<email>>}, {@code from ${name} (...)}, or
     * {@code set by the test} for a row a test built in code.
     */
    @SuppressWarnings("unchecked")
    public static String originOf(Map<String, String> data, String key) {
        Object[] last = LAST_ORIGINS.get();
        if (last != null && last[0] == data) {
            String o = ((Map<String, String>) last[1]).get(key);
            if (o != null) {
                return o;
            }
        }
        return data != null && data.get(SOURCE_KEY) != null ? "csv" : "set by the test (not from a data sheet)";
    }

    /**
     * The CSV origin stamped on this row, or a readable stand-in.
     *
     * <p>Returns {@code "(no csv)"} rather than null so a log line is
     * never half-built: a value substituted from ctx rather than from a
     * CSV row is a normal and interesting case, not a failure.</p>
     */
    public static String of(Map<String, String> dataMap) {
        if (dataMap == null) {
            return "(no csv)";
        }
        String src = dataMap.get(SOURCE_KEY);
        return (src == null || src.isEmpty()) ? "(no csv)" : src;
    }

    /** {@code csv/suite/res/Class/method.csv} -> {@code method.csv#row3}. */
    public static String shortOf(Map<String, String> dataMap) {
        String src = of(dataMap);
        int slash = src.lastIndexOf('/');
        return slash < 0 ? src : src.substring(slash + 1);
    }
}
