// =============================================================================
// RestLoggerUtilityDataHolder
// -----------------------------------------------------------------------------
// Ported verbatim from reference (com.visa.b2b.connect.rest.utilities).
// Per-test container: accumulates a structured log across a test case and
// carries the TestNG SoftAssert reference so multiple soft assertions can be
// collected before assertAll().
// =============================================================================

package com.ak.api.rest.utilities;

import org.testng.asserts.SoftAssert;

public class RestLoggerUtilityDataHolder {

    private final StringBuilder jsonLogBuilder = new StringBuilder();
    private SoftAssert softAssertRef;

    public String getJsonLog() {
        return jsonLogBuilder.toString();
    }

    public void appendJsonLog(String jsonLog) {
        this.jsonLogBuilder.append(jsonLog);
    }

    public SoftAssert getSoftAssertRef() {
        return softAssertRef;
    }

    public void setSoftAssertRef(SoftAssert softAssertRef) {
        this.softAssertRef = softAssertRef;
    }
}
