// =============================================================================
// H2QueryTest -- self-contained proof that the Db utility works end-to-end
// -----------------------------------------------------------------------------
// Uses an in-memory H2 database in PostgreSQL-compatibility mode. No Docker,
// no external Postgres, no local setup required -- H2 ships as a test-scoped
// dependency and everything runs inside the JVM.
//
// DB_CLOSE_DELAY=-1 keeps the in-memory DB alive across the multiple
// short-lived DriverManager.getConnection() calls the Db utility makes
// (otherwise H2 tears down the schema as soon as the last connection closes).
//
// This is the pattern to copy when you want to demo the utility to teammates
// without asking them to bring their own Postgres.
// =============================================================================

package com.ak.api.tests;

import java.util.List;
import java.util.Map;

import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import com.ak.api.db.Db;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

@Epic("API Automation")
@Feature("Db Utility Self-Test (H2)")
public class H2QueryTest extends BaseApiTest {

    /**
     * PostgreSQL compat mode so the SQL we write matches production-shape queries.
     * DB_CLOSE_DELAY=-1 keeps the shared in-memory database alive across the
     * fresh connections Db opens for each call. Fixed DB name 'ak_test' so
     * every test in this class sees the same schema.
     */
    private static final String H2_URL =
            "jdbc:h2:mem:ak_test;MODE=PostgreSQL;DB_CLOSE_DELAY=-1";

    private Db h2;

    @BeforeClass(alwaysRun = true)
    public void bootstrapSchema() {
        h2 = Db.using(H2_URL, "sa", "");
        h2.executeStatement("DROP TABLE IF EXISTS users");
        h2.executeStatement(
                "CREATE TABLE users ("
                        + "id INT PRIMARY KEY, "
                        + "email VARCHAR(120) NOT NULL, "
                        + "active BOOLEAN NOT NULL"
                        + ")");
        h2.executeStatement("INSERT INTO users (id, email, active) VALUES (?, ?, ?)",
                1, "alice@example.com", true);
        h2.executeStatement("INSERT INTO users (id, email, active) VALUES (?, ?, ?)",
                2, "bob@example.com", true);
        h2.executeStatement("INSERT INTO users (id, email, active) VALUES (?, ?, ?)",
                3, "carol@example.com", false);
    }

    @Test(groups = {"db", "h2"})
    @Story("queryOne returns a single row")
    @Description("Retrieve one user by id, assert every column mapped correctly (case-preserving column labels).")
    public void queryOne_singleUserById() {
        Map<String, Object> row = h2.queryOneRow(
                "SELECT id, email, active FROM users WHERE id = ?", 1);

        softAssert.assertNotNull(row, "row returned");
        softAssert.assertEquals(row.get("ID").toString(), "1", "ID");
        softAssert.assertEquals(row.get("EMAIL"), "alice@example.com", "EMAIL");
        softAssert.assertEquals(row.get("ACTIVE"), Boolean.TRUE, "ACTIVE");
    }

    @Test(groups = {"db", "h2"})
    @Story("queryAll returns multiple rows in a stable order")
    @Description("SELECT the two active users, assert size + the emails match in order.")
    public void queryAll_activeUsers() {
        List<Map<String, Object>> rows = h2.queryAllRows(
                "SELECT id, email FROM users WHERE active = ? ORDER BY id", true);

        softAssert.assertEquals(rows.size(), 2, "two active users");
        softAssert.assertEquals(rows.get(0).get("EMAIL"), "alice@example.com", "first row");
        softAssert.assertEquals(rows.get(1).get("EMAIL"), "bob@example.com", "second row");
    }

    @Test(groups = {"db", "h2"})
    @Story("exists() returns true / false correctly")
    @Description("Probe for a user that exists (returns true) and one that doesn't (returns false).")
    public void exists_presentAndAbsent() {
        softAssert.assertTrue(
                h2.rowExists("SELECT 1 FROM users WHERE email = ?", "alice@example.com"),
                "alice is present");
        softAssert.assertFalse(
                h2.rowExists("SELECT 1 FROM users WHERE email = ?", "nobody@example.com"),
                "nobody is absent");
    }

    @Test(groups = {"db", "h2"})
    @Story("execute returns affected-row count")
    @Description("UPDATE Carol to active, assert 1 row affected, then verify the flip stuck via queryOne.")
    public void execute_updateAndVerify() {
        int updated = h2.executeStatement(
                "UPDATE users SET active = ? WHERE id = ?", true, 3);
        softAssert.assertEquals(updated, 1, "one row updated");

        Map<String, Object> row = h2.queryOneRow(
                "SELECT active FROM users WHERE id = ?", 3);
        softAssert.assertEquals(row.get("ACTIVE"), Boolean.TRUE, "carol is now active");

        // Restore so re-runs stay deterministic.
        h2.executeStatement("UPDATE users SET active = ? WHERE id = ?", false, 3);
    }

    @Test(groups = {"db", "h2"})
    @Story("queryOne returns null when no row matches")
    @Description("Query for a non-existent id; asserts queryOne returns null (not an exception, not an empty map).")
    public void queryOne_noMatch_returnsNull() {
        Map<String, Object> row = h2.queryOneRow(
                "SELECT * FROM users WHERE id = ?", 9999);
        softAssert.assertNull(row, "no match => null");
    }
}
