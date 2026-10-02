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
