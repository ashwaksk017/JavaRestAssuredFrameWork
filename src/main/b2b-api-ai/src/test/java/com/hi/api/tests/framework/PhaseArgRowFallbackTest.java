package com.hi.api.tests.framework;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.rest.utilities.phase.Ref;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * A phase argument must find its value whichever way the key is spelled.
 *
 * <h2>The bug</h2>
 *
 * <p>The converter names a CSV column with an UNDERSCORE
 * ({@code DataSource_propCode}) and emits a spec that reads the DOTTED ctx
 * key ({@code Ref.ctx("DataSource.propCode")}). ctx is seeded only for
 * prefixes that got a {@code seedFromRow(ctx, row, "<prefix>.")} call, and
 * nothing ever emitted one for {@code DataSource.} -- so 41 reads in one
 * suite resolved to "" and every URL lost a segment:</p>
 *
 * <pre>/props//groups</pre>
 *
 * <p>ReadyAPI ran the same case green, which is what made it a conversion
 * fault rather than missing data: the value was sitting in the datasheet the
 * whole time.</p>
 *
 * <h2>Why it kept coming back</h2>
 *
 * <p>This is the third sighting of the same shape. It was fixed once inside
 * {@code CtxFields.seedFromRow}, whose comment records the identical symptom
 * one segment further along ({@code /topics//partitions/3}) -- but that
 * repair covered only the prefixes the converter happens to emit a seed call
 * for. Auditing both converted suites for "reads a ctx key nothing produces,
 * while the CSV holds a column that would supply it" found four more, in
 * both suites, which is why the fix now lives in the read path instead.</p>
 */
@Epic("Framework")
@Feature("Phase argument resolution")
public class PhaseArgRowFallbackTest {

    private static Map<String, String> row() {
        Map<String, String> r = new LinkedHashMap<>();
        // exactly as the converter emits them
        r.put("DataSource_propCode", "#generatedDatesAndProps_hcrs#");
        r.put("generatedDatesAndProps.hcrs", "PROPA");
        r.put("generatedDatesAndProps.pcrs", "PROPB");
        return r;
    }

    @Test(groups = {"unit", "framework"})
    @Story("a dotted ctx key resolves from an underscore CSV column")
    @Description("""
            The exact failure: the spec asks for DataSource.propCode, the
            datasheet spells it DataSource_propCode, and the cell is itself a
            placeholder pointing at a third column.
            """)
    public void aDottedKeyResolvesFromAnUnderscoreColumn() {
        Map<String, String> ctx = new LinkedHashMap<>();

        String v = Ref.resolveArg(ctx, row(), "DataSource.propCode");

        Assert.assertEquals(v, "PROPA",
                "the value was in the row under the underscore spelling, and "
                + "the cell's own placeholder had to be expanded to reach it");
    }

    @Test(groups = {"unit", "framework"})
    @Story("ctx still wins over the datasheet")
    @Description("""
            The safety property. A runtime extract must beat a datasheet
            literal, or a phase would send the previous run's id.
            """)
    public void ctxBeatsTheRow() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("DataSource.propCode", "FROMCTX");

        Assert.assertEquals(
                Ref.resolveArg(ctx, row(), "DataSource.propCode"),
                "FROMCTX");
    }

    @Test(groups = {"unit", "framework"})
    @Story("a key nothing supplies stays empty")
    @Description("""
            Negative tests assert on an empty id. The row fallback must not
            invent one -- it only looks for a column that actually exists.
            """)
    public void anUnsuppliedKeyStaysEmpty() {
        Assert.assertEquals(
                Ref.resolveArg(new LinkedHashMap<>(), row(),
                        "DataSource.nothingSuppliesThis"),
                "");
    }

    @Test(groups = {"unit", "framework"})
    @Story("an unresolvable placeholder yields empty, not the placeholder")
    @Description("""
            If the cell points at a column that is not there, returning the
            literal `#key#` would send it to the server verbatim. Empty keeps
            the broken-path guard reporting the real problem.
            """)
    public void anUnresolvablePlaceholderDoesNotReachTheServer() {
        Map<String, String> r = new LinkedHashMap<>();
        r.put("DataSource_propCode", "#nothingDefinesThis#");

        String v = Ref.resolveArg(new LinkedHashMap<>(), r,
                "DataSource.propCode");

        Assert.assertEquals(v, "", "got: " + v);
    }

    @Test(groups = {"unit", "framework"})
    @Story("a plain literal column resolves untouched")
    public void aPlainLiteralColumnResolves() {
        Map<String, String> r = new LinkedHashMap<>();
        r.put("activate_account_accountId", "123456789");

        Assert.assertEquals(
                Ref.resolveArg(new LinkedHashMap<>(), r,
                        "activate_account.accountId"),
                "123456789");
    }

    @Test(groups = {"unit", "framework"})
    @Story("a null row is not a crash")
    public void aNullRowIsHandled() {
        Assert.assertEquals(
                Ref.resolveArg(new LinkedHashMap<>(), null,
                        "DataSource.propCode"),
                "");
    }
}
