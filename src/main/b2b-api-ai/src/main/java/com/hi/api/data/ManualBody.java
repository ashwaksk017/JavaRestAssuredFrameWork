package com.hi.api.data;

import java.io.BufferedReader;
import java.io.Reader;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import com.hi.api.rest.utilities.RestUtilities;

/**
 * Render a hand-written JSON body from a template under
 * {@code src/test/resources/templates/manual/}.
 *
 * <pre>{@code
 * String body = ManualBody.render("templates/manual/activate.json", row, ctx);
 * Response res = RestUtilities.getResponsePost(body, url, headers);
 * }</pre>
 *
 * <h2>Why this exists rather than a documented three-line incantation</h2>
 *
 * A raw {@code mapJsonValues(getRequestTemplate(p), row)} -- the obvious
 * call, and what an author writes first -- is wrong in two ways that both
 * fail quietly:
 *
 * <ul>
 *   <li>{@code <<faker>>} and {@code ${ref}} tokens in the template body
 *       are NOT resolved. They are resolved in CSV <em>cells</em>, by
 *       {@link PlaceholderResolver#resolveRow}. The generated engine gets
 *       away with putting them in a body because {@code RestStep} wraps
 *       {@code mapJsonValues} with {@link PlaceholderResolver#resolveAll}
 *       on both sides; nothing wraps a hand-written call. Measured: the
 *       token goes to the server verbatim, inside the quotes.</li>
 *   <li>a {@code #key#} with no matching column renders the four-character
 *       STRING {@code "null"}, not JSON null. {@code RestStep} carries an
 *       explicit workaround for exactly this -- "B2B-3056 attest sent
 *       travelAgentId=null and H4B 400'd" -- and a hand-written call has
 *       no such guard.</li>
 * </ul>
 *
 * So this does what the engine does, and is STRICT: an unresolved
 * placeholder throws, naming the key, instead of sending `"null"` and
 * letting the server answer with a complaint about some other field.
 */
public final class ManualBody {

    private ManualBody() {}

    /**
     * @param classpathTemplate e.g. {@code "templates/manual/activate.json"}
     *                          -- the path with {@code src/test/resources/}
     *                          dropped
     * @param row               the data-provider row
     * @param ctx               per-test context; {@code ${ref}}s resolve
     *                          through it, so two fields naming the same
     *                          ref agree, and values an earlier call
     *                          captured are visible by name
     * @throws IllegalStateException when the template is not on the
     *         classpath, listing where a hand-written body belongs
     */
    public static String render(String classpathTemplate,
                                Map<String, String> row,
                                Map<String, String> ctx) throws Exception {
        String raw = read(classpathTemplate);
        // The converted path's order (ImportedScenario.mergedRow): the
        // configuration under the placeholder's name and its alias forms,
        // then the data sheet's row over it, then the values this test
        // captured or generated (ctx) over both. Before, only the row was
        // asked: a #name# the configuration or an earlier call had was
        // "unresolved" here and resolved in a converted test.
        Map<String, String> resolvedRow = PlaceholderResolver.resolveRow(row, ctx);
        Map<String, String> data = new LinkedHashMap<>();
        Map<String, String> origins = new HashMap<>();
        Matcher m = HASH_NAME.matcher(raw);
        while (m.find()) {
            String name = m.group(1);
            if (present(resolvedRow.get(name)) || (ctx != null && present(ctx.get(name)))) {
                continue;
            }
            String[] cfg = ValueSources.fromConfig(name);
            if (cfg != null) {
                data.put(name, cfg[0]);
                origins.put(name, "program_configuration.json (" + cfg[1] + ")");
                continue;
            }
            // Last: what the converted suites' bodies send for the same JSON
            // field -- a field they always send with a working value is not
            // left out or invented by a hand-written body. Said once a field.
            String field = fieldOf(raw, name);
            String[] conv = ValueSources.convertedLiteral(field);
            if (conv != null) {
                data.put(name, conv[0]);
                origins.put(name, "the converted suites' bodies (" + field + ": " + conv[1] + ")");
                if (WARNED_FIELDS.add(field)) {
                    org.slf4j.LoggerFactory.getLogger(ManualBody.class).warn(
                            "#{}# ({}) is not in the data sheet, the test's values or program_configuration.json: "
                                    + "using '{}', what the converted suites send ({}). Set {} in "
                                    + "program_configuration.json -- Settings tab -- to choose another",
                            name, field, conv[0], conv[1], name);
                }
            }
        }
        data.putAll(resolvedRow);
        if (ctx != null) {
            for (Map.Entry<String, String> e : ctx.entrySet()) {
                if (present(e.getValue()) && !PayloadProvenance.isReserved(e.getKey())) {
                    if (!present(resolvedRow.get(e.getKey()))) {
                        origins.put(e.getKey(), "a value this test captured or generated");
                    }
                    data.put(e.getKey(), e.getValue());
                }
            }
        }
        PayloadProvenance.noteOrigins(data, origins);
        // Same order as RestStep: tokens first, then columns, then any
        // token a column's value itself carried.
        String tokensFirst = PlaceholderResolver.resolveAll(raw, ctx);
        String mapped = RestUtilities.mapJsonValues(tokensFirst, data, true);
        String out = PlaceholderResolver.resolveAll(mapped, ctx);
        // A ${ref} or <<generator>> nothing knew stays in the text verbatim:
        // the server would get it literally. Said now, by name -- as the
        // strict #name# check does, so RetryAnalyzer does not retry it.
        Matcher left = LEFTOVER.matcher(out);
        if (left.find()) {
            throw new RestUtilities.UnresolvedPlaceholderException("template '" + classpathTemplate + "': " + left.group()
                    + " has no value -- not in the data sheet, not captured earlier in the test, not in "
                    + "program_configuration.json, and not a known generator (<<email>>, <<firstname>>, ...)");
        }
        return out;
    }

    private static final Pattern HASH_NAME = Pattern.compile("#([A-Za-z0-9_.\\-]+)#");
    private static final Pattern LEFTOVER = Pattern.compile("\\$\\{[^}]+\\}|<<[^<>]+>>");

    private static final java.util.Set<String> WARNED_FIELDS = java.util.concurrent.ConcurrentHashMap.newKeySet();

    /** The JSON field a {@code "#name#"} is the value of, or null (an array item, a part of a string). */
    static String fieldOf(String template, String name) {
        Matcher m = Pattern.compile("\"([A-Za-z0-9_]+)\"\\s*:\\s*\"#" + Pattern.quote(name) + "#\"").matcher(template);
        return m.find() ? m.group(1) : null;
    }

    private static boolean present(String v) {
        return v != null && !v.trim().isEmpty();
    }

    /** The template text, or a message saying where it should have been. */
    public static String read(String classpathTemplate) throws Exception {
        Reader r = RestUtilities.getRequestTemplate(classpathTemplate);
        if (r == null) {
            throw new IllegalStateException(
                    "template '" + classpathTemplate + "' is not on the "
                    + "classpath. A hand-written body belongs under "
                    + "src/test/resources/templates/manual/ -- "
                    + "src/main/resources/templates/<suite>/ is converter "
                    + "output: it is gitignored and --clean deletes it.");
        }
        try (BufferedReader br = new BufferedReader(r)) {
            return br.lines().collect(Collectors.joining("\n"));
        }
    }
}
