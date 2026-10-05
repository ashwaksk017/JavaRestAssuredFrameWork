package com.hi.api.auth;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * What the OAuth token request says about itself in a log.
 *
 * <p>{@code AuthUtilities} had no logging at all: it reached an identity
 * provider in silence, so a token minted against the wrong environment was
 * indistinguishable from no token call. Adding the line raised a second
 * question -- what does it print? -- and a line that decides how a
 * credential appears in a log file is not something to discover from a log
 * file.</p>
 *
 * <p>Same package as the class under test, which is why these helpers are
 * package-private rather than private. No network: these are pure.</p>
 */
public class AuthLoggingTest {

    @Test(groups = {"guards"})
    public void aClientIdIsMaskedToItsLastFourCharacters() {
        String masked = AuthUtilities.maskTail("P_C_lXzbgf85zH4TYY5snTnMSpYa");
        Assert.assertFalse(masked.contains("P_C_lXzbgf85"), masked);
        Assert.assertTrue(masked.endsWith("SpYa"),
                "the tail is what lets two ids be told apart: " + masked);
        Assert.assertTrue(masked.startsWith("****"), masked);
    }

    @Test(groups = {"guards"})
    public void aShortValueIsMaskedEntirelyRatherThanMostlyShown() {
        // Showing 4 of 4 characters would be showing the whole thing.
        Assert.assertEquals(AuthUtilities.maskTail("abcd"), "****");
        Assert.assertEquals(AuthUtilities.maskTail("ab"), "****");
    }

    @Test(groups = {"guards"})
    public void anAbsentValueSaysSoRatherThanPrintingNull() {
        Assert.assertEquals(AuthUtilities.maskTail(null), "(unset)");
        Assert.assertEquals(AuthUtilities.maskTail("   "), "(unset)");
    }

    @Test(groups = {"guards"})
    public void surroundingWhitespaceDoesNotLeakTheHead() {
        String masked = AuthUtilities.maskTail("  secret-value-1234  ");
        Assert.assertFalse(masked.contains("secret"), masked);
        Assert.assertTrue(masked.endsWith("1234"), masked);
    }

    @Test(groups = {"guards"})
    public void anEmptyScopeReadsAsNoneNotAsBlank() {
        Assert.assertEquals(AuthUtilities.blankToNone(null), "(none)");
        Assert.assertEquals(AuthUtilities.blankToNone(""), "(none)");
        Assert.assertEquals(AuthUtilities.blankToNone("api.read"), "api.read");
    }
}
