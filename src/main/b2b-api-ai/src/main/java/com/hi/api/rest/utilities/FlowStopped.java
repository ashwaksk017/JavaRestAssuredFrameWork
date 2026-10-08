package com.hi.api.rest.utilities;

import com.hi.api.config.Config;
import com.hi.api.rest.ApiRoutes;

import io.restassured.response.Response;

/**
 * Raised when a write the rest of the case builds on was rejected.
 *
 * <p>A case creates something and then works on it: read it back, confirm
 * its owner, activate it, look it up in Salesforce. When the create comes
 * back 400 there is nothing to work on, and every later step runs against
 * ids that do not exist -- the ones the datasheet saved from somebody's last
 * run. One leadspace run, 27 rejected creates: 27 one-time-passcode queries
 * skipped for a missing member id, 21 confirmations posted with last run's
 * passcode to last run's account (404), 18 polls of 14 attempts each on an
 * account that was never there, and the whole test then run twice more by
 * the retry analyzer to get the same 400. The one line that mattered -- the
 * server's reason for the 400 -- was 300 lines above the failure.</p>
 *
 * <p>The status assertion has already failed the test by the time this is
 * raised, so stopping never turns a pass into a failure. It only ends the
 * test at the step that broke it, with the server's answer in the message.</p>
 *
 * <p>Two things it DOES change. A case the converter marks to SKIP at a later
 * untranslated database step is reported FAILED when an earlier write is
 * refused -- it never reaches the skip. And the steps after the refusal no
 * longer run, including a DELETE the case itself ends with; what earlier
 * steps created is left to the per-case cleanup.</p>
 *
 * <p>This is NOT what ReadyAPI does: most cases are saved with "abort on
 * error" off, and ReadyAPI runs the remaining steps against the same stale
 * property values. {@code test.stopAfterRejectedWrite=false} restores
 * that.</p>
 */
public final class FlowStopped extends AssertionError {

    private static final long serialVersionUID = 1L;

    private final String step;
    private final int status;
    private final boolean transientRejection;
    private final String signature;

    private FlowStopped(String message, String step, int status, boolean transientRejection,
                        String serverSaid) {
        super(message);
        this.step = step;
        this.status = status;
        this.transientRejection = transientRejection;
        // Ids and timestamps differ between attempts; the reason does not.
        this.signature = step + "|" + status + "|"
                + (serverSaid == null ? "" : serverSaid
                        // trace ids and UUIDs first, then any remaining number
                        .replaceAll("(?i)\\b[0-9a-f]{8}(?:-?[0-9a-f]{4}){3}-?[0-9a-f]{12}\\b", "#")
                        .replaceAll("(?i)\\b(?=[0-9a-f]*[0-9])[0-9a-f]{12,}\\b", "#")
                        .replaceAll("[0-9]+", "#"));
    }

    /**
     * What was refused and why, without the values that change per attempt:
     * the same string for two attempts refused the same way.
     */
    public String signature() {
        return signature;
    }

    public String step() {
        return step;
    }

    public int status() {
        return status;
    }

    /**
     * Would running the whole test again plausibly end differently?
     *
     * <p>A 5xx, a timeout or a throttle may clear. A conflict (409) may too:
     * the retry regenerates the identity it collided on. So may a 401/403,
     * which the retry meets with a fresh token. Those are always retried.</p>
     *
     * <p>A 400, 404, 415 or 422 is USUALLY the server refusing what this
     * test sends -- but not always (a 400 "Username is not unique" is a
     * collision on a random value). So one such refusal is still retried;
     * the retry analyzer stops only when the next attempt is refused at the
     * same step with the same message. See {@link #signature}.</p>
     *
     * <p>Except a rejection the framework already knows to be temporary
     * ("Member status is invalid" while an activation settles): that one
     * outlasted this attempt's wait and may not outlast the next.</p>
     */
    public boolean worthRetrying() {
        return transientRejection || worthRetrying(status);
    }

    public static boolean worthRetrying(int status) {
        if (status < 400 || status >= 500) {
            return true;
        }
        return status == 401 || status == 403 || status == 408
                || status == 409 || status == 429;
    }

    /**
     * Is this response a rejected write the case cannot continue past?
     *
     * @param expected the status the step asserts; negative when it asserts none
     * @param expectedList the step's status list when its column holds one, else null
     */
    public static boolean isRejectedWrite(String verb, String url, String stepName,
                                   int expected, java.util.Set<Integer> expectedList,
                                   Response res) {
        if (res == null || verb == null) {
            return false;
        }
        // A write. A GET that fails says the record is not visible; a DELETE
        // that fails is usually a pre-clean of something already gone.
        if (!("POST".equals(verb) || "PUT".equals(verb) || "PATCH".equals(verb))) {
            return false;
        }
        int actual = res.getStatusCode();
        if (actual < 400) {
            return false;
        }
        // The step must have asked for a success, and not got this.
        if (expectedList != null) {
            if (expectedList.contains(actual)) {
                return false;
            }
            for (int code : expectedList) {
                if (code < 200 || code >= 300) {
                    return false;
                }
            }
        } else if (expected < 200 || expected >= 300) {
            return false;       // no expectation, or a negative case
        }
        // A token call is not a write the case builds on; the auth
        // diagnostics and the token refresh own that failure.
        if (ApiRoutes.isTokenPath(url)
                || (stepName != null && stepName.toLowerCase().contains("token"))) {
            return false;
        }
        return true;
    }

    /** Throws when the step is a rejected write and the stop is enabled. */
    public static void stopIfRejectedWrite(String verb, String url, String stepName,
                                    int expected, java.util.Set<Integer> expectedList,
                                    Response res) {
        if (!isRejectedWrite(verb, url, stepName, expected, expectedList, res)) {
            return;
        }
        if (!Config.getBool("test.stopAfterRejectedWrite", true)) {
            return;
        }
        int actual = res.getStatusCode();
        String want = expectedList != null ? expectedList.toString() : "[" + expected + "]";
        String body = ResponseMasking.mask(cap(RestUtilities.getResponseAsString(res)));
        // Starts with the status assertion's own wording, so the digest
        // groups it with the failure it is.
        throw new FlowStopped("expected status for " + stepName + " expected " + want
                + " but found [" + actual + "] -- flow stopped here: " + verb + " "
                + stepName + " was rejected, so the steps after it have nothing to act on"
                + " (test.stopAfterRejectedWrite=false runs them anyway)."
                + (body.isEmpty() ? "" : " Server said: " + body),
                stepName, actual, RestUtilities.isTransientResponse(res), body);
    }

    private static String cap(String s) {
        if (s == null) {
            return "";
        }
        String one = s.replaceAll("\\s+", " ").trim();
        return one.length() <= 400 ? one : one.substring(0, 400) + "...";
    }
}
