package com.hi.api.reporting;

import org.testng.ITestResult;

import com.hi.api.retry.RetryAnalyzer;

import io.qameta.allure.Allure;
import io.qameta.allure.model.Status;
import io.qameta.allure.model.StatusDetails;

/**
 * Shared TestNG retry / Allure helpers so intermediate RetryAnalyzer
 * attempts do not inflate suite, GitLab, Xray, or Extent totals.
 */
public final class TestInvocations {

    private static final ThreadLocal<String> ALLURE_UUID = new ThreadLocal<>();

    private TestInvocations() {}

    /**
     * True when this callback is a RetryAnalyzer re-run, not a terminal
     * outcome. Skip counting / publishing when true.
     */
    public static boolean supersededByRetry(ITestResult result) {
        if (result == null) return false;
        if (result.wasRetried()) return true;
        if (result.getStatus() == ITestResult.SUCCESS) return false;
        return RetryAnalyzer.moreRetriesRemain(result);
    }

    public static void captureAllureUuid() {
        try {
            Allure.getLifecycle().getCurrentTestCase().ifPresent(ALLURE_UUID::set);
        } catch (RuntimeException ignored) {
            // Allure not on the listener path
        }
    }

    public static void markAllureFailed(Throwable error) {
        if (error == null) return;
        try {
            var lifecycle = Allure.getLifecycle();
            String uuid = lifecycle.getCurrentTestCase().orElse(ALLURE_UUID.get());
            if (uuid == null || uuid.isEmpty()) return;
            lifecycle.updateTestCase(uuid, tc -> {
                tc.setStatus(Status.FAILED);
                StatusDetails details = new StatusDetails();
                details.setMessage(error.getMessage());
                tc.setStatusDetails(details);
            });
        } catch (RuntimeException ignored) {
            // Best-effort: TestNG ITestResult.FAILURE is the source of truth
        }
    }

    public static void clearAllureUuid() {
        ALLURE_UUID.remove();
    }

    /** Terminal outcome: drop retry budget so the next row starts at 0. */
    public static void complete(ITestResult result) {
        RetryAnalyzer.clearAttempts(result);
        clearAllureUuid();
    }
}
