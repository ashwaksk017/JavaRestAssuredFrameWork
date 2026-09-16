package com.ak.api.tests.db;

import java.util.List;

import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import com.ak.api.db.Db;
import com.ak.api.db.repo.AccountRulesRepository;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Managed domains read from {@code account_rules}, against a REAL database.
 *
 * <p>Uses in-memory H2 in PostgreSQL-compatibility mode, the pattern
 * {@code H2QueryTest} establishes: no Docker, no external Postgres, and the
 * SQL actually executes rather than being mocked.</p>
 *
 * <p>Deliberately NOT extending {@code BaseApiTest} -- these tests need no
 * HTTP bootstrap, and the guards suite must stay runnable without any
 * credentials.</p>
 */
@Epic("API Automation")
@Feature("Managed domains from the database")
public class AccountRulesDomainsTest {

    /**
     * A NON-EMPTY password on purpose. Config.get() skips a system property
     * whose value fails isNonEmpty(), so -Ddb.password="" is ignored and the
     * lookup falls through to application.properties -- handing H2 some other
     * password and failing with SQLState 28000 "Wrong user name or password".
     * The fixture connects directly via Db.using(...) where an empty password
     * WOULD work, so the two paths disagreed and only the read path broke.
     */
    private static final String H2_USER = "sa";
    private static final String H2_PASS = "sa";

    /**
     * NON_KEYWORDS=VALUE matters: {@code value} is reserved in H2 but NOT in
     * PostgreSQL, and the real column is named {@code value} -- the suite's
     * own translated Groovy already runs
     * {@code SELECT value FROM segment.account_rules}. Un-reserving it lets
     * this test execute the EXACT SQL the repository emits. Quoting the
     * identifier instead would make H2 case-sensitive and diverge from
     * production.
     */
    private static final String H2_URL =
            "jdbc:h2:mem:ak_rules;MODE=PostgreSQL;DB_CLOSE_DELAY=-1;NON_KEYWORDS=VALUE";

    @BeforeClass(alwaysRun = true)
    public void bootstrapSchema() {
        Db h2 = Db.using(H2_URL, H2_USER, H2_PASS);
        h2.executeStatement("DROP TABLE IF EXISTS account_rules");
        h2.executeStatement(
                "CREATE TABLE account_rules ("
                        + "rule_type VARCHAR(64), "
                        + "value VARCHAR(255), "
                        + "reason VARCHAR(64))");
        // the rows the real query would see
        h2.executeStatement("INSERT INTO account_rules (rule_type, value, reason) "
                + "VALUES ('account_blacklist', ?, ?)", "managed-one.com", "managed_domain");
        h2.executeStatement("INSERT INTO account_rules (rule_type, value, reason) "
                + "VALUES ('account_blacklist', ?, ?)", "WWW.Managed-Two.com", "managed_domain");
        // duplicate, to prove de-duplication
        h2.executeStatement("INSERT INTO account_rules (rule_type, value, reason) "
                + "VALUES ('account_blacklist', ?, ?)", "managed-one.com", "managed_domain");
        // a different reason must NOT come back
        h2.executeStatement("INSERT INTO account_rules (rule_type, value, reason) "
                + "VALUES ('account_blacklist', ?, ?)", "competitor.com", "competitor_domain");
        // a blank value must be dropped
        h2.executeStatement("INSERT INTO account_rules (rule_type, value, reason) "
                + "VALUES ('account_blacklist', ?, ?)", "   ", "managed_domain");
    }

    private void pointConfigAtH2() {
        System.setProperty("db.url", H2_URL);
        System.setProperty("db.user", H2_USER);
        System.setProperty("db.password", H2_PASS);
    }

    @AfterMethod(alwaysRun = true)
    public void clearDbProps() {
        System.clearProperty("db.url");
        System.clearProperty("db.user");
        System.clearProperty("db.password");
        System.clearProperty("domains.fromDb.table");
    }

    @AfterClass(alwaysRun = true)
    public void dropSchema() {
        Db.using(H2_URL, H2_USER, H2_PASS).executeStatement("DROP TABLE IF EXISTS account_rules");
    }

    @Test(groups = {"unit"})
    @Story("the managed-domain list comes back from the database")
    @Description("""
            Replaces the static ALLOWED_DOMAINS string. Filters by reason,
            de-duplicates, lower-cases, and strips a www. prefix so the values
            match what pickAllowedDomain expects.
            """)
    public void readsManagedDomainsFilteredByReason() {
        pointConfigAtH2();
        List<String> domains = AccountRulesRepository.domainsByReason("managed_domain");

        Assert.assertTrue(domains.contains("managed-one.com"), String.valueOf(domains));
        // lower-cased and www-stripped
        Assert.assertTrue(domains.contains("managed-two.com"), String.valueOf(domains));
        // the other reason is excluded
        Assert.assertFalse(domains.contains("competitor.com"), String.valueOf(domains));
        // de-duplicated, and the blank row dropped
        Assert.assertEquals(domains.size(), 2, String.valueOf(domains));
    }

    @Test(groups = {"unit"})
    @Story("a different reason returns its own rows")
    public void reasonIsHonoured() {
        pointConfigAtH2();
        List<String> competitor = AccountRulesRepository.domainsByReason("competitor_domain");
        Assert.assertEquals(competitor, List.of("competitor.com"), String.valueOf(competitor));
    }

    @Test(groups = {"unit"})
    @Story("an unknown reason yields nothing, not an error")
    public void unknownReasonIsEmpty() {
        pointConfigAtH2();
        Assert.assertTrue(
                AccountRulesRepository.domainsByReason("no_such_reason").isEmpty());
    }

    @Test(groups = {"unit"})
    @Story("FAIL-SOFT: a broken database must never throw")
    @Description("""
            The caller keeps its configured ALLOWED_DOMAINS when this returns
            empty. Throwing, or returning empty AS IF authoritative, would
            empty the allowlist and poison generated identity suite-wide.
            """)
    public void aBrokenDatabaseReturnsEmptyRatherThanThrowing() {
        // valid-looking URL, nothing behind it
        System.setProperty("db.url", "jdbc:h2:mem:does_not_exist_here;IFEXISTS=TRUE");
        System.setProperty("db.user", H2_USER);
        System.setProperty("db.password", H2_PASS);
        List<String> domains = AccountRulesRepository.domainsByReason("managed_domain");
        Assert.assertTrue(domains.isEmpty(), String.valueOf(domains));
    }

    @Test(groups = {"unit"})
    @Story("FAIL-SOFT: placeholder credentials are treated as unconfigured")
    @Description("""
            program_configuration.json ships __SET_ME__ placeholders, which
            build a NON-BLANK db.url -- so Db.isConfigured() alone says yes
            while any connection attempt fails. Config.isUnset already knows
            that vocabulary.
            """)
    public void placeholderCredentialsAreSkipped() {
        System.setProperty("db.url", "jdbc:postgresql://__SET_ME__:5432/__SET_ME__");
        Assert.assertTrue(
                AccountRulesRepository.domainsByReason("managed_domain").isEmpty());
    }

    @Test(groups = {"unit"})
    @Story("a blank reason is refused")
    public void blankReasonIsRefused() {
        pointConfigAtH2();
        Assert.assertTrue(AccountRulesRepository.domainsByReason("").isEmpty());
        Assert.assertTrue(AccountRulesRepository.domainsByReason(null).isEmpty());
    }
}
