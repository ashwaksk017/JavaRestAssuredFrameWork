package com.hi.api.tests.db;

import org.testng.Assert;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;

import com.hi.api.db.Db;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

@Epic("API Automation")
@Feature("Readable-test utilities")
public class DbPollTest {

    private static final String H2_URL =
            "jdbc:h2:mem:otp_poll_test;MODE=PostgreSQL;DB_CLOSE_DELAY=-1";

    private Db h2;

    @BeforeClass(alwaysRun = true)
    public void bootstrap() {
        h2 = Db.using(H2_URL, "sa", "");
        h2.executeStatement("DROP TABLE IF EXISTS otp");
        h2.executeStatement(
                "CREATE TABLE otp ("
                        + "account_member_id VARCHAR(32) PRIMARY KEY, "
                        + "email_otp INT"
                        + ")");
        h2.executeStatement("INSERT INTO otp (account_member_id, email_otp) VALUES (?, ?)",
                "mem-1", 42);
    }

    @Test(groups = {"unit"})
    @Story("pollEmailOtp skips when Db is not configured")
    @Description("Static pollEmailOtp returns empty rather than throwing when db.url is unset.")
    public void pollEmailOtp_skipsWhenNotConfigured() {
        if (Db.isConfigured()) {
            Assert.assertEquals(Db.pollEmailOtp(""), "");
            return;
        }
        Assert.assertEquals(Db.pollEmailOtp("mem-1"), "");
        Assert.assertEquals(Db.pollUntilStable("select 1", "x"), "");
    }

    @Test(groups = {"unit", "db", "h2"})
    @Story("pollColumnUntilStable pads email_otp")
    @Description("H2 uppercase column labels still resolve; numeric OTP is 6-digit padded; two equal reads are stable.")
    public void pollColumnUntilStable_padsOtpAndTreatsRepeatAsStable() {
        String otp = h2.pollColumnUntilStable(
                "SELECT email_otp FROM otp WHERE account_member_id = ?",
                "email_otp", 2, 5L, "mem-1");
        Assert.assertEquals(otp, "000042");
    }

    @Test(groups = {"unit"})
    @Story("a numeric account_member_id is bound as a number")
    @Description("Seen live on b2b-stg: the column is a bigint and Postgres refuses bigint = varchar, so "
            + "the story owner's one-time code was never found. A numeric id goes as a Long; any other as text.")
    public void memberIdParam_bindsANumericIdAsANumber() {
        Assert.assertEquals(Db.memberIdParam(" 417778 "), 417778L);
        Assert.assertEquals(Db.memberIdParam("mem-1"), "mem-1");
        Assert.assertEquals(Db.memberIdParam(null), "");
    }

    @Test(groups = {"unit", "db", "h2"})
    @Story("pollMemberEmailOtp against a bigint column")
    @Description("The real table's shape: a numeric id reads its code, both the SQL and the jOOQ way.")
    public void pollMemberEmailOtp_readsABigintColumn() {
        h2.executeStatement("CREATE SCHEMA IF NOT EXISTS \"segment\"");
        h2.executeStatement("DROP TABLE IF EXISTS \"segment\".\"account_member_internal_security\"");
        h2.executeStatement("CREATE TABLE \"segment\".\"account_member_internal_security\" ("
                + "\"account_member_id\" BIGINT PRIMARY KEY, \"email_otp\" INT)");
        h2.executeStatement("INSERT INTO \"segment\".\"account_member_internal_security\" "
                + "(\"account_member_id\", \"email_otp\") VALUES (?, ?)", 417778L, 7);
        Assert.assertEquals(h2.pollColumnUntilStable(
                "SELECT \"email_otp\" FROM \"segment\".\"account_member_internal_security\" "
                        + "WHERE \"account_member_id\" = ?", "email_otp", 2, 5L, Db.memberIdParam("417778")),
                "000007");
    }

    @Test(groups = {"unit", "db", "h2"})
    @Story("pollColumnUntilStable empty rows")
    @Description("Missing member id exhausts attempts and returns empty.")
    public void pollColumnUntilStable_emptyWhenNoRows() {
        String otp = h2.pollColumnUntilStable(
                "SELECT email_otp FROM otp WHERE account_member_id = ?",
                "email_otp", 2, 5L, "missing");
        Assert.assertEquals(otp, "");
    }
}
