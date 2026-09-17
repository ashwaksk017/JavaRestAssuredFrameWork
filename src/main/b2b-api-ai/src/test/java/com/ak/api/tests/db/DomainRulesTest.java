package com.ak.api.tests.db;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.testng.annotations.AfterClass;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import static org.testng.Assert.assertEquals;
import static org.testng.Assert.assertNotEquals;
import static org.testng.Assert.assertNull;
import static org.testng.Assert.assertTrue;

import com.ak.api.db.Db;
import com.ak.api.db.repo.DomainRules;

import io.qameta.allure.Description;
import io.qameta.allure.Feature;

/**
 * Contract for {@link DomainRules}: live account_rules domains replacing the
 * frozen CSV snapshot for Properties.RandomDomain / RandomDomain2.
 *
 * <p>Runs against in-memory H2 in PostgreSQL mode. NOTE the database name is
 * deliberately NOT the one {@code AccountRulesDomainsTest} uses -- that class
 * drops {@code account_rules} in its {@code @AfterClass}, and both run in the
 * same JVM in the guards suite, so a shared name would let one class delete
 * the other's fixture depending on ordering.</p>
 *
 * <p>{@code NON_KEYWORDS=VALUE} because {@code value} is reserved in H2 but
 * not in Postgres; quoting it here would mean the test no longer exercises
 * the exact SQL production sends.</p>
 */
@Feature("account_rules domain sourcing")
public class DomainRulesTest {

    private static final String H2_URL =
            "jdbc:h2:mem:ak_domainrules;MODE=PostgreSQL;DB_CLOSE_DELAY=-1;NON_KEYWORDS=VALUE";
    private static final String H2_USER = "sa";
    private static final String H2_PASS = "sa";

    @BeforeClass(alwaysRun = true)
    public void bootstrapSchema() {
        Db h2 = Db.using(H2_URL, H2_USER, H2_PASS);
        h2.executeStatement("DROP TABLE IF EXISTS account_rules");
        h2.executeStatement(
                "CREATE TABLE account_rules ("
                        + "rule_type VARCHAR(64), "
                        + "value VARCHAR(255), "
                        + "reason VARCHAR(64))");
        h2.executeStatement("INSERT INTO account_rules (rule_type, value, reason) "
                + "VALUES ('account_blacklist', ?, ?)", "managed-one.com", "managed_domain");
        h2.executeStatement("INSERT INTO account_rules (rule_type, value, reason) "
                + "VALUES ('account_blacklist', ?, ?)", "managed-two.com", "managed_domain");
        h2.executeStatement("INSERT INTO account_rules (rule_type, value, reason) "
                + "VALUES ('account_blacklist', ?, ?)", "managed-three.com", "managed_domain");
        h2.executeStatement("INSERT INTO account_rules (rule_type, value, reason) "
                + "VALUES ('account_blacklist', ?, ?)", "rival.com", "competitor_domain");
    }

    @AfterClass(alwaysRun = true)
    public void dropSchema() {
        Db.using(H2_URL, H2_USER, H2_PASS).executeStatement("DROP TABLE IF EXISTS account_rules");
    }

    @AfterMethod(alwaysRun = true)
    public void clearState() {
        System.clearProperty("db.url");
        System.clearProperty("db.user");
        System.clearProperty("db.password");
        System.clearProperty(DomainRules.ENABLED_KEY);
        DomainRules.resetForTest();
    }

    private void pointAtH2() {
        System.setProperty("db.url", H2_URL);
        System.setProperty("db.user", H2_USER);
        System.setProperty("db.password", H2_PASS);
    }

    private void enable() {
        System.setProperty(DomainRules.ENABLED_KEY, "true");
    }

    private static Map<String, String> row(String testCaseId) {
        Map<String, String> r = new HashMap<>();
        r.put("test_case_id", testCaseId);
        return r;
    }

    // ---------------------------------------------------------------
    // Reason resolution
    // ---------------------------------------------------------------

    @Test(groups = {"unit"})
    @Description("Case ids naming a reason unambiguously resolve to it.")
    public void reasonIsInferredFromTheCaseId() {
        assertEquals(DomainRules.reasonForRow(
                row("B2B-6324_Verify_with_managed_domain_400")), "managed_domain");
        assertEquals(DomainRules.reasonForRow(
                row("B2B-6324_AddCompetitorDomain")), "competitor_domain");
        assertEquals(DomainRules.reasonForRow(
                row("B2B-6238_add_freemail_domain_400")), "freemail_domain");
    }

    @Test(groups = {"unit"})
    @Description("An explicit randomDomain.reason column beats inference.")
    public void explicitReasonColumnWins() {
        Map<String, String> r = row("B2B-6324_Verify_with_managed_domain_400");
        r.put(DomainRules.REASON_COLUMN, "freemail_domain");
        assertEquals(DomainRules.reasonForRow(r), "freemail_domain");
    }

    @Test(groups = {"unit"})
    @Description("Ambiguous names (blocklist / freelist) resolve to nothing "
            + "rather than guessing, so the CSV value stands.")
    public void ambiguousNamesResolveToNull() {
        assertNull(DomainRules.reasonForRow(row("B2B_4473_post_createaccount_blocklist_400")));
        assertNull(DomainRules.reasonForRow(row("B2B_4473_post_createaccount_freelist_400")));
        assertNull(DomainRules.reasonForRow(row("B2B-722_post_activate_program_account_204")));
        assertNull(DomainRules.reasonForRow(null));
    }

    // ---------------------------------------------------------------
    // The switch
    // ---------------------------------------------------------------

    @Test(groups = {"unit"})
    @Description("NEGATIVE CONTROL: off by default, the CSV values pass through "
            + "untouched even with a reachable database full of matching rows.")
    public void disabledByDefaultKeepsCsvValues() {
        pointAtH2();
        String[] out = DomainRules.overrideOrCsv(
                row("B2B-6324_Verify_with_managed_domain_400"), "frozen1.com", "frozen2.com");
        assertEquals(out[0], "frozen1.com");
        assertEquals(out[1], "frozen2.com");
    }

    @Test(groups = {"unit"})
    @Description("Enabled, both placeholders come from the live rows for the "
            + "row's reason.")
    public void enabledSourcesBothDomainsFromDb() {
        pointAtH2();
        enable();
        String[] out = DomainRules.overrideOrCsv(
                row("B2B-6324_Verify_with_managed_domain_400"), "frozen1.com", "frozen2.com");
        List<String> live = List.of("managed-one.com", "managed-two.com", "managed-three.com");
        assertTrue(live.contains(out[0]), "RandomDomain not from the live set: " + out[0]);
        assertTrue(live.contains(out[1]), "RandomDomain2 not from the live set: " + out[1]);
    }

    @Test(groups = {"unit"}, invocationCount = 25)
    @Description("The pair stays distinct when more than one row matches -- "
            + "cases that add one domain and verify the other would otherwise "
            + "collide. Repeated because the pick is random.")
    public void thePairIsDistinctWhenSeveralRowsMatch() {
        pointAtH2();
        enable();
        String[] out = DomainRules.overrideOrCsv(
                row("B2B-6324_Verify_with_managed_domain_400"), "frozen1.com", "frozen2.com");
        // Both halves matter. Distinctness alone does NOT discriminate the
        // feature: the frozen CSV pair is already distinct, so this test
        // passed even with overrideOrCsv neutered until membership was
        // asserted too. Caught by revert-testing.
        List<String> live = List.of("managed-one.com", "managed-two.com", "managed-three.com");
        assertTrue(live.contains(out[0]) && live.contains(out[1]),
                "pair must come from the live rows, not the frozen CSV: "
                        + out[0] + " / " + out[1]);
        assertNotEquals(out[0], out[1]);
    }

    @Test(groups = {"unit"})
    @Description("A single matching row cannot yield a distinct pair; both "
            + "placeholders take it rather than one silently reverting.")
    public void singleRowGivesBothPlaceholdersTheSameDomain() {
        pointAtH2();
        enable();
        String[] out = DomainRules.overrideOrCsv(
                row("B2B-6324_AddCompetitorDomain"), "frozen1.com", "frozen2.com");
        assertEquals(out[0], "rival.com");
        assertEquals(out[1], "rival.com");
    }

    // ---------------------------------------------------------------
    // Fail-soft
    // ---------------------------------------------------------------

    @Test(groups = {"unit"})
    @Description("FAIL-SOFT: enabled but no reason resolvable keeps the CSV values.")
    public void unresolvableReasonKeepsCsvValues() {
        pointAtH2();
        enable();
        String[] out = DomainRules.overrideOrCsv(
                row("B2B-722_post_activate_program_account_204"), "frozen1.com", "frozen2.com");
        assertEquals(out[0], "frozen1.com");
        assertEquals(out[1], "frozen2.com");
    }

    @Test(groups = {"unit"})
    @Description("FAIL-SOFT: enabled with no database configured keeps the CSV "
            + "values -- a DB outage must never leave a row with no domain.")
    public void unreachableDatabaseKeepsCsvValues() {
        enable();
        String[] out = DomainRules.overrideOrCsv(
                row("B2B-6324_Verify_with_managed_domain_400"), "frozen1.com", "frozen2.com");
        assertEquals(out[0], "frozen1.com");
        assertEquals(out[1], "frozen2.com");
    }

    @Test(groups = {"unit"})
    @Description("FAIL-SOFT: a reason with no matching rows keeps the CSV values.")
    public void reasonWithNoRowsKeepsCsvValues() {
        pointAtH2();
        enable();
        Map<String, String> r = row("anything");
        r.put(DomainRules.REASON_COLUMN, "no_such_reason");
        String[] out = DomainRules.overrideOrCsv(r, "frozen1.com", "frozen2.com");
        assertEquals(out[0], "frozen1.com");
        assertEquals(out[1], "frozen2.com");
    }
}
