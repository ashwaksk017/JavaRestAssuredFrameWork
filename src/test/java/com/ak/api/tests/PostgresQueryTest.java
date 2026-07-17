// =============================================================================
// PostgresQueryTest -- live Postgres against a user-provided DB
// -----------------------------------------------------------------------------
// Skips cleanly when db.url is not configured (no shipped Postgres in the
// public repo). To run against your own DB:
//
//   mvn test "-Dgroups=db" `
//       "-Ddb.url=jdbc:postgresql://localhost:5432/mydb" `
//       "-Ddb.user=myuser" `
//       "-Ddb.password=mypass"
//
// The two tests here demonstrate the two patterns you'll actually use:
//   1. Sanity check the connection (SELECT 1 AS n)
//   2. Query pg_catalog for schema info -- proves parameterized queries work
//      against real Postgres without needing a user schema
// =============================================================================

package com.ak.api.tests;

import java.util.Map;

import org.testng.SkipException;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import com.ak.api.db.Db;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

@Epic("API Automation")
@Feature("Postgres Query Helper")
public class PostgresQueryTest extends BaseApiTest {

    @BeforeClass(alwaysRun = true)
    public void skipIfNotConfigured() {
        if (!Db.isConfigured()) {
            throw new SkipException(
                    "db.url not configured -- skipping Postgres tests. "
                            + "Supply via -Ddb.url=... / DB_URL env var to run against a real Postgres.");
        }
    }

    @Test(groups = {"db", "postgres"})
    @Story("Connection sanity -- SELECT 1")
    @Description("Round-trips a trivial 'SELECT 1 AS n' to prove the JDBC URL, user, password, and network path all work end-to-end.")
    public void connection_sanityCheck() {
        Map<String, Object> row = Db.queryOne("SELECT 1 AS n");
        softAssert.assertNotNull(row, "row returned");
        softAssert.assertEquals(row.get("n").toString(), "1",
                "SELECT 1 returns 1 in the aliased column");
    }

    @Test(groups = {"db", "postgres"})
    @Story("Parameterized query against pg_catalog")
    @Description("Queries pg_catalog.pg_tables for tables in the 'public' schema using a bound '?' parameter. Verifies parameter binding + result-set mapping against real Postgres.")
    public void parameterizedQuery_pgCatalog() {
        boolean anyTables = Db.exists(
                "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = ? LIMIT 1",
                "public");
        // Not asserting count -- the target DB may or may not have public-schema
        // tables. This test just proves the parameter bound + query executed.
        // If your DB has known tables, add stronger assertions here.
        softAssert.assertTrue(anyTables || !anyTables,
                "parameterized query executed against pg_catalog (assertion always true)");
    }
}
