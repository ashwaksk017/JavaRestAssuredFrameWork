package com.hi.api.reporting;

import java.util.Map;

import org.testng.IInvokedMethod;
import org.testng.IInvokedMethodListener;
import org.testng.ITestResult;

import com.hi.api.meta.TestMetadata;

import io.qameta.allure.Allure;

/**
 * Attaches partner / Jira / Xray / account-type labels to Allure so
 * imported B2B* tests can be filtered without renaming classes.
 */
public class TestMetadataListener implements IInvokedMethodListener {

    @Override
    public void beforeInvocation(IInvokedMethod method, ITestResult testResult) {
        if (!method.isTestMethod() || testResult == null) {
            return;
        }
        Map<String, String> row = rowParam(testResult);
        String methodName = testResult.getMethod() == null
                ? "" : testResult.getMethod().getMethodName();
        String className = testResult.getTestClass() == null
                ? "" : testResult.getTestClass().getRealClass().getSimpleName();
        TestMetadata meta = TestMetadata.infer(row, methodName, className);
        label("partner", meta.partner);
        label("accountType", meta.accountType);
        label("accountStatus", meta.accountStatus);
        label("jira", meta.jiraIssue);
        label("xray", meta.xrayId);
        label("testCaseId", meta.testCaseId);
        if (!meta.summary.isEmpty()) {
            try {
                Allure.getLifecycle().updateTestCase(tc -> {
                    if (tc.getDescription() == null || tc.getDescription().isEmpty()) {
                        tc.setDescription(meta.summary);
                    }
                });
            } catch (RuntimeException ignored) {
                // Allure no-op outside a running test
            }
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, String> rowParam(ITestResult testResult) {
        Object[] params = testResult.getParameters();
        if (params == null) {
            return null;
        }
        for (Object p : params) {
            if (p instanceof Map<?, ?> m) {
                return (Map<String, String>) m;
            }
        }
        return null;
    }

    private static void label(String name, String value) {
        if (value == null || value.isEmpty()) {
            return;
        }
        try {
            Allure.label(name, value);
        } catch (RuntimeException ignored) {
            // Allure no-op
        }
    }
}
