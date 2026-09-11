// =============================================================================
// XrayResult -- one row in the Xray "import execution" payload
// -----------------------------------------------------------------------------
// Xray Cloud accepts a batched execution import:
//
//   POST /api/v2/import/execution
//   {
//     "testExecutionKey": "PROJ-123",
//     "tests": [
//       { "testKey": "PROJ-456", "status": "PASSED",  "comment": "..." },
//       { "testKey": "PROJ-789", "status": "FAILED",  "comment": "AssertionError: ..." }
//     ]
//   }
//
// This record holds one entry; XrayClient batches them into the outer envelope.
// =============================================================================

package com.ak.api.xray;

import org.testng.ITestResult;

public record XrayResult(
        String testKey,
        Status status,
        String comment,
        long durationMs
) {
    /**
     * Xray Cloud test-status vocabulary. Xray Server / DC uses different
     * status names (e.g. "PASS" / "FAIL") -- extend this enum's mapping if
     * you need to target that deployment.
     */
    public enum Status {
        PASSED, FAILED, SKIPPED, ABORTED;

        /** Map from TestNG's ITestResult.getStatus() to an Xray status. */
        public static Status fromTestNg(int testngStatus) {
            return switch (testngStatus) {
                case ITestResult.SUCCESS -> PASSED;
                case ITestResult.FAILURE -> FAILED;
                case ITestResult.SKIP    -> SKIPPED;
                default                  -> ABORTED;
            };
        }
    }
}
