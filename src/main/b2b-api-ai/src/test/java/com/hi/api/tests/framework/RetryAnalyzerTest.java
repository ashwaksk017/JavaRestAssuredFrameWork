package com.hi.api.tests.framework;

import java.lang.reflect.Proxy;

import org.testng.Assert;
import org.testng.ITestClass;
import org.testng.ITestNGMethod;
import org.testng.ITestResult;
import org.testng.annotations.Test;

import com.hi.api.retry.RetryAnalyzer;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

@Epic("API Automation")
@Feature("Retry")
public class RetryAnalyzerTest {

    @Test(groups = {"unit"})
    @Story("retry budget does not count intermediate attempts")
    @Description("moreRetriesRemain is true until max retries are consumed.")
    public void moreRetriesRemain_untilBudgetExhausted() {
        RetryAnalyzer.resetAttempts();
        RetryAnalyzer ra = new RetryAnalyzer();
        ITestResult r = stubResult();
        Assert.assertTrue(RetryAnalyzer.moreRetriesRemain(r));
        Assert.assertTrue(ra.retry(r));
        Assert.assertTrue(RetryAnalyzer.moreRetriesRemain(r));
        Assert.assertTrue(ra.retry(r));
        Assert.assertFalse(RetryAnalyzer.moreRetriesRemain(r),
                "terminal failure must be countable");
        Assert.assertFalse(ra.retry(r));
        RetryAnalyzer.clearAttempts(r);
        Assert.assertTrue(RetryAnalyzer.moreRetriesRemain(r));
    }

    @Test(groups = {"unit"})
    @Story("a missing value is not retried")
    @Description("An unresolved placeholder fails every attempt the same way: no retry.")
    public void anUnresolvedPlaceholderIsNotRetried() {
        RetryAnalyzer.resetAttempts();
        ITestResult r = stubResult(new RuntimeException("wrapped",
                new com.hi.api.rest.utilities.RestUtilities.UnresolvedPlaceholderException("#accountId# has no value")));
        Assert.assertFalse(new RetryAnalyzer().retry(r));
        Assert.assertFalse(RetryAnalyzer.moreRetriesRemain(r), "counted as the last attempt");
        RetryAnalyzer.clearAttempts(r);
    }

    private static ITestResult stubResult() {
        return stubResult(null);
    }

    private static ITestResult stubResult(Throwable thrown) {
        ClassLoader cl = RetryAnalyzerTest.class.getClassLoader();
        ITestClass testClass = (ITestClass) Proxy.newProxyInstance(
                cl, new Class<?>[] {ITestClass.class}, (p, m, a) -> {
                    if ("getRealClass".equals(m.getName())) return RetryAnalyzerTest.class;
                    return defaultValue(m.getReturnType());
                });
        ITestNGMethod method = (ITestNGMethod) Proxy.newProxyInstance(
                cl, new Class<?>[] {ITestNGMethod.class}, (p, m, a) -> {
                    if ("getMethodName".equals(m.getName())) return "retryMe";
                    return defaultValue(m.getReturnType());
                });
        return (ITestResult) Proxy.newProxyInstance(
                cl, new Class<?>[] {ITestResult.class}, (p, m, a) -> {
                    if ("getTestClass".equals(m.getName())) return testClass;
                    if ("getMethod".equals(m.getName())) return method;
                    if ("getParameters".equals(m.getName())) return new Object[0];
                    if ("getThrowable".equals(m.getName())) return thrown;
                    return defaultValue(m.getReturnType());
                });
    }

    private static Object defaultValue(Class<?> type) {
        if (!type.isPrimitive()) return null;
        if (type == boolean.class) return false;
        if (type == long.class) return 0L;
        if (type == int.class) return 0;
        if (type == double.class) return 0d;
        if (type == float.class) return 0f;
        if (type == short.class) return (short) 0;
        if (type == byte.class) return (byte) 0;
        if (type == char.class) return (char) 0;
        return null;
    }
}
