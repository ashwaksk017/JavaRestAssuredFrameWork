package com.hi.api.reporting;

import java.lang.reflect.Method;
import java.util.List;
import java.util.Map;

import org.testng.IInvokedMethod;
import org.testng.IInvokedMethodListener;
import org.testng.ISuite;
import org.testng.ISuiteListener;
import org.testng.ITestResult;

import com.hi.api.gitlab.GitLabClient;
import com.hi.api.gitlab.GitLabResult;
import com.hi.api.gitlab.GitLabResultsCollector;
import com.hi.api.gitlab.GitLabTest;

/**
 * Collects every @Test result for GitLab CI summaries and optional API sync.
 * No-op on the API when {@code gitlab.enabled=false} (default). Always writes
 * {@code target/gitlab-summary.md} and {@code target/gitlab.env}.
 */
public class GitLabReportListener implements IInvokedMethodListener, ISuiteListener {

    private static final String CSV_COLUMN = "gitlab_test_case_id";

    @Override
    public void beforeInvocation(IInvokedMethod method, ITestResult testResult) {
        if (!method.isTestMethod()) return;

        Object[] params = testResult.getParameters();
        if (params != null) {
            for (Object p : params) {
                if (p instanceof Map<?, ?> rowMap) {
                    Object idObj = rowMap.get(CSV_COLUMN);
                    if (idObj != null) {
                        String id = idObj.toString().trim();
                        if (!id.isEmpty()) {
                            GitLabResultsCollector.setCurrentTestCaseIid(id);
                            return;
                        }
                    }
                }
            }
        }

        Method reflectMethod = testResult.getMethod().getConstructorOrMethod().getMethod();
        if (reflectMethod != null) {
            GitLabTest ann = reflectMethod.getAnnotation(GitLabTest.class);
            if (ann != null && ann.value() != null && !ann.value().isBlank()) {
                GitLabResultsCollector.setCurrentTestCaseIid(ann.value().trim());
            }
        }
    }

    @Override
    public void afterInvocation(IInvokedMethod method, ITestResult testResult) {
        if (!method.isTestMethod()) return;
        if (TestInvocations.supersededByRetry(testResult)) {
            GitLabResultsCollector.clearCurrentTestCaseIid();
            return;
        }
        String iid = GitLabResultsCollector.getCurrentTestCaseIid();
        String comment = null;
        if (testResult.getThrowable() != null) {
            comment = testResult.getThrowable().getMessage();
            if (comment == null) comment = testResult.getThrowable().toString();
        }
        long duration = testResult.getEndMillis() - testResult.getStartMillis();
        String cls = testResult.getTestClass() == null
                ? ""
                : testResult.getTestClass().getName();
        String name = testResult.getMethod() == null
                ? ""
                : testResult.getMethod().getMethodName();
        GitLabResultsCollector.record(new GitLabResult(
                cls,
                name,
                iid,
                GitLabResult.Status.fromTestNg(testResult.getStatus()),
                comment,
                duration));
        GitLabResultsCollector.clearCurrentTestCaseIid();
    }

    @Override
    public void onFinish(ISuite suite) {
        List<GitLabResult> results = GitLabResultsCollector.drain();
        new GitLabClient().publish(results);
    }
}
