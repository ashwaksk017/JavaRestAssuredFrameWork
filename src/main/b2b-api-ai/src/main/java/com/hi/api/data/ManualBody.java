package com.hi.api.data;

import java.io.BufferedReader;
import java.io.Reader;
import java.util.Map;
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
        Map<String, String> data = PlaceholderResolver.resolveRow(row, ctx);
        // Same order as RestStep: tokens first, then columns, then any
        // token a column's value itself carried.
        String tokensFirst = PlaceholderResolver.resolveAll(raw, ctx);
        String mapped = RestUtilities.mapJsonValues(tokensFirst, data, true);
        return PlaceholderResolver.resolveAll(mapped, ctx);
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
