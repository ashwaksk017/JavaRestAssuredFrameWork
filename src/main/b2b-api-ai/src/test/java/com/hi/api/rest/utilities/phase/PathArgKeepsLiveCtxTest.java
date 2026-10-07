package com.hi.api.rest.utilities.phase;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * A path argument that goes through a datasheet reference must take the
 * LIVE ctx value, not the datasheet's saved snapshot of the same field.
 *
 * <p>partialgoalregression / goal10_Post_rateplancreate_400, run 7:</p>
 *
 * <pre>
 * [availability] groovyScript_availPropertyAndDates -> LONME on 2026-11-09 (inventory 163 > 9)
 * [subst] ... column=generatedDatesAndProps_arrivalDate | value=2026-11-09
 * -> GET  /props/MILHI/groups   (step=GET_Groups_SingleProp)
 * </pre>
 *
 * <p>The spec reads {@code Ref.ctx("DataSource.propCode")}. ctx has no
 * such key, so {@link Ref#resolveArg} falls to the CSV column
 * {@code DataSource_propCode}, whose cell is itself a reference:
 * {@code #generatedDatesAndProps_hcrs#}. The expansion asked ctx for the
 * key UNDER THE UNDERSCORE SPELLING, which {@code ctxGet} does not
 * normalise (it only alias-walks on a trailing dotted field), so the live
 * {@code generatedDatesAndProps.hcrs = LONME} that the availability search
 * had just published was missed -- and the row fallback, which IS tried
 * under both spellings, answered with the {@code generatedDatesAndProps.hcrs}
 * column: the Properties step's saved last-run value, MILHI.</p>
 *
 * <p>The query and body routes were already right: they go through
 * {@code mergedRow}, where ctx wins and is indexed under both spellings,
 * which is why the arrivalDate above was live while the propCode beside
 * it was stale.</p>
 */
@Feature("Framework")
@Story("Path argument resolution")
public class PathArgKeepsLiveCtxTest {

    private static Map<String, String> row() {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("DataSource_propCode", "#generatedDatesAndProps_hcrs#");
        row.put("DataSource_peakRoom", "12");
        // The Properties step's saved values, emitted as seed columns so
        // seedFromRowIfAbsent can fall back to them when no search runs.
        row.put("generatedDatesAndProps.hcrs", "MILHI");
        row.put("generatedDatesAndProps.arrivalDate", "2026-11-01");
        return row;
    }

    @Test
    public void aCellReferenceTakesTheLiveCtxValueOverTheSavedSnapshot() {
        Map<String, String> ctx = new LinkedHashMap<>();
        // What AvailabilitySearch.run publishes after the search.
        ctx.put("generatedDatesAndProps.hcrs", "LONME");
        ctx.put("generatedDatesAndProps.arrivalDate", "2026-11-09");

        Assert.assertEquals(Ref.resolveArg(ctx, row(), "DataSource.propCode"), "LONME",
                "the searched property must win over the datasheet snapshot");
    }

    @Test
    public void theSnapshotStillAnswersWhenNothingLiveExists() {
        Map<String, String> ctx = new LinkedHashMap<>();
        // No search ran (translation stubbed, or the step is absent): the
        // saved value is the only one there is, exactly as before.
        Assert.assertEquals(Ref.resolveArg(ctx, row(), "DataSource.propCode"), "MILHI");
    }

    @Test
    public void aPlainCellIsUnchanged() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("generatedDatesAndProps.hcrs", "LONME");
        Assert.assertEquals(Ref.resolveArg(ctx, row(), "DataSource.peakRoom"), "12");
    }

    @Test
    public void aDirectCtxKeyStillWinsOutright() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("DataSource.propCode", "NYCNH");
        ctx.put("generatedDatesAndProps.hcrs", "LONME");
        Assert.assertEquals(Ref.resolveArg(ctx, row(), "DataSource.propCode"), "NYCNH");
    }

    @Test
    public void aStepNameWithUnderscoresSplitsOnTheLastOne() {
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("GET_Groups_SingleProp.groupId", "G-77");
        Map<String, String> row = new LinkedHashMap<>();
        // The primary key's trailing field ("grp") matches nothing in ctx,
        // so only the cell expansion can find the value.
        row.put("DataSource_grp", "#GET_Groups_SingleProp_groupId#");
        row.put("GET_Groups_SingleProp.groupId", "G-stale");
        Assert.assertEquals(Ref.resolveArg(ctx, row, "DataSource.grp"), "G-77");
    }
}
