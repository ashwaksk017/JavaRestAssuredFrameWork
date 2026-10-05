package com.hi.api.data;

import static org.testng.Assert.assertEquals;
import static org.testng.Assert.assertFalse;
import static org.testng.Assert.assertNotNull;
import static org.testng.Assert.assertNull;
import static org.testng.Assert.assertTrue;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.annotations.Test;

/**
 * The {@code execute} switch, and above all the direction it fails in.
 *
 * <p>The asymmetry is the whole design. Running a row someone meant to
 * disable costs one test execution. Skipping a row someone meant to run
 * costs coverage with nothing in the report to say so. So every test here
 * that pins "this RUNS" is guarding against silent coverage loss across
 * 1,064 row files that predate the column, and is more load-bearing than
 * the ones that pin "this skips".</p>
 */
public class ExecutionFlagTest {

    private static Map<String, String> row(String execute) {
        Map<String, String> r = new LinkedHashMap<>();
        r.put("test_case_id", "B2B-1_do_thing_200");
        if (execute != null) {
            r.put("execute", execute);
        }
        return r;
    }

    // --- off --------------------------------------------------------
    @Test(groups = {"guards"})
    public void nSwitchesTheRowOff() {
        assertNotNull(ExecutionFlag.skipReason(row("N")));
    }

    @Test(groups = {"guards"})
    public void offValuesAreRecognisedWhateverTheCasing() {
        for (String v : new String[]{"N", "n", "No", "NO", "no", "false",
                                     "FALSE", "0", "off", "OFF", "skip"}) {
            assertNotNull(ExecutionFlag.skipReason(row(v)),
                    "should switch off: " + v);
        }
    }

    @Test(groups = {"guards"})
    public void surroundingWhitespaceDoesNotDefeatIt() {
        assertNotNull(ExecutionFlag.skipReason(row("  N  ")));
    }

    @Test(groups = {"guards"})
    public void aRetypedHeaderIsStillHonoured() {
        // A flag that stopped working on a capital letter would be worse
        // than no flag: the row runs, the sheet says it must not, and
        // nothing reports the disagreement.
        Map<String, String> r = new LinkedHashMap<>();
        r.put("Execute", "N");
        assertNotNull(ExecutionFlag.skipReason(r));
    }

    // --- on ---------------------------------------------------------
    @Test(groups = {"guards"})
    public void blankMeansRun() {
        assertNull(ExecutionFlag.skipReason(row("")));
        assertNull(ExecutionFlag.skipReason(row("   ")));
    }

    @Test(groups = {"guards"})
    public void aMissingColumnMeansRun() {
        // 1,064 generated row files and 1,165 rows predate this column.
        // Reading "no column" as "skip" would switch off the whole suite.
        assertNull(ExecutionFlag.skipReason(row(null)));
    }

    @Test(groups = {"guards"})
    public void onValuesRun() {
        for (String v : new String[]{"Y", "y", "Yes", "YES", "true", "1",
                                     "on", "run"}) {
            assertNull(ExecutionFlag.skipReason(row(v)),
                    "should run: " + v);
        }
    }

    @Test(groups = {"guards"})
    public void anUnrecognisedValueRunsRatherThanSkipping() {
        // A typo must never cost coverage. It warns on stdout instead.
        assertNull(ExecutionFlag.skipReason(row("maybe")));
        assertNull(ExecutionFlag.skipReason(row("NN")));
        assertNull(ExecutionFlag.skipReason(row("-")));
        assertTrue(ExecutionFlag.isUnrecognised("maybe"));
        assertFalse(ExecutionFlag.isUnrecognised(""));
        assertFalse(ExecutionFlag.isUnrecognised("N"));
    }

    @Test(groups = {"guards"})
    public void aNullRowRuns() {
        assertNull(ExecutionFlag.skipReason(null));
    }

    // --- the message ------------------------------------------------
    @Test(groups = {"guards"})
    public void theReasonNamesTheRowSoASkipCanBeActedOn() {
        // A SKIPPED line without the row's identity is useless: a CSV of
        // twelve rows gives twelve identical-looking skips.
        String why = ExecutionFlag.skipReason(row("N"));
        assertTrue(why.contains("B2B-1_do_thing_200"), why);
        assertTrue(why.contains("execute=N"), why);
    }

    @Test(groups = {"guards"})
    public void theReasonSaysItIsNotAFailure() {
        assertTrue(ExecutionFlag.skipReason(row("N")).contains("not a failure"));
    }

    @Test(groups = {"guards"})
    public void aRowWithNoIdStillProducesAReason() {
        Map<String, String> r = new HashMap<>();
        r.put("execute", "N");
        assertNotNull(ExecutionFlag.skipReason(r));
    }

    // --- finding the row among the parameters -----------------------
    @Test(groups = {"guards"})
    public void theRowIsFoundInTheParameters() {
        Map<String, String> r = row("N");
        assertEquals(ExecutionFlag.rowOf(new Object[]{r}), r);
    }

    @Test(groups = {"guards"})
    public void theRowIsFoundWhenItIsNotTheFirstParameter() {
        // A test taking (String env, Map row) is legal; reading only
        // params[0] would quietly stop honouring the flag for it.
        Map<String, String> r = row("N");
        assertEquals(ExecutionFlag.rowOf(new Object[]{"qa", r}), r);
    }

    @Test(groups = {"guards"})
    public void noParametersYieldsNoRow() {
        assertNull(ExecutionFlag.rowOf(null));
        assertNull(ExecutionFlag.rowOf(new Object[0]));
        assertNull(ExecutionFlag.rowOf(new Object[]{"just a string"}));
    }

    @Test(groups = {"guards"})
    public void aNonStringKeyedMapIsNotMistakenForARow() {
        Map<Integer, String> notARow = new HashMap<>();
        notARow.put(1, "one");
        assertNull(ExecutionFlag.rowOf(new Object[]{notARow}));
    }

    @Test(groups = {"guards"})
    public void anEmptyMapIsNotARow() {
        assertNull(ExecutionFlag.rowOf(new Object[]{new HashMap<String, String>()}));
    }

    // --- the throwing entry point -----------------------------------
    @Test(groups = {"guards"})
    public void skipIfOffThrowsForADisabledRow() throws Exception {
        java.lang.reflect.Method m = ExecutionFlagTest.class
                .getDeclaredMethod("skipIfOffThrowsForADisabledRow");
        try {
            ExecutionFlag.skipIfOff(m, new Object[]{row("N")});
            org.testng.Assert.fail("a disabled row must skip");
        } catch (org.testng.SkipException expected) {
            assertTrue(expected.getMessage().contains("execute=N"));
        }
    }

    @Test(groups = {"guards"})
    public void skipIfOffIsSilentForAnEnabledRow() throws Exception {
        java.lang.reflect.Method m = ExecutionFlagTest.class
                .getDeclaredMethod("skipIfOffIsSilentForAnEnabledRow");
        ExecutionFlag.skipIfOff(m, new Object[]{row("Y")});
        ExecutionFlag.skipIfOff(m, new Object[]{row(null)});
        ExecutionFlag.skipIfOff(null, null);
    }
}
