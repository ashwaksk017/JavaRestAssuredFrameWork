package com.hi.api.tests.framework;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * A REST step's PARAMETERS are properties of that step.
 *
 * <p>ReadyAPI reads {@code ${GET_Groups_SingleProp#arrivalDate}} as the
 * arrivalDate parameter declared on that request. The converter honours
 * both halves of that and spells them differently: the value is captured
 * as the CSV column {@code qry_<step>_<param>}, while the reference
 * translates to {@code #<step>_<param>#}. Nothing joined the two, so the
 * body went out as</p>
 *
 * <pre>
 * "startDate": "null"
 * </pre>
 *
 * <p>with the date sitting three cells away in the same row. The server
 * could only answer that startDate was invalid, which sends the search to
 * the data rather than to the spelling -- the reason this survived several
 * runs.</p>
 */
@Epic("Framework")
@Feature("Key resolution")
public class RestParamAliasTest {

    private static Map<String, String> row(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        return m;
    }

    @Test(groups = {"unit", "framework"})
    @Story("a query parameter resolves under the name the body references")
    public void aQueryParamIsReadableAsAStepProperty() {
        Map<String, String> merged = ImportedScenario.mergedRow(
                row("qry_GET_Groups_SingleProp_arrivalDate", "2026-10-26"),
                null);

        Assert.assertEquals(
                merged.get("GET_Groups_SingleProp_arrivalDate"), "2026-10-26");
        // the original spelling keeps working
        Assert.assertEquals(
                merged.get("qry_GET_Groups_SingleProp_arrivalDate"),
                "2026-10-26");
    }

    @Test(groups = {"unit", "framework"})
    @Story("a path parameter resolves the same way")
    public void aPathParamIsReadableAsAStepProperty() {
        Map<String, String> merged = ImportedScenario.mergedRow(
                row("path_GET_Shop_propCode", "AAAAA"), null);

        Assert.assertEquals(merged.get("GET_Shop_propCode"), "AAAAA");
    }

    @Test(groups = {"unit", "framework"})
    @Story("a genuine key beats the alias, whichever arrives first")
    @Description("""
            The alias only ever ADDS a reading for a key that resolved to
            nothing. If something really publishes `<step>_<param>`, that
            value wins -- otherwise this fix would quietly change what
            already-passing calls send.
            """)
    public void aRealKeyIsNeverOverwrittenByTheAlias() {
        // alias source first, real key second
        Map<String, String> aliasFirst = ImportedScenario.mergedRow(
                row("qry_S_p", "from-param", "S_p", "real"), null);
        Assert.assertEquals(aliasFirst.get("S_p"), "real");

        // real key first, alias source second
        Map<String, String> realFirst = ImportedScenario.mergedRow(
                row("S_p", "real", "qry_S_p", "from-param"), null);
        Assert.assertEquals(realFirst.get("S_p"), "real");
    }

    @Test(groups = {"unit", "framework"})
    @Story("a ctx value still beats a row-derived alias")
    public void ctxWinsOverTheAlias() {
        Map<String, String> merged = ImportedScenario.mergedRow(
                row("qry_S_p", "from-param"), row("S_p", "from-ctx"));

        Assert.assertEquals(merged.get("S_p"), "from-ctx");
    }

    @Test(groups = {"unit", "framework"})
    @Story("the prefix alone is not a key")
    public void aBarePrefixDoesNotProduceAnEmptyKey() {
        Map<String, String> merged = ImportedScenario.mergedRow(
                row("qry_", "x", "path_", "y"), null);

        Assert.assertFalse(merged.containsKey(""), merged.toString());
    }

    @Test(groups = {"unit", "framework"})
    @Story("an unrelated key is not de-prefixed")
    public void onlyTheTwoRestPrefixesAreBridged() {
        Map<String, String> merged = ImportedScenario.mergedRow(
                row("expected_status_code", "200",
                    "DataSource_propCode", "AAAAA"), null);

        Assert.assertFalse(merged.containsKey("status_code"),
                merged.toString());
        Assert.assertFalse(merged.containsKey("propCode"), merged.toString());
    }
}
