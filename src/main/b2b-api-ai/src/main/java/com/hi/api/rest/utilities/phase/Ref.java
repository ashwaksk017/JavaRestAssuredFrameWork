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

    @Override
    public String toString() {
        return describe();
    }

    /** A ctx key, read through the alias-walking {@code ImportedScenario.ctxGet}. */
    public static Ref ctx(String key) {
        return new Ref() {
            @Override
            public String resolve(PhaseContext c) {
                String v = ImportedScenario.ctxGet(c.ctx, key);
                return v == null ? "" : v;
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
}
