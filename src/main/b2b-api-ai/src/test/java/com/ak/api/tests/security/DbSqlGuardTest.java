package com.ak.api.tests.security;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.ak.api.db.Db;

import io.qameta.allure.Description;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Locks {@link Db#unsafeSqlReason(String)} -- the gate that stops a broken
 * Groovy-to-SQL translation from reaching the driver.
 *
 * <p>A malformed statement must be REFUSED with a reason naming the fault.
 * The failure mode being prevented is not a crash: it is a DELETE whose
 * unterminated literal swallows the rest of the statement, matches nothing,
 * and passes silently, leaving the suite to fail later somewhere unrelated.</p>
 *
 * <p>Pure unit tests -- no database, no network.</p>
 */
@Feature("Db SQL guards")
public class DbSqlGuardTest {

    @Test(groups = {"unit", "guards"})
    @Story("Empty and whitespace-only SQL is refused")
    public void emptyAndBlank_areRefused() {
        Assert.assertNotNull(Db.unsafeSqlReason(null));
        Assert.assertNotNull(Db.unsafeSqlReason(""));
        Assert.assertNotNull(Db.unsafeSqlReason("   "), "whitespace-only must be refused");
        Assert.assertNotNull(Db.unsafeSqlReason("\n\t "), "newline/tab-only must be refused");
    }

    @Test(groups = {"unit", "guards"})
    @Story("Quote-only / punctuation-only SQL is refused")
    @Description("Produced when the placeholder carrying the whole statement "
            + "resolves to an empty string.")
    public void quoteOnly_isRefused() {
        Assert.assertNotNull(Db.unsafeSqlReason("''"));
        Assert.assertNotNull(Db.unsafeSqlReason("''''"));
        Assert.assertNotNull(Db.unsafeSqlReason("()"));
        Assert.assertNotNull(Db.unsafeSqlReason("';"));
    }

    @Test(groups = {"unit", "guards"})
    @Story("Unbalanced single quotes are refused")
    @Description("""
            The real occurrence in this corpus -- the final literal never
            closes, so the driver swallows the closing paren into the string:
              DELETE FROM account WHERE web_site IN ('a.com', 'www.laafd.com)
            """)
    public void unbalancedQuotes_areRefused() {
        String broken = "DELETE FROM account WHERE web_site IN ('a.com', 'www.laafd.com)";
        String reason = Db.unsafeSqlReason(broken);
        Assert.assertNotNull(reason, "unterminated literal must be refused");
        Assert.assertTrue(reason.toLowerCase().contains("quote"),
                "reason should name the quote fault, was: " + reason);
    }

    @Test(groups = {"unit", "guards"})
    @Story("Unbalanced parentheses are refused")
    public void unbalancedParens_areRefused() {
        Assert.assertNotNull(Db.unsafeSqlReason(
                "DELETE FROM account WHERE id IN (1, 2"));
    }

    @Test(groups = {"unit", "guards"})
    @Story("Well-formed SQL still passes")
    @Description("The guard must not become so strict that valid statements "
            + "are refused -- that would silently skip real cleanup.")
    public void wellFormedSql_passes() {
        Assert.assertNull(Db.unsafeSqlReason(
                "DELETE FROM account WHERE web_site IN ('a.com', 'b.com')"));
        Assert.assertNull(Db.unsafeSqlReason(
                "UPDATE account_member SET email_address = 'x@y.com' WHERE id = 42"));
        // Doubled quote is SQL's own escape -- 4 quotes, still balanced.
        Assert.assertNull(Db.unsafeSqlReason(
                "UPDATE t SET name = 'it''s fine' WHERE id = 1"));
        Assert.assertNull(Db.unsafeSqlReason("DELETE FROM account"));
    }

    @Test(groups = {"unit", "guards"})
    @Story("Pre-existing guards still hold")
    @Description("Regression cover for the checks that existed before the "
            + "quote/paren/blank additions.")
    public void existingGuards_stillHold() {
        Assert.assertNotNull(Db.unsafeSqlReason(
                "DELETE FROM account WHERE d = ${#Project#DOMAIN}"),
                "untranslated SoapUI ref");
        Assert.assertNotNull(Db.unsafeSqlReason("SELECT * FROM account"),
                "SELECT belongs in queryAll, not execute");
        Assert.assertNotNull(Db.unsafeSqlReason(
                "DELETE FROM account WHERE web_site IN ()"),
                "empty IN-list");
    }
}
