package com.hi.api.tests.framework;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.data.ManualBody;
import com.hi.api.data.PlaceholderResolver;
import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.dsl.Template;

/**
 * The worked example MANUAL_FIXES.md section 5 documents, executed.
 *
 * <p>A documented snippet that nothing runs is a snippet that rots. This
 * renders `templates/manual/example_request.json` exactly the way the doc
 * tells an author to, and pins what comes out -- so the delimiter-to-JSON
 * -type contract, the classpath path, and the two-stage resolve order
 * cannot drift away from the instructions without failing the build.</p>
 *
 * <p>No HTTP, no DB. It builds a body and inspects the string.</p>
 */
public class ManualTemplateExampleTest {

    private static final String TEMPLATE = "templates/manual/example_request.json";

    /** The row a data provider would hand the test. */
    private static Map<String, String> row() {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("test_case_id", "DOC-1_render_template");
        row.put("Properties_accountId", "12345");
        row.put("Properties_Email", "someone@example.com");
        row.put("Properties_employeeCount", "33");
        row.put("Properties_selfManaged", "true");
        return row;
    }

    /** Exactly the one line the doc prescribes. */
    private static String render(Map<String, String> row) throws Exception {
        return ManualBody.render(TEMPLATE, row, new LinkedHashMap<>());
    }

    /** The naive call an author writes first, kept to pin why it is wrong. */
    private static String renderNaively(Map<String, String> row) throws Exception {
        Map<String, String> ctx = new LinkedHashMap<>();
        Map<String, String> data = PlaceholderResolver.resolveRow(row, ctx);
        return RestUtilities.mapJsonValues(
                RestUtilities.getRequestTemplate(TEMPLATE), data);
    }

    @Test(groups = {"guards"})
    public void theTemplateIsOnTheClasspathWhereTheDocSaysItIs() {
        // Template.ofPath validates the resource and tells an author off
        // for putting a body under src/main/resources/templates/<suite>/.
        Assert.assertEquals(
                Template.ofPath("doc example", TEMPLATE).resolve(), TEMPLATE);
    }

    @Test(groups = {"guards"})
    public void hashDelimiterProducesAQuotedString() throws Exception {
        Assert.assertTrue(render(row()).contains("\"accountId\": \"12345\""),
                render(row()));
    }

    @Test(groups = {"guards"})
    public void atDelimiterProducesANumberAndEatsTheQuotes() throws Exception {
        String body = render(row());
        Assert.assertTrue(body.contains("\"employeeCount\": 33"), body);
        Assert.assertFalse(body.contains("\"employeeCount\": \"33\""), body);
    }

    @Test(groups = {"guards"})
    public void percentDelimiterProducesABooleanAndEatsTheQuotes() throws Exception {
        String body = render(row());
        Assert.assertTrue(body.contains("\"selfManaged\": true"), body);
        Assert.assertFalse(body.contains("\"selfManaged\": \"true\""), body);
    }

    @Test(groups = {"guards"})
    public void aLiteralInTheTemplateIsLeftAlone() throws Exception {
        Assert.assertTrue(
                render(row()).contains("\"activationSource\": \"leadspace\""));
    }

    @Test(groups = {"guards"})
    public void aFakerTokenInTheTemplateIsResolvedByTheHelper() throws Exception {
        String body = render(row());
        Assert.assertFalse(body.contains("<<digits(6)>>"), body);
        Assert.assertTrue(body.matches("(?s).*\"freshEachTime\": \"\\d{6}\".*"), body);
    }

    @Test(groups = {"guards"})
    public void theNaiveCallLeavesAFakerTokenInTheBody() throws Exception {
        // MEASURED, and the whole reason ManualBody exists: a raw
        // mapJsonValues call sends `<<digits(6)>>` to the server inside
        // the quotes. The generated engine escapes this only because
        // RestStep wraps mapJsonValues with resolveAll on both sides.
        Assert.assertTrue(renderNaively(row()).contains("<<digits(6)>>"),
                "if this now fails, mapJsonValues learned to resolve faker "
                        + "tokens and MANUAL_FIXES.md section 5 should say so");
    }

    @Test(groups = {"guards"})
    public void aDollarRefIsStableWithinOneRowAndDiffersAcrossRows()
            throws Exception {
        // `${username}` resolves once per row via ctx, so two fields in the
        // SAME row agree -- which is the whole reason the form exists.
        Map<String, String> ctx = new LinkedHashMap<>();
        Map<String, String> r = row();
        r.put("a", "${username}");
        r.put("b", "${username}");
        Map<String, String> data = PlaceholderResolver.resolveRow(r, ctx);
        Assert.assertEquals(data.get("a"), data.get("b"));
        Assert.assertFalse(data.get("a").contains("${"), data.get("a"));

        Map<String, String> other = PlaceholderResolver.resolveRow(row(),
                new LinkedHashMap<>());
        Assert.assertNotEquals(data.get("a"), other.get("a"),
                "a fresh ctx must mint a new identity, or two rows collide");
    }

    @Test(groups = {"guards"})
    public void resolveRowDoesNotMutateTheProviderRow() throws Exception {
        // The DataProvider array is reused on retry. Mutating it would make
        // a retried row see values minted for the first attempt.
        Map<String, String> r = row();
        r.put("minted", "<<digits(4)>>");
        PlaceholderResolver.resolveRow(r, new LinkedHashMap<>());
        Assert.assertEquals(r.get("minted"), "<<digits(4)>>");
    }

    @Test(groups = {"guards"})
    public void aMissingColumnThrowsRatherThanSendingTheWordNull()
            throws Exception {
        Map<String, String> r = row();
        r.remove("Properties_accountId");
        try {
            String body = ManualBody.render(TEMPLATE, r, new LinkedHashMap<>());
            Assert.fail("a missing column must not render silently: " + body);
        } catch (Exception expected) {
            Assert.assertTrue(
                    String.valueOf(expected.getMessage())
                            .contains("Properties_accountId"),
                    "the exception must NAME the key: " + expected.getMessage());
        }
    }

    @Test(groups = {"guards"})
    public void theNaiveCallSendsTheFourCharacterStringNull() throws Exception {
        // MEASURED. Not JSON null -- the STRING "null". RestStep carries an
        // explicit workaround for this ("B2B-3056 attest sent
        // travelAgentId=null and H4B 400'd"); a hand-written call has none.
        Map<String, String> r = row();
        r.remove("Properties_accountId");
        Assert.assertTrue(renderNaively(r).contains("\"accountId\": \"null\""),
                renderNaively(r));
    }

    @Test(groups = {"guards"})
    public void aDottedCsvColumnDoesNotFeedAnUnderscorePlaceholder()
            throws Exception {
        // MEASURED, and the reason the doc says to use UNDERSCORES in a
        // hand-written row file. The dot-to-underscore aliasing lives in
        // the generated per-suite TestSupport.mergedRow, which a manual
        // test must not import -- so `Properties.accountId` does NOT
        // satisfy `#Properties_accountId#` on this path. Strict rendering
        // turns that into an immediate, named failure instead of a body
        // carrying the word "null".
        Map<String, String> r = row();
        r.remove("Properties_accountId");
        r.put("Properties.accountId", "12345");
        try {
            String body = ManualBody.render(TEMPLATE, r, new LinkedHashMap<>());
            Assert.fail("if this now renders, mergedRow-style aliasing reached "
                    + "this path and MANUAL_FIXES.md section 5 should say so: "
                    + body);
        } catch (Exception expected) {
            Assert.assertTrue(
                    String.valueOf(expected.getMessage())
                            .contains("Properties_accountId"),
                    expected.getMessage());
        }
    }
}
