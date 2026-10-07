package com.hi.api.tests.db;


import com.hi.api.tests.BaseApiTest;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import com.hi.api.db.Db;
import com.hi.api.db.schema.Tables;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import org.jooq.SQLDialect;

@Epic("API Automation")
@Feature("Db jOOQ backend (H2)")
public class JooqQueryTest extends BaseApiTest {

    private static final String H2_URL =
            "jdbc:h2:mem:ak_jooq_test;MODE=PostgreSQL;DB_CLOSE_DELAY=-1";

    private Db h2;

    @BeforeClass(alwaysRun = true)
    public void bootstrapSchema() {
        h2 = Db.using(H2_URL, "sa", "").withApi(Db.Api.JOOQ);
        Assert.assertEquals(h2.api(), Db.Api.JOOQ);
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

    @AfterMethod(alwaysRun = true)
    public void clearApiOverride() {
        System.clearProperty("db.api");
    }

    @Test(groups = {"db", "h2", "jooq", "unit"})
    @Story("jOOQ queryOne")
    @Description("Same Map row contract as JDBC when db.api=jooq / withApi(JOOQ).")
    public void queryOne_singleUserById() {
        Map<String, Object> row = h2.queryOneRow(
                "SELECT id, email, active FROM users WHERE id = ?", 1);
        softAssert.assertNotNull(row, "row returned");
        softAssert.assertEquals(String.valueOf(row.get("ID")), "1", "ID");
        softAssert.assertEquals(row.get("EMAIL"), "alice@example.com", "EMAIL");
        softAssert.assertEquals(row.get("ACTIVE"), Boolean.TRUE, "ACTIVE");
    }

    @Test(groups = {"db", "h2", "jooq", "unit"})
    @Story("jOOQ queryAll")
    @Description("SELECT through jOOQ returns the same ordered rows as JDBC.")
    public void queryAll_activeUsers() {
        List<Map<String, Object>> rows = h2.queryAllRows(
                "SELECT id, email FROM users WHERE active = ? ORDER BY id", true);
        softAssert.assertEquals(rows.size(), 2, "two active users");
        softAssert.assertEquals(rows.get(0).get("EMAIL"), "alice@example.com", "first row");
        softAssert.assertEquals(rows.get(1).get("EMAIL"), "bob@example.com", "second row");
    }

    @Test(groups = {"db", "h2", "jooq", "unit"})
    @Story("jOOQ execute + verify")
    @Description("UPDATE via jOOQ execute, then queryOne to confirm.")
    public void execute_updateAndVerify() {
        int updated = h2.executeStatement(
                "UPDATE users SET active = ? WHERE id = ?", true, 3);
        softAssert.assertEquals(updated, 1, "one row updated");
        Map<String, Object> row = h2.queryOneRow(
                "SELECT active FROM users WHERE id = ?", 3);
        softAssert.assertEquals(row.get("ACTIVE"), Boolean.TRUE, "carol is now active");
        h2.executeStatement("UPDATE users SET active = ? WHERE id = ?", false, 3);
    }

    @Test(groups = {"db", "h2", "jooq", "unit"})
    @Story("withDsl fluent verification")
    @Description("Hand-written tests can use DSLContext without changing imported JDBC SQL.")
    public void withDsl_fetchCountAndEmail() {
        Integer count = h2.withDsl(dsl ->
                dsl.fetchCount(Db.table("USERS")));
        softAssert.assertEquals(count, Integer.valueOf(3), "three users");
        String email = h2.withDsl(dsl ->
                dsl.fetchOne("SELECT email FROM users WHERE id = ?", 1)
                        .get(0, String.class));
        softAssert.assertEquals(email, "alice@example.com");
    }

    @Test(groups = {"db", "h2", "jooq", "unit"})
    @Story("dialect-safe identifiers")
    @Description("Db.table / Db.field wrap DSL.name so schema-qualified names stay dialect-safe.")
    public void tableAndField_qualifiedIdentifiers() {
        String email = h2.withDsl(dsl ->
                dsl.select(Db.field("EMAIL"))
                        .from(Db.table("USERS"))
                        .where(Db.field("ID").eq(1))
                        .fetchOne(0, String.class));
        softAssert.assertEquals(email, "alice@example.com");
        softAssert.assertEquals(
                Db.name("segment", "account_member_internal_security").last(),
                "account_member_internal_security");
        Integer count = h2.withDsl(dsl -> dsl.fetchCount(Db.table("USERS")));
        softAssert.assertEquals(count, Integer.valueOf(3));
    }

    @Test(groups = {"db", "h2", "jooq", "unit"})
    @Story("hand-maintained schema catalog")
    @Description("Type-safe AccountMemberInternalSecurity poll pads email_otp the same as JDBC.")
    public void schemaTable_pollMemberEmailOtp() {
        h2.executeStatement("CREATE SCHEMA IF NOT EXISTS \"segment\"");
        h2.executeStatement(
                "DROP TABLE IF EXISTS \"segment\".\"account_member_internal_security\"");
        h2.executeStatement(
                "CREATE TABLE \"segment\".\"account_member_internal_security\" ("
                        + "\"account_member_id\" VARCHAR(64) PRIMARY KEY, "
                        + "\"email_otp\" INT)");
        h2.executeStatement(
                "INSERT INTO \"segment\".\"account_member_internal_security\" "
                        + "(\"account_member_id\", \"email_otp\") VALUES (?, ?)",
                "mem-schema-1", 42);
        Integer raw = h2.withDsl(dsl -> dsl.select(Tables.ACCOUNT_MEMBER_INTERNAL_SECURITY.EMAIL_OTP)
                .from(Tables.ACCOUNT_MEMBER_INTERNAL_SECURITY)
                .where(Tables.ACCOUNT_MEMBER_INTERNAL_SECURITY.ACCOUNT_MEMBER_ID.eq("mem-schema-1"))
                .fetchOne(Tables.ACCOUNT_MEMBER_INTERNAL_SECURITY.EMAIL_OTP));
        softAssert.assertEquals(raw, Integer.valueOf(42), "type-safe select");
        String otp = h2.pollMemberEmailOtp("mem-schema-1", 2, 5L);
        softAssert.assertEquals(otp, "000042");
    }

    @Test(groups = {"db", "h2", "jooq", "unit"})
    @Story("Db.using pins JDBC")
    @Description("Db.using ignores -Ddb.api=jooq; only configured()/withApi honor db.api.")
    public void using_pinsSqlEvenWhenConfigIsJooq() {
        String prev = System.getProperty("db.api");
        try {
            System.setProperty("db.api", "jooq");
            Assert.assertEquals(Db.Api.fromConfig(), Db.Api.JOOQ);
            Assert.assertEquals(Db.using(H2_URL, "sa", "").api(), Db.Api.SQL);
            Assert.assertEquals(Db.configured().api(), Db.Api.JOOQ);
            Assert.assertEquals(h2.api(), Db.Api.JOOQ, "withApi instance stays jOOQ");
        } finally {
            restoreProperty("db.api", prev);
        }
    }

    @Test(groups = {"db", "h2", "jooq", "unit"})
    @Story("static queryAll honors db.api")
    @Description("Db.queryAll uses configured() so -Ddb.api=jooq switches the static shortcuts.")
    public void staticQueryAll_honorsDbApiJooq() {
        String prevUrl = System.getProperty("db.url");
        String prevUser = System.getProperty("db.user");
        String prevPass = System.getProperty("db.password");
        String prevDriver = System.getProperty("db.driver");
        String prevApi = System.getProperty("db.api");
        String mem = "jdbc:h2:mem:ak_jooq_static;MODE=PostgreSQL;DB_CLOSE_DELAY=-1";
        try {
            System.setProperty("db.url", mem);
            System.setProperty("db.user", "sa");
            System.setProperty("db.password", "h2test");
            System.setProperty("db.driver", "org.h2.Driver");
            System.setProperty("db.api", "jooq");
            Assert.assertEquals(Db.configured().api(), Db.Api.JOOQ);
            Db.execute("DROP TABLE IF EXISTS static_users");
            Db.execute("CREATE TABLE static_users (id INT PRIMARY KEY, email VARCHAR(120))");
            Db.execute("INSERT INTO static_users (id, email) VALUES (?, ?)",
                    1, "static@example.com");
            List<Map<String, Object>> rows = Db.queryAll(
                    "SELECT email FROM static_users WHERE id = ?", 1);
            Assert.assertEquals(rows.size(), 1);
            Assert.assertEquals(String.valueOf(rows.get(0).get("EMAIL")),
                    "static@example.com");
        } finally {
            try {
                if (Db.isConfigured()) {
                    Db.execute("DROP TABLE IF EXISTS static_users");
                }
            } catch (RuntimeException ignored) {
                // H2 mem DB may already be gone
            }
            restoreProperty("db.url", prevUrl);
            restoreProperty("db.user", prevUser);
            restoreProperty("db.password", prevPass);
            restoreProperty("db.driver", prevDriver);
            restoreProperty("db.api", prevApi);
        }
    }

    private static void restoreProperty(String key, String previous) {
        if (previous == null) {
            System.clearProperty(key);
        } else {
            System.setProperty(key, previous);
        }
    }

    @Test(groups = {"db", "h2", "jooq", "unit"})
    @Story("jOOQ pollUntilStable")
    @Description("OTP poll loop uses queryAllRows, so withApi(JOOQ) still pads email_otp.")
    public void pollColumnUntilStable_viaJooq() {
        h2.executeStatement("DROP TABLE IF EXISTS otp");
        h2.executeStatement(
                "CREATE TABLE otp ("
                        + "account_member_id VARCHAR(32) PRIMARY KEY, "
                        + "email_otp INT)");
        h2.executeStatement("INSERT INTO otp (account_member_id, email_otp) VALUES (?, ?)",
                "mem-1", 42);
        String otp = h2.pollColumnUntilStable(
                "SELECT email_otp FROM otp WHERE account_member_id = ?",
                "email_otp", 2, 5L, "mem-1");
        softAssert.assertEquals(otp, "000042");
    }

    @Test(groups = {"unit"})
    @Story("db.api config")
    @Description("db.api=jooq|sql (and jdbc/orm aliases) parse from Config / -D.")
    public void apiFromConfig_parsesAliases() {
        System.setProperty("db.api", "jooq");
        Assert.assertEquals(Db.Api.fromConfig(), Db.Api.JOOQ);
        System.setProperty("db.api", "jdbc");
        Assert.assertEquals(Db.Api.fromConfig(), Db.Api.SQL);
        System.setProperty("db.api", "orm");
        Assert.assertEquals(Db.Api.fromConfig(), Db.Api.JOOQ);
        System.setProperty("db.api", "sql");
        Assert.assertEquals(Db.Api.fromConfig(), Db.Api.SQL);
        Assert.assertEquals(Db.dialectFor("jdbc:postgresql://localhost/db"), SQLDialect.POSTGRES);
        Assert.assertEquals(Db.dialectFor("jdbc:h2:mem:x"), SQLDialect.H2);
    }
}
