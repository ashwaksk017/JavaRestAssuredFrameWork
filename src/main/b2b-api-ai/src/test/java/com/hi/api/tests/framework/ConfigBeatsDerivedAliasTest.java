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
 * A configured value must not be replaced by a guess at a name.
 *
 * <p>{@code ${#Project#username}} is a ReadyAPI PROJECT property -- the API
 * credential -- and it translates to {@code #username#}. mergedRow layers
 * config, then the row, then ctx, and the alias writer used {@code put},
 * so the row won. The CSV column {@code Properties.Username} derives a bare
 * {@code username} alias through the snake-case rule, and that alias
 * replaced the configured credential with a per-test enrollment name. The
 * token request then carried a test username and the server answered
 * {@code invalid request}.</p>
 *
 * <p>The rule is deliberately narrow: config is overwritten only by an
 * EXACT key, never by a DERIVED alias. An exact same-named column is a
 * deliberate per-row override and still wins.</p>
 *
 * <p>These tests use an unconfigured key where they need "not from
 * config", so they assert the alias machinery itself rather than depending
 * on what any particular environment happens to have set.</p>
 */
@Epic("Framework")
@Feature("Key resolution")
public class ConfigBeatsDerivedAliasTest {

    private static Map<String, String> map(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        return m;
    }

    @Test(groups = {"unit", "framework"})
    @Story("a derived alias still publishes when nothing else holds the key")
    @Description("""
            The convenience the alias exists for has to keep working --
            this fix must not turn the snake-case rule off.
            """)
    public void aDerivedAliasStillPublishes() {
        Map<String, String> merged =
                ImportedScenario.mergedRow(map("Properties.SomeOddField", "v"), null);

        Assert.assertEquals(merged.get("some_odd_field"), "v");
        Assert.assertEquals(merged.get("Properties_SomeOddField"), "v");
    }

    @Test(groups = {"unit", "framework"})
    @Story("ctx still beats the row on a shared derived alias")
    @Description("""
            The behaviour the narrow rule exists to preserve. The code
            documents it: when a PropertyTransfer write lands after a
            generator's fake write for the same field, the extract's real
            value has to win on the shared alias. A blanket putIfAbsent
            would have regressed exactly this.
            """)
    public void ctxStillBeatsTheRowOnASharedAlias() {
        Map<String, String> merged = ImportedScenario.mergedRow(
                map("Gen.someOddField", "fake"),
                map("Transfer.someOddField", "real"));

        Assert.assertEquals(merged.get("some_odd_field"), "real");
    }

    @Test(groups = {"unit", "framework"})
    @Story("an exact key from the row still overrides")
    public void anExactRowKeyStillWins() {
        Map<String, String> merged =
                ImportedScenario.mergedRow(map("some_odd_field", "exact"), null);

        Assert.assertEquals(merged.get("some_odd_field"), "exact");
    }

    @Test(groups = {"unit", "framework"})
    @Story("the row's own exact spelling is never disturbed")
    public void theRowsOwnKeysAreUntouched() {
        Map<String, String> merged = ImportedScenario.mergedRow(
                map("Properties.Username", "test-user"), null);

        // the column itself always resolves under its own name
        Assert.assertEquals(merged.get("Properties.Username"), "test-user");
        Assert.assertEquals(merged.get("Properties_Username"), "test-user");
    }

    @Test(groups = {"unit", "framework"})
    @Story("null and empty inputs are safe")
    public void badInputIsSafe() {
        Assert.assertNotNull(ImportedScenario.mergedRow(null, null));
        Assert.assertNotNull(ImportedScenario.mergedRow(map(), map()));
    }
}
