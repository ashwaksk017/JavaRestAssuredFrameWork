package com.hi.api.rest.utilities;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * A template that is nothing but one placeholder carries a WHOLE payload.
 *
 * <p>ReadyAPI cases that keep their request in a DataSource cell convert
 * to a template file holding exactly {@code #DataSource_Requestbody#}.
 * `mapJsonValues` JSON-string-escapes substituted values, which is right
 * for {@code "field": "#x#"} and wrong here: it turned the body into a
 * JSON string literal, so the server received
 * {@code  &#123;\n  \"startDate\": ...} and answered
 * {@code 998 Invalid Parameter Value / JSON Parse Error} at
 * {@code Line:1;Column:2} -- column 2 being the backslash. 108 of those
 * in one run, invisible until the loopback-routing fix let the call
 * reach a server at all.</p>
 */
@Feature("Readable-test utilities")
public class WholeBodyPlaceholderTest {

    private static final String BODY =
            "{\n  \"startDate\": \"2026-11-08\",\n  \"peakRooms\": \"10\"\n}";

    // Built from chars on purpose: a literal "\\n" in source is one editing
    // slip away from meaning a real newline, and that slip makes this test
    // assert the opposite of what it claims.
    private static final String ESCAPED_NEWLINE = String.valueOf('\\') + 'n';
    private static final String ESCAPED_QUOTE = String.valueOf('\\') + '"';

    private static Map<String, String> row(String k, String v) {
        Map<String, String> m = new LinkedHashMap<>();
        m.put(k, v);
        return m;
    }

    @Test(groups = {"unit"})
    @Story("A whole-body placeholder passes the payload through unescaped")
    public void aWholeBodyPlaceholderIsNotEscaped() throws Exception {
        String out = RestUtilities.mapJsonValues(
                "#DataSource_Requestbody#", row("DataSource_Requestbody", BODY));
        Assert.assertEquals(out, BODY,
                "the payload must arrive byte-identical; escaping it makes the "
                + "server reject the body at Line:1;Column:2");
        Assert.assertFalse(out.contains(ESCAPED_QUOTE),
                "no escaped quotes: " + out);
        Assert.assertFalse(out.contains(ESCAPED_NEWLINE),
                "no escaped newlines: " + out);
    }

    @Test(groups = {"unit"})
    @Story("The converter writes the token on its own line, so trailing newline counts")
    public void surroundingWhitespaceStillCountsAsWholeTemplate() throws Exception {
        String out = RestUtilities.mapJsonValues(
                "#DataSource_Requestbody#\n", row("DataSource_Requestbody", BODY));
        Assert.assertEquals(out.trim(), BODY.trim(), out);
    }

    @Test(groups = {"unit"})
    @Story("A placeholder INSIDE a JSON string is still escaped")
    public void aFieldPlaceholderIsStillEscaped() throws Exception {
        // This is the case jsonEscape exists for: a value with a quote in it
        // would otherwise break out of its own string and corrupt the body.
        String out = RestUtilities.mapJsonValues(
                "{\"name\": \"#n#\"}", row("n", "say \"hi\""));
        Assert.assertTrue(out.contains(ESCAPED_QUOTE),
                "a quote inside a JSON string field MUST stay escaped: " + out);
    }

    @Test(groups = {"unit"})
    @Story("Two tokens, or a token with JSON around it, are not whole-template")
    public void onlyASingleBareTokenQualifies() {
        Assert.assertTrue(
                RestUtilities.isWholeTemplateOnePlaceholder("#DataSource_Requestbody#"));
        Assert.assertTrue(
                RestUtilities.isWholeTemplateOnePlaceholder("  #a.b-c_1#\n"));
        Assert.assertFalse(RestUtilities.isWholeTemplateOnePlaceholder("#a##b#"));
        Assert.assertFalse(RestUtilities.isWholeTemplateOnePlaceholder("{\"a\":\"#a#\"}"));
        Assert.assertFalse(RestUtilities.isWholeTemplateOnePlaceholder("##"));
        Assert.assertFalse(RestUtilities.isWholeTemplateOnePlaceholder("#"));
        Assert.assertFalse(RestUtilities.isWholeTemplateOnePlaceholder(""));
        Assert.assertFalse(RestUtilities.isWholeTemplateOnePlaceholder(null));
        // a token containing JSON punctuation is not a placeholder NAME
        Assert.assertFalse(
                RestUtilities.isWholeTemplateOnePlaceholder("#{\"a\":1}#"));
    }
}
