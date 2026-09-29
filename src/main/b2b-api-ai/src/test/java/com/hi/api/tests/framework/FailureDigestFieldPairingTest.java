package com.hi.api.tests.framework;

import java.lang.reflect.Method;

import org.testng.Assert;
import org.testng.annotations.Test;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * The digest must say what WE sent, not only what the server complained about.
 *
 * <p>Before this, a failure read:</p>
 *
 * <pre>
 * server said: {"code":998,"message":"Invalid Parameter Value",
 *               "notifications":[{"fields":["startDate"],...}]}
 * </pre>
 *
 * <p>which names the field and hides the value. The value was the whole
 * bug -- an Excel date cell reaching the server as
 * {@code 2026-12-17 00:00:00} -- and finding that out cost a round trip
 * per run. The server names the field and the framework holds the
 * request, so the line that actually diagnoses it can be assembled.</p>
 *
 * <p>Driven by reflection because the helpers are private statics on a
 * class the converter EMITS. Testing them through the public listener
 * would mean standing up a TestNG context and a failing HTTP call; this
 * pins the logic that was actually wrong.</p>
 */
@Epic("Framework")
@Feature("Failure digest")
public class FailureDigestFieldPairingTest {

    private static String pair(String serverBody, String request, String url)
            throws Exception {
        Class<?> c = Class.forName("com.hi.api.reporting.FailureDigestListener");
        Method m = c.getDeclaredMethod("offendingFields", String.class,
                String.class, String.class);
        m.setAccessible(true);
        return (String) m.invoke(null, serverBody, request, url);
    }

    @Test(groups = {"unit", "framework"})
    @Story("a rejected JSON field is shown with the value we sent")
    @Description("""
            The exact failure that motivated this: the server rejected
            startDate, and the request held a Python-style timestamp.
            """)
    public void aRejectedJsonFieldIsPairedWithOurValue() throws Exception {
        String server = "{\"context\":\"GOAL\",\"code\":998,"
                + "\"message\":\"Invalid Parameter Value\",\"notifications\":"
                + "[{\"code\":\"31\",\"fields\":[\"startDate\"],"
                + "\"message\":\"Invalid JSON Parameter Value\"}]}";
        String request = "{\"startDate\":\"2026-12-17 00:00:00\","
                + "\"endDate\":\"2026-12-18\"}";

        String got = pair(server, request, "");

        Assert.assertTrue(got.contains("startDate="), got);
        Assert.assertTrue(got.contains("2026-12-17 00:00:00"), got);
        // the field the server did NOT name stays out of the line
        Assert.assertFalse(got.contains("endDate"), got);
    }

    @Test(groups = {"unit", "framework"})
    @Story("a dotted field path resolves to its leaf")
    @Description("""
            The server names roomTypes.roomTypeCode; the body nests it
            inside an array. The leaf is what can be found.
            """)
    public void aDottedFieldPathIsResolvedToItsLeaf() throws Exception {
        String server = "{\"code\":999,\"notifications\":[{\"fields\":"
                + "[\"roomTypes.roomTypeCode\"],\"message\":\"regex\"}]}";
        String request = "{\"roomTypes\":[{\"roomTypeCode\":\"KXTD\"}]}";

        String got = pair(server, request, "");

        Assert.assertTrue(got.contains("roomTypeCode="), got);
        Assert.assertTrue(got.contains("KXTD"), got);
    }

    @Test(groups = {"unit", "framework"})
    @Story("a rejected QUERY parameter is found in the url")
    @Description("""
            A rejected parameter can live in the query string rather than
            a body -- arrivalDate on a GET is exactly that case.
            """)
    public void aRejectedQueryParameterIsPairedFromTheUrl() throws Exception {
        String server = "{\"notifications\":[{\"code\":\"33\",\"fields\":"
                + "[\"arrivalDate\"],\"message\":\"Invalid Query Parameter\"}]}";
        String url = "GET https://h/props/AAAAA/groups"
                + "?arrivalDate=2026-12-17%2000:00:00&peakRooms=12";

        String got = pair(server, "", url);

        Assert.assertTrue(got.contains("arrivalDate="), got);
        Assert.assertTrue(got.contains("2026-12-17"), got);
    }

    @Test(groups = {"unit", "framework"})
    @Story("a field we never sent says so, rather than guessing")
    @Description("""
            "not in the request" is a finding in itself: the server
            rejected a field the request never carried.
            """)
    public void aFieldMissingFromTheRequestIsReportedAsMissing()
            throws Exception {
        String server = "{\"notifications\":[{\"fields\":[\"groupId\"]}]}";

        String got = pair(server, "{\"other\":1}", "GET https://h/x");

        Assert.assertTrue(got.contains("groupId="), got);
        Assert.assertTrue(got.contains("not in the request"), got);
    }

    @Test(groups = {"unit", "framework"})
    @Story("a response naming no field produces no line")
    @Description("A 401 body has no `fields`; the digest must stay quiet.")
    public void aResponseWithNoFieldsProducesNothing() throws Exception {
        Assert.assertEquals(
                pair("{\"message\":\"Missing Credentials\"}", "{}", ""), "");
        Assert.assertEquals(pair("", "{}", ""), "");
        Assert.assertEquals(pair(null, "{}", ""), "");
    }

    @Test(groups = {"unit", "framework"})
    @Story("several rejected fields are all paired")
    public void everyNamedFieldIsPaired() throws Exception {
        String server = "{\"notifications\":["
                + "{\"fields\":[\"startDate\"]},{\"fields\":[\"endDate\"]}]}";
        String request = "{\"startDate\":\"A\",\"endDate\":\"B\"}";

        String got = pair(server, request, "");

        Assert.assertTrue(got.contains("startDate=") && got.contains("endDate="),
                got);
    }

    @Test(groups = {"unit", "framework"})
    @Story("a credential-shaped field is redacted, not printed")
    @Description("""
            The value is shown UNMASKED so a malformed date is legible.
            That must not become a way to read a secret out of a digest
            pasted into a chat or a ticket.
            """)
    public void aCredentialShapedFieldIsRedacted() throws Exception {
        String server = "{\"notifications\":[{\"fields\":[\"client_secret\"]},"
                + "{\"fields\":[\"password\"]},{\"fields\":[\"assertion\"]}]}";
        String request = "{\"client_secret\":\"SHOULD-NOT-APPEAR\","
                + "\"password\":\"ALSO-NOT\",\"assertion\":\"NOR-THIS\"}";

        String got = pair(server, request, "");

        Assert.assertFalse(got.contains("SHOULD-NOT-APPEAR"), got);
        Assert.assertFalse(got.contains("ALSO-NOT"), got);
        Assert.assertFalse(got.contains("NOR-THIS"), got);
        Assert.assertTrue(got.contains("redacted"), got);
    }

    @Test(groups = {"unit", "framework"})
    @Story("a malformed date stays legible")
    @Description("The reason the value is not masked at all.")
    public void aMalformedDateIsShownInFull() throws Exception {
        String server = "{\"notifications\":[{\"fields\":[\"cutOffDate\"]}]}";

        String got = pair(server, "{\"cutOffDate\":\"2026-12-17 00:00:00\"}", "");

        Assert.assertTrue(got.contains("2026-12-17 00:00:00"), got);
    }

    @Test(groups = {"unit", "framework"})
    @Story("a numeric value is read as readily as a quoted one")
    public void anUnquotedScalarIsFound() throws Exception {
        String server = "{\"notifications\":[{\"fields\":[\"peakRooms\"]}]}";

        String got = pair(server, "{\"peakRooms\":12,\"x\":1}", "");

        Assert.assertTrue(got.contains("peakRooms=") && got.contains("12"), got);
    }
}
