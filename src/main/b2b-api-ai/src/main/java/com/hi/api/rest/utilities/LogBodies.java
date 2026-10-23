package com.hi.api.rest.utilities;

import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * A request or response body as the run log shows it: JSON pretty-printed,
 * on its own indented lines under the line that names it, so a 40-field
 * body is read field by field instead of as one 4,000-character line.
 * Anything that is not JSON (XML, a form, plain text) keeps its own line
 * breaks, indented the same way.
 *
 * <p>Redact BEFORE calling this: it only lays the text out. Never throws --
 * a body it cannot parse is shown as it came.</p>
 */
public final class LogBodies {

    private static final ObjectMapper JSON = new ObjectMapper();
    /** Under "  sent    :" / " .. response body": far enough in to read as part of that line. */
    static final String PAD = "      ";

    private LogBodies() {
    }

    /**
     * The body on its own lines, starting with a line break, each line
     * indented; JSON pretty-printed. {@code max} caps the laid-out text
     * ({@code <= 0}: no cap), saying how much was left out. Empty input: "".
     */
    public static String pretty(String body, int max) {
        if (body == null || body.trim().isEmpty()) {
            return "";
        }
        String t = body.trim();
        String out = t;
        if (t.startsWith("{") || t.startsWith("[")) {
            try {
                out = JSON.writerWithDefaultPrettyPrinter().writeValueAsString(JSON.readTree(t));
            } catch (Exception | StackOverflowError notJson) {
                out = t;
            }
        }
        out = out.replace("\r\n", "\n");
        if (max > 0 && out.length() > max) {
            out = out.substring(0, max) + "\n... (" + (out.length() - max) + " more characters)";
        }
        return "\n" + PAD + out.replace("\n", "\n" + PAD);
    }
}
