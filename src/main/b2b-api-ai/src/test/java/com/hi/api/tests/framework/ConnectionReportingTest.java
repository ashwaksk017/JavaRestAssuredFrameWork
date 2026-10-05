package com.hi.api.tests.framework;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.db.Db;

/**
 * The lines that say WHICH external system we are talking to.
 *
 * <p>Added so a run stops being silent about its outbound calls, and then
 * covered here because a reporting path with no test is a path that first
 * runs in front of a user -- which is exactly how an unexecuted line
 * shipped broken earlier today.</p>
 *
 * <p>The redaction one is not cosmetic. A JDBC URL may legally carry its
 * own credentials, and this line goes into a log that gets pasted into a
 * ticket.</p>
 */
public class ConnectionReportingTest {

    @Test(groups = {"guards"})
    public void aPasswordInTheJdbcUrlIsRedacted() {
        String out = Db.redactUrlCredentials(
                "jdbc:postgresql://db.example.com:5432/app?user=svc&password=hunter2");
        Assert.assertFalse(out.contains("hunter2"), out);
        Assert.assertFalse(out.contains("svc"), out);
        Assert.assertTrue(out.contains("db.example.com:5432/app"),
                "the host and schema are the POINT of the line: " + out);
    }

    @Test(groups = {"guards"})
    public void everyCredentialSpellingIsCovered() {
        for (String key : new String[]{"password", "PASSWORD", "pwd",
                                       "user", "username", "USER"}) {
            String out = Db.redactUrlCredentials(
                    "jdbc:mysql://h/db?" + key + "=sensitive");
            Assert.assertFalse(out.contains("sensitive"), key + " -> " + out);
        }
    }

    @Test(groups = {"guards"})
    public void aSecondParameterAfterTheSecretIsNotSwallowed() {
        // A greedy redaction would eat the rest of the URL, and the line
        // would stop telling anyone which database it was.
        String out = Db.redactUrlCredentials(
                "jdbc:postgresql://h/db?password=abc&ssl=true&schema=public");
        Assert.assertFalse(out.contains("abc"), out);
        Assert.assertTrue(out.contains("ssl=true"), out);
        Assert.assertTrue(out.contains("schema=public"), out);
    }

    @Test(groups = {"guards"})
    public void aUrlWithNoCredentialsIsLeftAlone() {
        String url = "jdbc:postgresql://db.example.com:5432/app";
        Assert.assertEquals(Db.redactUrlCredentials(url), url);
    }

    @Test(groups = {"guards"})
    public void aSemicolonStyleUrlIsHandledToo() {
        // SQL Server uses `;` rather than `&`.
        String out = Db.redactUrlCredentials(
                "jdbc:sqlserver://h:1433;databaseName=app;password=secret;encrypt=true");
        Assert.assertFalse(out.contains("secret"), out);
        Assert.assertTrue(out.contains("databaseName=app"), out);
    }

    @Test(groups = {"guards"})
    public void nullIsNotAnError() {
        // It is called while building a log line, so throwing here would
        // turn a diagnostic into an outage.
        Assert.assertEquals(Db.redactUrlCredentials(null), "");
    }
}
