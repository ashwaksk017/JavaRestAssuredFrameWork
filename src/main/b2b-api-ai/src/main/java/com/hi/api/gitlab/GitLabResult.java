package com.hi.api.gitlab;

import org.testng.ITestResult;

public record GitLabResult(
        String className,
        String methodName,
        String testCaseIid,
        Status status,
        String comment,
        long durationMs
) {
    public enum Status {
        PASSED, FAILED, SKIPPED, ABORTED;

        public static Status fromTestNg(int testngStatus) {
            return switch (testngStatus) {
                case ITestResult.SUCCESS -> PASSED;
                case ITestResult.FAILURE -> FAILED;
                case ITestResult.SKIP    -> SKIPPED;
                default                  -> ABORTED;
            };
        }
    }

    public boolean hasTestCase() {
        return testCaseIid != null && !testCaseIid.isBlank();
    }
}
