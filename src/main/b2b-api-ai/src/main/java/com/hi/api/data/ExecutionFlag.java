package com.hi.api.data;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * The per-row {@code execute} flag: {@code N} switches one data row off.
 *
 * <p>Read from every data-driven test by {@code BaseApiTest.newTestHolder},
 * so it works for imported suites and hand-written tests alike, and for
 * CSV, Excel and JSON sources -- all three hand TestNG a
 * {@code Map<String,String>} row, and this only ever looks at the row.</p>
 *
 * <h2>Fail-open, deliberately</h2>
 *
 * A missing column, a missing cell, whitespace, or any value this does not
 * recognise means <strong>run</strong>. There are 1,064 generated row files
 * and 1,165 rows in this tree that predate the column; a provider that read
 * "no value" as "skip" would have switched off the entire suite silently.
 *
 * <p>The asymmetry is on purpose. Running a row someone meant to disable
 * costs one test execution. Skipping a row someone meant to run costs
 * coverage, and nothing in the report says a requirement stopped being
 * checked. So only an explicit, recognised "off" value skips, and an
 * unrecognised one runs and says so loudly.</p>
 *
 * <h2>Skipped, not filtered</h2>
 *
 * A disabled row is reported as SKIPPED with its reason, not dropped from
 * the data provider. A filtered row would simply not appear, and "what are
 * we not running?" would have no answer anywhere in the output.
 */
public final class ExecutionFlag {

    /** The reserved column name. */
    public static final String COLUMN = "execute";

    /** The row column carrying a case id, used to name the skipped row. */
    private static final String ID_COLUMN = "test_case_id";

    /**
     * Values that switch a row off. {@code Set.of} caps at 10 arguments
     * (see the {@code Map.ofEntries} note in the README), which these fit.
     */
    private static final Set<String> OFF =
            Set.of("n", "no", "false", "0", "off", "skip");

    /** Values that explicitly switch a row on -- same meaning as blank. */
    private static final Set<String> ON =
            Set.of("y", "yes", "true", "1", "on", "run");

    private ExecutionFlag() {}

    /**
     * The first {@code Map} among a test method's parameters, or null.
     *
     * <p>Every provider here yields one-element rows, but the search is
     * over all parameters rather than {@code params[0]}: a test that takes
     * {@code (String env, Map<String,String> row)} is legal, and reading
     * only the first slot would quietly stop honouring the flag for it.</p>
     */
    @SuppressWarnings("unchecked")
    public static Map<String, String> rowOf(Object[] params) {
        if (params == null) {
            return null;
        }
        for (Object p : params) {
            if (p instanceof Map<?, ?> m && !m.isEmpty()) {
                Object k = m.keySet().iterator().next();
                if (k instanceof String) {
                    return (Map<String, String>) m;
                }
            }
        }
        return null;
    }

    /** True when this cell switches the row off. */
    public static boolean isOff(String cell) {
        String v = cell == null ? "" : cell.trim().toLowerCase(Locale.ROOT);
        return !v.isEmpty() && OFF.contains(v);
    }

    /** True when the value is neither recognised nor blank. */
    public static boolean isUnrecognised(String cell) {
        String v = cell == null ? "" : cell.trim().toLowerCase(Locale.ROOT);
        return !v.isEmpty() && !OFF.contains(v) && !ON.contains(v);
    }

    /**
     * Why this row should be skipped, or {@code null} to run it.
     *
     * <p>The message names the column, the value and the row's
     * {@code test_case_id}, because a SKIPPED line without the row's
     * identity cannot be acted on: a CSV with twelve rows gives twelve
     * identical-looking skips.</p>
     */
    public static String skipReason(Map<String, String> row) {
        if (row == null) {
            return null;
        }
        String cell = cellOf(row);
        if (isUnrecognised(cell)) {
            System.out.println("[execute] WARNING: " + COLUMN + "=\""
                    + cell.trim() + "\" is not a value this understands"
                    + idSuffix(row) + ". RUNNING the row. Use Y or N -- an"
                    + " unrecognised value never skips, because a typo must"
                    + " not cost coverage silently.");
            return null;
        }
        if (!isOff(cell)) {
            return null;
        }
        return COLUMN + "=" + cell.trim() + idSuffix(row)
                + " -- switched off in the data sheet, not a failure";
    }

    /**
     * Throw {@link org.testng.SkipException} when the row is switched off.
     *
     * <p>Called from {@code @BeforeMethod}, so TestNG reports the test as
     * SKIPPED. It also prints its own line: {@code onTestSkipped} in
     * {@code ProgressLogListener} suppresses skips under 5ms to hide
     * retry-cycle noise, and a flag skip takes ~0ms, so without this the
     * console would show nothing at all for a disabled row.</p>
     *
     * @param testMethod the upcoming test method, for the console line
     * @param params     that method's parameters, as TestNG will pass them
     */
    public static void skipIfOff(Method testMethod, Object[] params) {
        Map<String, String> row = rowOf(params);
        String reason = skipReason(row);
        if (reason == null) {
            return;
        }
        System.out.println("[execute] SKIPPED  " + label(testMethod)
                + "  " + reason);
        throw new org.testng.SkipException(reason);
    }

    private static String cellOf(Map<String, String> row) {
        String cell = row.get(COLUMN);
        if (cell != null) {
            return cell;
        }
        // Tolerate a header an author retyped as `Execute` or ` execute `.
        // A flag that silently stopped working on a capital letter would
        // be worse than no flag: the row runs and the sheet says it must
        // not, and nothing reports the disagreement.
        for (Map.Entry<String, String> e : row.entrySet()) {
            String k = e.getKey();
            if (k != null && k.trim().equalsIgnoreCase(COLUMN)) {
                return e.getValue();
            }
        }
        return null;
    }

    private static String idSuffix(Map<String, String> row) {
        String id = row.get(ID_COLUMN);
        return (id == null || id.isBlank()) ? "" : " [" + ID_COLUMN + "=" + id.trim() + "]";
    }

    private static String label(Method m) {
        if (m == null) {
            return "(unknown test)";
        }
        return m.getDeclaringClass().getSimpleName() + "#" + m.getName();
    }

    /** Column names this treats as reserved, for the CSV-contract check. */
    public static List<String> reservedColumns() {
        return Arrays.asList(COLUMN);
    }
}
