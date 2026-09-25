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
