package com.hi.api.rest.utilities.phase;

import java.util.function.Function;

import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.support.ImportedScenario;

/**
 * Where a value for a REST call comes from.
 *
 * <p>The generated phase bodies spelled this out as Java expressions --
 * {@code TestSupport.ctxGet(ctx, "PropertiesDetails.accountID")},
 * {@code RestUtilities.safeJsonExtract(http_request_200_enroll_guestRes, "guestId")},
 * {@code row.getOrDefault("path_x_id", "123")} -- and because the text
 * differed, two calls to the same endpoint became two methods. A Ref names
 * the SOURCE instead, so the call is written once and the case says where
 * its id comes from.</p>
 *
 * <p>The four named kinds cover every expression the converter emits for
 * path parameters and tokens; {@link #expr(Function)} is the escape hatch
 * for a translated form none of them fits, and is generated as a lambda.</p>
 */
public abstract class Ref {

    /** Resolve against the running flow. Never null: absent values are "". */
    public abstract String resolve(PhaseContext c);

    /** A stable, human-readable form for logs and the dedup report. */
    public abstract String describe();

    /**
     * True when this value is empty because the AUTHOR left it empty, not
     * because something upstream failed to produce it.
     *
     * <p>The broken-path guard cannot tell the two apart from the URL alone,
     * and they need opposite answers: an extract that came back empty is a
     * fault to stop on, while a ReadyAPI parameter saved with no value is the
     * request the project actually sends. Only a row literal with no fallback
     * and no cell can say "the author meant this"; every other kind stays
     * false, so the guard keeps failing closed.</p>
     */
    public boolean authorEmpty(PhaseContext c) {
        return false;
    }

    @Override
    public String toString() {
        return describe();
    }

    /** A ctx key, read through the alias-walking {@code ImportedScenario.ctxGet}. */
    public static Ref ctx(String key) {
        return new Ref() {
            @Override
            public String resolve(PhaseContext c) {
                return resolveArg(c.ctx, c.row, key);
            }

            @Override
            public String describe() {
                return "ctx:" + key;
            }
        };
    }

    /** A JsonPath read from an EARLIER phase's response in this flow. */
    public static Ref resp(String step, String jsonPath) {
        return new Ref() {
            @Override
            public String resolve(PhaseContext c) {
                io.restassured.response.Response r = c.response(step);
                if (r == null) {
                    return "";
                }
                String v = RestUtilities.safeJsonExtract(r, jsonPath);
                return v == null ? "" : v;
            }

            @Override
            public String describe() {
                return "resp:" + step + "#" + jsonPath;
            }
        };
    }

    /** A CSV column with the SoapUI literal as fallback. */
    public static Ref row(String column, String fallback) {
        return new Ref() {
            @Override
            public String resolve(PhaseContext c) {
                String v = c.row == null ? null : c.row.get(column);
                if (v == null || v.isEmpty()) {
                    return fallback == null ? "" : fallback;
                }
                return v;
            }

            @Override
            public boolean authorEmpty(PhaseContext c) {
                return (fallback == null || fallback.isEmpty()) && resolve(c).isEmpty();
            }

            @Override
            public String describe() {
                return "row:" + column + "|" + fallback;
            }
        };
    }

    /** A fixed value (e.g. the repeating-digit ids negative tests rely on). */
    public static Ref literal(String value) {
        return new Ref() {
            @Override
            public String resolve(PhaseContext c) {
                return value == null ? "" : value;
            }

            @Override
            public String describe() {
                return "lit:" + value;
            }
        };
    }

    /** Escape hatch: a generated lambda for a translated form. */
    public static Ref expr(String description, Function<PhaseContext, String> fn) {
        return new Ref() {
            @Override
            public String resolve(PhaseContext c) {
                String v = fn.apply(c);
                return v == null ? "" : v;
            }

            @Override
            public String describe() {
                return "expr:" + description;
            }
        };
    }

    /**
     * A phase argument: ctx first, then the CSV row under either spelling.
     *
     * <h2>Why the row fallback exists</h2>
     *
     * <p>The converter names a CSV column with an UNDERSCORE
     * ({@code DataSource_propCode}) and emits a spec that reads the DOTTED
     * ctx key ({@code Ref.ctx("DataSource.propCode")}). ctx is seeded only
     * for prefixes that got a {@code CtxFields.seedFromRow(ctx, row,
     * "<prefix>.")} call, and nothing ever emitted one for
     * {@code DataSource.} -- so 41 reads in one suite resolved to "" and
     * every URL lost a segment: {@code /props//groups}. ReadyAPI ran the
     * same case green, which is what made it a conversion fault rather than
     * missing data: the value was in the datasheet the whole time.</p>
     *
     * <p>This is the third sighting of that shape. It was fixed once inside
     * {@code CtxFields.seedFromRow}, whose comment records the identical
     * symptom one segment further along ({@code /topics//partitions/3}), but
     * that repair covered only the prefixes the converter happens to emit a
     * seed call for. Auditing both converted suites for "reads a ctx key
     * nothing produces, while the CSV holds a column that would supply it"
     * found four more, in both suites -- which is why the fix lives in the
     * READ path now instead of being chased prefix by prefix.</p>
     *
     * <p>ctx is consulted FIRST, so a runtime extract still beats a
     * datasheet literal, and a key that is genuinely absent (a negative test
     * asserting an empty id) stays empty, because no column supplies it
     * either.</p>
     */
    public static String resolveArg(java.util.Map<String, String> ctx,
                                    java.util.Map<String, String> row,
                                    String key) {
        String v = ImportedScenario.ctxGet(ctx, key);
        if (v != null && !v.isEmpty()) {
            return v;
        }
        if (row == null || key == null) {
            return "";
        }
        String raw = row.get(key);
        if (raw == null || raw.isEmpty()) {
            raw = row.get(key.replace('.', '_'));
        }
        if (raw == null || raw.isEmpty()) {
            raw = row.get(key.replace('_', '.'));
        }
        if (raw == null || raw.isEmpty()) {
            return "";
        }
        String expanded = expandCell(ctx, row, raw);
        // An unexpanded placeholder is worse than nothing: it would go to
        // the server verbatim. Returning "" keeps the existing broken-path
        // guard reporting the real problem.
        return (expanded.indexOf('#') >= 0 || expanded.indexOf('@') >= 0)
                ? "" : expanded;
    }

    /**
     * Expand {@code #key#} / {@code @key@} in a datasheet cell.
     *
     * <p>Needed because the cell can itself be a reference:
     * {@code DataSource_propCode} is literally
     * {@code #generatedDatesAndProps_hcrs#} when the workbook behind that
     * DataSource was not imported. ctx is tried first for each reference
     * (through the alias-walking {@code ctxGet}), then the row under either
     * spelling. Bounded to 5 passes so a cell referring to itself
     * terminates instead of hanging the run.</p>
     */
    private static String expandCell(java.util.Map<String, String> ctx,
                                     java.util.Map<String, String> row,
                                     String value) {
        if (value == null || value.isEmpty()) {
            return value == null ? "" : value;
        }
        if (value.indexOf('#') < 0 && value.indexOf('@') < 0) {
            return value;
        }
        String cur = value;
        for (int pass = 0; pass < 5; pass++) {
            String next = expandOnce(ctx, row, cur);
            if (next.equals(cur)) {
                return next;
            }
            cur = next;
        }
        return cur;
    }

    private static final java.util.regex.Pattern CELL_REF =
            java.util.regex.Pattern.compile("[#@]([A-Za-z0-9_.\\-]+)[#@]");

    private static String expandOnce(java.util.Map<String, String> ctx,
                                     java.util.Map<String, String> row,
                                     String text) {
        java.util.regex.Matcher m = CELL_REF.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {
            String k = m.group(1);
            String v = ImportedScenario.ctxGet(ctx, k);
            // A cell reference is spelled `#Step_field#`; the live ctx key the
            // Groovy published is `Step.field`. ctxGet does not alias the two,
            // so the lookup missed and fell to the ROW -- which also carries
            // the Properties step's saved snapshot of the same field. The
            // availability search picked LONME and the path went to MILHI.
            // Try the dotted spellings in ctx BEFORE the row.
            if ((v == null || v.isEmpty()) && k.indexOf('_') >= 0) {
                int last = k.lastIndexOf('_');
                v = ImportedScenario.ctxGet(ctx, k.substring(0, last) + "." + k.substring(last + 1));
                if (v == null || v.isEmpty()) {
                    v = ImportedScenario.ctxGet(ctx, k.replace('_', '.'));
                }
            }
            if (v == null || v.isEmpty()) {
                v = row == null ? null : row.get(k);
            }
            if ((v == null || v.isEmpty()) && row != null) {
                v = row.get(k.replace('.', '_'));
            }
            if ((v == null || v.isEmpty()) && row != null) {
                v = row.get(k.replace('_', '.'));
            }
            // Leave an unresolved reference in place so the caller can see
            // it did not resolve and return "" rather than half a value.
            m.appendReplacement(out, java.util.regex.Matcher.quoteReplacement(
                    (v == null || v.isEmpty()) ? m.group() : v));
        }
        m.appendTail(out);
        return out.toString();
    }

}
