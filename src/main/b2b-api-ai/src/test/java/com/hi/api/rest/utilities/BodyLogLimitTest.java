package com.hi.api.rest.utilities;

import static org.testng.Assert.assertEquals;
import static org.testng.Assert.assertFalse;
import static org.testng.Assert.assertTrue;

import org.testng.annotations.Test;

/**
 * Request and response bodies reach the log whole.
 *
 * <p>They used to be cut at 800 characters, which is shorter than the
 * payloads worth reading: a group-shop response runs to several KB of
 * roomRates, so the part that mattered was always the part removed. The
 * log is build output under {@code target/} and {@code mvn clean}
 * removes it, so log size is the cheaper problem to have.</p>
 *
 * <p>Pinned here because the limit is the kind of thing that gets
 * reinstated to quieten a noisy run, and the next person to do that
 * should have to see this test and decide deliberately.</p>
 */
public class BodyLogLimitTest {

    private static String body(int chars) {
        return "{\"roomRates\":\"" + "x".repeat(chars) + "\"}";
    }

    @Test
    public void aLongBodyIsLoggedWhole() {
        // Someone who deliberately passed -Dbody.log.limit=800 to quieten a
        // run should not get a red test for it; this pins the DEFAULT.
        if (Integer.getInteger("body.log.limit", 0) > 0) {
            throw new org.testng.SkipException(
                    "body.log.limit is set explicitly; this test pins the default");
        }
        String big = body(50_000);

        String logged = RestStep.capForLog(big);

        assertEquals(logged, big, "the log must carry the whole body");
        assertFalse(logged.contains("(truncated)"), "nothing should be cut");
    }

    @Test
    public void theFailureDigestStaysShort() {
        // A different job from the log: the digest is read as a compact
        // summary across a whole run, and a full body per entry buries it.
        String digest = RestStep.capForDigest(body(50_000));

        assertTrue(digest.contains("... (truncated)"), "the digest stays capped");
        assertTrue(digest.length() < 1_000,
                "digest entry should stay near its 800-char cap, was " + digest.length());
    }

    @Test
    public void nullIsReadableRatherThanAnException() {
        assertEquals(RestStep.capForLog(null), "<null>");
        assertEquals(RestStep.capForDigest(null), "<null>");
    }

    @Test
    public void aShortBodyIsUntouchedEitherWay() {
        String small = body(10);

        assertEquals(RestStep.capForLog(small), small);
        assertEquals(RestStep.capForDigest(small), small);
    }
}
