package com.hi.api.tests.framework;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * "Unresolved" and "missing" are not the same finding.
 *
 * <p>Three shipped bugs were one bug: a value existed, a reference asked
 * for it, and the two spelled it differently. The placeholder resolved to
 * nothing, {@code RestUtilities} substituted its {@code null} fallback,
 * and the server answered that the field was invalid -- so the search went
 * to the data, which was fine and sitting in the same map under a
 * neighbouring name.</p>
 *
 * <p>The convert-time check catches this statically. This asks the same
 * question where the answer is certain: with the real map, on the machine
 * running the suite, for values a static scan cannot see.</p>
 */
@Epic("Framework")
@Feature("Key resolution")
public class NearMissKeyReportTest {

    @SuppressWarnings("unchecked")
    private static String report(List<String> unresolved,
                                 Map<String, String> data) throws Exception {
        Class<?> c = Class.forName("com.hi.api.rest.utilities.RestUtilities");
        Method m = c.getDeclaredMethod("nearMissKeys", List.class, Map.class);
        m.setAccessible(true);
        return (String) m.invoke(null, unresolved, data);
    }

    private static Map<String, String> map(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        return m;
    }

    @Test(groups = {"unit", "framework"})
    @Story("a REST parameter captured under qry_ is named")
    public void theRestParameterGapIsNamed() throws Exception {
        String got = report(
                Arrays.asList("#GET_Groups_SingleProp_arrivalDate#"),
                map("qry_GET_Groups_SingleProp_arrivalDate", "2026-10-26"));

        Assert.assertTrue(got.contains("qry_GET_Groups_SingleProp_arrivalDate"),
                got);
    }

    @Test(groups = {"unit", "framework"})
    @Story("the dot / underscore gap is named in both directions")
    public void theSpellingGapIsNamed() throws Exception {
        Assert.assertTrue(
                report(Arrays.asList("#DataSource_propCode#"),
                        map("DataSource.propCode", "AAAAA"))
                        .contains("DataSource.propCode"));
        Assert.assertTrue(
                report(Arrays.asList("#DataSource.propCode#"),
                        map("DataSource_propCode", "AAAAA"))
                        .contains("DataSource_propCode"));
    }

    @Test(groups = {"unit", "framework"})
    @Story("genuinely missing data reports nothing")
    @Description("""
            The whole value of the line is that it distinguishes a naming
            gap from absent data. If it fired for both it would say
            nothing at all.
            """)
    public void genuinelyMissingDataIsSilent() throws Exception {
        Assert.assertEquals(
                report(Arrays.asList("#startDate#"),
                        map("somethingElse", "x")), "");
    }

    @Test(groups = {"unit", "framework"})
    @Story("an empty neighbour is not a find")
    public void anEmptyNeighbourIsNotReported() throws Exception {
        Assert.assertEquals(
                report(Arrays.asList("#S_p#"), map("qry_S_p", "")), "");
    }

    @Test(groups = {"unit", "framework"})
    @Story("the %pct% and @at@ delimiters are stripped too")
    public void everyDelimiterShapeIsHandled() throws Exception {
        Assert.assertTrue(report(Arrays.asList("%S_p%"),
                map("qry_S_p", "v")).contains("qry_S_p"));
        Assert.assertTrue(report(Arrays.asList("@S_p@"),
                map("path_S_p", "v")).contains("path_S_p"));
    }

    @Test(groups = {"unit", "framework"})
    @Story("null and empty inputs never throw")
    @Description("A reporting aid must never be the reason a run fails.")
    public void badInputIsSafe() throws Exception {
        Assert.assertEquals(report(null, map("a", "b")), "");
        Assert.assertEquals(report(Arrays.asList("#x#"), map()), "");
        Assert.assertEquals(report(Arrays.asList("#x#"), null), "");
    }
}
