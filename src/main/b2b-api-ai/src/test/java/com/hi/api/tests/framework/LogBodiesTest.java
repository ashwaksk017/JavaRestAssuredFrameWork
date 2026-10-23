package com.hi.api.tests.framework;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.rest.utilities.LogBodies;

/**
 * Bodies in the full run log are laid out to be read: JSON pretty-printed
 * on indented lines under the line that names it (asked for: a 4,000-
 * character one-line body could not be read).
 */
public class LogBodiesTest {

    @Test(groups = {"guards"})
    public void jsonIsPrettyPrintedOnIndentedLines() {
        String out = LogBodies.pretty("{\"a\":1,\"b\":{\"c\":\"x\"}}", 0);
        Assert.assertTrue(out.startsWith("\n      {"), out);
        Assert.assertTrue(out.contains("\n        \"a\" : 1,"), out);
        Assert.assertTrue(out.contains("\n          \"c\" : \"x\""), out);
        for (String line : out.substring(1).split("\n")) {
            Assert.assertTrue(line.startsWith("      "), "every line indented: [" + line + "]");
        }
    }

    @Test(groups = {"guards"})
    public void notJsonKeepsItsOwnLinesIndented() {
        Assert.assertEquals(LogBodies.pretty("grant_type=password&x=1", 0), "\n      grant_type=password&x=1");
        Assert.assertEquals(LogBodies.pretty("<a>\n<b/>\n</a>", 0), "\n      <a>\n      <b/>\n      </a>");
        Assert.assertEquals(LogBodies.pretty("{not json", 0), "\n      {not json", "shown as it came");
    }

    @Test(groups = {"guards"})
    public void aLongBodySaysHowMuchWasLeftOut() {
        String out = LogBodies.pretty("x".repeat(50), 20);
        Assert.assertTrue(out.contains("... (30 more characters)"), out);
    }

    @Test(groups = {"guards"})
    public void nothingToShowIsEmpty() {
        Assert.assertEquals(LogBodies.pretty(null, 0), "");
        Assert.assertEquals(LogBodies.pretty("  ", 0), "");
    }

    @Test(groups = {"guards"})
    public void aRedactedBodyStaysRedacted() {
        String redacted = com.hi.api.security.Secrets.redact("{\"client_secret\":\"abcdefghijkl12345\",\"k\":\"v\"}");
        String out = LogBodies.pretty(redacted, 0);
        Assert.assertFalse(out.contains("abcdefghijkl12345"), out);
        Assert.assertTrue(out.contains("\"k\" : \"v\""), out);
    }
}
