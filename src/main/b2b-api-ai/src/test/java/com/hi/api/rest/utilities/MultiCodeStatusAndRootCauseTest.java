package com.hi.api.rest.utilities;

import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;
import org.testng.asserts.SoftAssert;

import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.response.Response;

import com.hi.api.reporting.ProgressLogListener;

/**
 * Two things a run said nothing about.
 *
 * <p>1. A ReadyAPI {@code Valid HTTP Status Codes} assertion listing
 * several codes ({@code 200,201,206}) reached the CSV as an EMPTY cell,
 * so the step logged "no expected status configured" and the check was
 * skipped -- 26 times in one run, each one reported as a pass. The cell
 * now carries the list and any listed code passes.</p>
 *
 * <p>2. Four tests reported {@code UndeclaredThrowableException:} with
 * nothing after the colon, 24 more {@code IllegalStateException:
 * SetupHelper.flow_A ...}; every one was really
 * {@code UnknownHostException: kapi-s.hhc.hilton.com}. The FAILED line
 * now appends the root cause.</p>
 */
@Feature("Framework")
@Story("Multi-code status assert and root-cause reporting")
public class MultiCodeStatusAndRootCauseTest {

    private static Response withStatus(int code) {
        return new ResponseBuilder().setStatusCode(code).setBody("{}").build();
    }

    private static Map<String, String> row(String cell) {
        Map<String, String> r = new HashMap<>();
        r.put("expected_POST_groupEvents_status_code", cell);
        return r;
    }

    private static int failures(SoftAssert sa) {
        try {
            sa.assertAll();
            return 0;
        } catch (AssertionError e) {
            return 1;
        }
    }

    @Test
    public void aListedCodePasses() {
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.statusFromStepColumn(sa, withStatus(201), row("200,201,206"),
                "POST_groupEvents", -1);
        Assert.assertEquals(failures(sa), 0);
    }

    @Test
    public void anUnlistedCodeFailsAndNamesTheList() {
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.statusFromStepColumn(sa, withStatus(404), row("200, 201, 206"),
                "POST_groupEvents", -1);
        try {
            sa.assertAll();
            Assert.fail("404 is not in the list");
        } catch (AssertionError e) {
            Assert.assertTrue(e.getMessage().contains("[200, 201, 206]"), e.getMessage());
            Assert.assertTrue(e.getMessage().contains("[404]"), e.getMessage());
        }
    }

    @Test
    public void aSingleCodeKeepsTheOldExactPath() {
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.statusFromStepColumn(sa, withStatus(200), row("200"),
                "POST_groupEvents", -1);
        Assert.assertEquals(failures(sa), 0);
        Assert.assertNull(ResponseAsserts.expectedStatusList("200"));
        Assert.assertNull(ResponseAsserts.expectedStatusList(""));
        Assert.assertNull(ResponseAsserts.expectedStatusList(null));
        Assert.assertNull(ResponseAsserts.expectedStatusList("not-a-code"));
    }

    @Test
    public void theRowExpectedOverloadAcceptsAListToo() {
        SoftAssert sa = new SoftAssert();
        ResponseAsserts.status(sa, withStatus(206), row("200,201,206"),
                "POST_groupEvents", -1);
        Assert.assertEquals(failures(sa), 0);
    }

    @Test
    public void theFailedLineNamesTheRootCause() {
        Throwable root = new java.net.UnknownHostException("kapi-s.hhc.hilton.com");
        Throwable mid = new RuntimeException("RestStep tokenRequest exchange failed", root);
        Throwable top = new IllegalStateException("SetupHelper.flow_A for suite eadkafkaevents", mid);
        String d = ProgressLogListener.describe(top);
        Assert.assertTrue(d.startsWith("IllegalStateException: SetupHelper.flow_A"), d);
        Assert.assertTrue(d.endsWith("<- UnknownHostException: kapi-s.hhc.hilton.com"), d);
    }

    @Test
    public void anUndeclaredThrowableIsUnwrapped() {
        Throwable root = new java.net.UnknownHostException("kapi-s.hhc.hilton.com");
        Throwable top = new java.lang.reflect.UndeclaredThrowableException(root);
        String d = ProgressLogListener.describe(top);
        Assert.assertTrue(d.contains("UnknownHostException: kapi-s.hhc.hilton.com"), d);
        Assert.assertSame(ProgressLogListener.rootCause(top), root);
    }

    @Test
    public void aPlainExceptionIsUnchanged() {
        String d = ProgressLogListener.describe(new AssertionError("The following asserts failed:"));
        Assert.assertEquals(d, "AssertionError: The following asserts failed:");
    }
}
