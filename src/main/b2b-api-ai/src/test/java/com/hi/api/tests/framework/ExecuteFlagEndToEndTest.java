package com.hi.api.tests.framework;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.DataProvider;
import org.testng.annotations.Test;
import org.testng.Assert;

import com.hi.api.tests.BaseApiTest;

/**
 * The {@code execute} switch, end to end through TestNG.
 *
 * <p>{@link com.hi.api.data.ExecutionFlagTest} covers the parsing. What it
 * cannot cover is the part that actually makes the feature work: TestNG
 * injecting the data-provider row into {@code BaseApiTest.newTestHolder} as
 * {@code Object[]}. That injection is a framework behaviour, not ours, and
 * if it ever stops happening the column becomes decoration -- every row
 * runs, including the ones someone switched off, and nothing says so.</p>
 *
 * <p>So this runs three real data rows through the real lifecycle and
 * checks which bodies were reached.</p>
 */
public class ExecuteFlagEndToEndTest extends BaseApiTest {

    private static final Set<String> RAN = ConcurrentHashMap.newKeySet();

    /** Restored in @AfterClass -- it is a process-wide property. */
    private static String priorCoolDown;

    @BeforeClass(alwaysRun = true)
    public void silenceTheCoolDown() {
        // Three rows at the default 3s inter-method cool-down would add
        // ~9s to a guards suite that runs in twelve. Nothing here talks
        // to a shared environment, so the pacing buys nothing.
        priorCoolDown = System.getProperty("test.interMethodCoolDownMs");
        System.setProperty("test.interMethodCoolDownMs", "0");
        RAN.clear();
    }

    @AfterClass(alwaysRun = true)
    public void restoreCoolDown() {
        if (priorCoolDown == null) {
            System.clearProperty("test.interMethodCoolDownMs");
        } else {
            System.setProperty("test.interMethodCoolDownMs", priorCoolDown);
        }
    }

    private static Map<String, String> row(String id, String execute) {
        Map<String, String> r = new LinkedHashMap<>();
        r.put("test_case_id", id);
        if (execute != null) {
            r.put("execute", execute);
        }
        return r;
    }

    @DataProvider(name = "threeRows")
    public Object[][] threeRows() {
        return new Object[][]{
                {row("row-yes", "Y")},
                {row("row-no", "N")},
                {row("row-blank", null)},     // no column at all
                {row("row-typo", "mabye")},   // unrecognised -> must RUN
        };
    }

    @Test(groups = {"guards"}, dataProvider = "threeRows")
    public void recordWhichRowsReachedTheBody(Map<String, String> row) {
        RAN.add(row.get("test_case_id"));
    }

    /**
     * {@code alwaysRun} because the row switched off above makes the
     * method it depends on a SKIP, and TestNG would otherwise skip this
     * verification too -- leaving the feature unverified by a suite that
     * still reported green.
     */
    @Test(groups = {"guards"}, alwaysRun = true,
          dependsOnMethods = "recordWhichRowsReachedTheBody")
    public void onlyTheSwitchedOffRowWasSkipped() {
        Assert.assertTrue(RAN.contains("row-yes"),
                "execute=Y must run; ran=" + sorted());
        Assert.assertTrue(RAN.contains("row-blank"),
                "a row with no execute column must run -- 1,064 generated "
                        + "row files predate the column; ran=" + sorted());
        Assert.assertTrue(RAN.contains("row-typo"),
                "an unrecognised value must RUN, never skip: a typo must "
                        + "not cost coverage silently; ran=" + sorted());
        Assert.assertFalse(RAN.contains("row-no"),
                "execute=N must NOT reach the test body; ran=" + sorted());
        Assert.assertEquals(RAN.size(), 3, "ran=" + sorted());
    }

    private static String sorted() {
        return Collections.singletonList(new java.util.TreeSet<>(RAN)).toString();
    }
}
