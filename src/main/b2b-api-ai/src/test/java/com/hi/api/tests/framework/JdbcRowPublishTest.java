package com.hi.api.tests.framework;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * A JDBC step's result columns have to be readable.
 *
 * <p>{@code ${STEP#ResponseAsXml#//Results[1]/ResultSet[1]/Row[1]/TABLE.COLUMN[1]}}
 * is how these projects pull a value out of a query, and the converter
 * translates it to {@code #STEP_Response_TABLE_COLUMN#} correctly. What
 * was missing was the other end: the emitted step ran the query, logged
 * the row count, and dropped the rows -- so the placeholder had no
 * producer and the request carried the literal {@code null}. A value
 * fetched and thrown away one line earlier.</p>
 *
 * <p>Both imported suites hit this, on a rate-plan code and on a one-time
 * passcode.</p>
 */
@Epic("Framework")
@Feature("Key resolution")
public class JdbcRowPublishTest {

    private static List<Map<String, Object>> rows(Object... kv) {
        Map<String, Object> r = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            r.put((String) kv[i], kv[i + 1]);
        }
        List<Map<String, Object>> out = new ArrayList<>();
        out.add(r);
        return out;
    }

    @Test(groups = {"unit", "framework"})
    @Story("a column is readable under step_Response_TABLE_COLUMN")
    @Description("""
            The spelling ReadyAPI actually uses: it names the result node
            after the table, uppercased. Verified against both suites --
            GOALRATEPLANS.SRP_CODE and
            ACCOUNT_MEMBER_INTERNAL_SECURITY.EMAIL_OTP.
            """)
    public void theTableQualifiedSpellingResolves() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ImportedScenario.publishJdbcRow(ctx, "Postgres", "GOALRATEPLANS",
                rows("srp_code", "ABC123"));

        Assert.assertEquals(ctx.get("Postgres_Response_GOALRATEPLANS_SRP_CODE"),
                "ABC123");
    }

    @Test(groups = {"unit", "framework"})
    @Story("the bare column spelling resolves too")
    public void theBareColumnSpellingResolves() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ImportedScenario.publishJdbcRow(ctx, "Postgres", "GOALRATEPLANS",
                rows("srp_code", "ABC123"));

        Assert.assertEquals(ctx.get("Postgres_Response_SRP_CODE"), "ABC123");
    }

    @Test(groups = {"unit", "framework"})
    @Story("an existing ctx value is never overwritten")
    @Description("""
            The whole safety argument. This may only ADD a reading for a
            key that held nothing -- so the only behaviour that changes is
            a placeholder that was becoming `null`, and nothing that
            resolves today resolves differently.
            """)
    public void anExistingValueIsNeverOverwritten() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("Postgres_Response_SRP_CODE", "already-here");
        ctx.put("Postgres_Response_GOALRATEPLANS_SRP_CODE", "also-here");

        ImportedScenario.publishJdbcRow(ctx, "Postgres", "GOALRATEPLANS",
                rows("srp_code", "from-db"));

        Assert.assertEquals(ctx.get("Postgres_Response_SRP_CODE"),
                "already-here");
        Assert.assertEquals(
                ctx.get("Postgres_Response_GOALRATEPLANS_SRP_CODE"),
                "also-here");
    }

    @Test(groups = {"unit", "framework"})
    @Story("an empty ctx value is treated as absent")
    public void anEmptyValueIsFilled() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("Postgres_Response_SRP_CODE", "");

        ImportedScenario.publishJdbcRow(ctx, "Postgres", "", rows("srp_code", "v"));

        Assert.assertEquals(ctx.get("Postgres_Response_SRP_CODE"), "v");
    }

    @Test(groups = {"unit", "framework"})
    @Story("a step with no derivable table still publishes its columns")
    @Description("""
            One bundled JDBC step has an empty query, and a join has no
            single right table. A miss there must cost an alias, not the
            value.
            """)
    public void noTableStillPublishesTheBareColumn() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ImportedScenario.publishJdbcRow(ctx, "S", "", rows("col", "v"));

        Assert.assertEquals(ctx.get("S_Response_COL"), "v");
        Assert.assertFalse(ctx.containsKey("S_Response__COL"), ctx.toString());
    }

    @Test(groups = {"unit", "framework"})
    @Story("only the first row is published")
    @Description("ReadyAPI's reference names Row[1]; later rows are not it.")
    public void onlyTheFirstRowIsPublished() {
        Map<String, String> ctx = new LinkedHashMap<>();
        List<Map<String, Object>> two = rows("col", "first");
        Map<String, Object> second = new LinkedHashMap<>();
        second.put("col", "second");
        two.add(second);

        ImportedScenario.publishJdbcRow(ctx, "S", "", two);

        Assert.assertEquals(ctx.get("S_Response_COL"), "first");
    }

    @Test(groups = {"unit", "framework"})
    @Story("a null column value is skipped, not written as \"null\"")
    @Description("""
            Writing the four characters `null` is the bug this exists to
            remove; reproducing it here would be a poor joke.
            """)
    public void aNullColumnIsSkipped() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ImportedScenario.publishJdbcRow(ctx, "S", "", rows("col", null));

        Assert.assertFalse(ctx.containsKey("S_Response_COL"), ctx.toString());
    }

    @Test(groups = {"unit", "framework"})
    @Story("a non-string column is rendered, not dropped")
    public void aNumericColumnIsPublished() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ImportedScenario.publishJdbcRow(ctx, "S", "", rows("n", 42));

        Assert.assertEquals(ctx.get("S_Response_N"), "42");
    }

    @Test(groups = {"unit", "framework"})
    @Story("no input shape can throw")
    @Description("A plumbing aid must never be the reason a suite fails.")
    public void badInputIsSafe() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ImportedScenario.publishJdbcRow(null, "S", "T", rows("c", "v"));
        ImportedScenario.publishJdbcRow(ctx, null, "T", rows("c", "v"));
        ImportedScenario.publishJdbcRow(ctx, "S", "T", null);
        ImportedScenario.publishJdbcRow(ctx, "S", "T", new ArrayList<>());
        List<Map<String, Object>> nullRow = new ArrayList<>();
        nullRow.add(null);
        ImportedScenario.publishJdbcRow(ctx, "S", "T", nullRow);

        Assert.assertTrue(ctx.isEmpty(), ctx.toString());
    }
}
