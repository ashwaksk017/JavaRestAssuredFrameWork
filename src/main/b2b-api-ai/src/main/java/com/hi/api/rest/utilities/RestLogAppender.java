// =============================================================================
// RestLogAppender
// -----------------------------------------------------------------------------
// Ported from reference (com.visa.b2b.connect.rest.utilities.RestLogAppender).
// Same three static helpers -- pretty-print request body, appendJsonLog with
// banner separators, log response body.
//
// Kept as a separate class (rather than folding into RestUtilities) so
// existing callers that reference RestLogAppender continue to compile.
// =============================================================================

package com.hi.api.rest.utilities;

import io.restassured.path.json.JsonPath;

public class RestLogAppender {

    public static void logRequestBody(String testCaseId, RestLoggerUtilityDataHolder rpdhl, String payload) {
        JsonPath jsonPath = new JsonPath(payload);
        String prettyReqBody = jsonPath.prettify();
        appendJsonLog(testCaseId, rpdhl, prettyReqBody, "Request");
    }

    public static void appendJsonLog(String testCaseId, RestLoggerUtilityDataHolder rpdhl, String payload, String type) {
        rpdhl.appendJsonLog("********************* Test Case " + testCaseId + " " + type + " *********************");
        rpdhl.appendJsonLog(System.lineSeparator());
        // Reference-compat twin of RestUtilities.appendJsonLog -- redacted
        // here too, so a legacy caller on this class cannot bypass it.
        rpdhl.appendJsonLog(com.hi.api.security.Secrets.redact(payload));
        rpdhl.appendJsonLog(System.lineSeparator());
        rpdhl.appendJsonLog("********************* Test Case " + testCaseId + " " + type + " *********************");
        rpdhl.appendJsonLog(System.lineSeparator());
    }

    public static void logResponseBody(String testCaseId, RestLoggerUtilityDataHolder rpdhl, String payload) {
        appendJsonLog(testCaseId, rpdhl, payload, "Response ");
    }
}
