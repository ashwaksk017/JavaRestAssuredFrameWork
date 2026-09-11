package com.ak.api.tests.framework;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.ak.api.context.ScenarioContext;

import io.qameta.allure.Description;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Locks the typed context view. Pure unit tests -- no HTTP, no database.
 *
 * <p>The important assertions here are not the happy paths; they are the
 * separations. An account id is spelled 26 ways in the corpus and a guest id
 * 17, and the previous heuristic resolution is what allowed one to answer for
 * another.</p>
 */
@Feature("ScenarioContext")
public class ScenarioContextTest {

    private static Map<String, String> ctx(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        return m;
    }

    @Test(groups = {"unit", "context"})
    @Story("Declared aliases resolve, in priority order")
    public void aliasesResolveInOrder() {
        // Both spellings present -- the canonical (first-declared) wins.
        ScenarioContext sc = ScenarioContext.of(ctx(
                "PropertiesaccountID.accountID", "222",
                "PropertiesDetails.accountID", "111"));
        Assert.assertEquals(sc.accountId().orElseThrow().longValue(), 111L,
                "first declared alias must win");

        // Only the lower-priority spelling present -- still resolves.
        ScenarioContext only = ScenarioContext.of(ctx(
                "PropertiesaccountID.accountID", "222"));
        Assert.assertEquals(only.accountId().orElseThrow().longValue(), 222L);
    }

    @Test(groups = {"unit", "context"})
    @Story("Owner guest id and member guest id never merge")
    @Description("""
            The regression this class exists to prevent. A generic
            "key contains guestid" match would let the member's guest id
            answer for the owner's, which the project's do-not-regress list
            explicitly forbids.
            """)
    public void ownerAndMemberGuestIdsStayDistinct() {
        ScenarioContext sc = ScenarioContext.of(ctx(
                "Properties.guestID", "1001",
                "Properties.memberGuestID", "2002"));

        Assert.assertEquals(sc.guestId().orElseThrow().longValue(), 1001L,
                "owner guest id");
        Assert.assertEquals(sc.memberGuestId().orElseThrow().longValue(), 2002L,
                "member guest id");
        Assert.assertNotEquals(sc.guestId(), sc.memberGuestId());
    }

    @Test(groups = {"unit", "context"})
    @Story("Member guest id alone does NOT answer for the owner's")
    public void memberGuestIdDoesNotLeakIntoGuestId() {
        ScenarioContext sc = ScenarioContext.of(ctx(
                "Properties.memberGuestID", "2002"));
        Assert.assertTrue(sc.guestId().isEmpty()
                        || sc.guestId().orElseThrow() != 2002L,
                "member guest id must not satisfy guestId()");
    }

    @Test(groups = {"unit", "context"})
    @Story("Unresolved placeholders are not numbers")
    @Description("An unresolved #x# / @x@ must read as absent, not blow up "
            + "with a bare NumberFormatException from deep in a phase.")
    public void unresolvedPlaceholderIsNotANumber() {
        ScenarioContext sc = ScenarioContext.of(ctx(
                "PropertiesDetails.accountID", "@Properties_accountID@"));
        Assert.assertTrue(sc.accountId().isEmpty(), "placeholder is not an id");
        Assert.assertEquals(sc.rawOf(ScenarioContext.ACCOUNT_ID),
                "@Properties_accountID@", "raw value still readable");
    }

    @Test(groups = {"unit", "context"})
    @Story("require() names the field and the value found")
    public void requireFailsWithAUsefulMessage() {
        ScenarioContext sc = ScenarioContext.of(ctx());
        try {
            sc.requireAccountId();
            Assert.fail("expected IllegalStateException");
        } catch (IllegalStateException e) {
            String m = e.getMessage();
            Assert.assertTrue(m.contains("accountId"), m);
            Assert.assertTrue(m.contains("PropertiesDetails.accountID"),
                    "message should list the aliases tried: " + m);
        }
    }

    @Test(groups = {"unit", "context"})
    @Story("Empty values are skipped, not returned")
    @Description("A present-but-empty key must not shadow a populated "
            + "lower-priority alias.")
    public void emptyValueFallsThroughToNextAlias() {
        ScenarioContext sc = ScenarioContext.of(ctx(
                "PropertiesDetails.accountID", "",
                "PropertiesaccountID.accountID", "333"));
        Assert.assertEquals(sc.accountId().orElseThrow().longValue(), 333L);
    }

    @Test(groups = {"unit", "context"})
    @Story("Writes are visible through the raw map, and vice versa")
    @Description("The view shares the map rather than copying it -- that is "
            + "what lets generated Support classes and typed callers coexist.")
    public void writesArePassThrough() {
        Map<String, String> raw = ctx();
        ScenarioContext sc = ScenarioContext.of(raw);

        sc.put(ScenarioContext.ACCOUNT_ID, 4242L);
        Assert.assertEquals(raw.get("PropertiesDetails.accountID"), "4242",
                "typed write must land on the canonical key in the shared map");

        raw.put("Properties.guestID", "5150");
        Assert.assertEquals(sc.guestId().orElseThrow().longValue(), 5150L,
                "raw write must be visible to the typed view");
    }

    @Test(groups = {"unit", "context"})
    @Story("Tokens and text fields")
    public void textFieldsResolve() {
        ScenarioContext sc = ScenarioContext.of(ctx(
                "tokenId.GeneratedTokenID", "Bearer abc",
                "sftokenId.GeneratedTokenID", "Bearer sf",
                "Properties.Domain", "example.com"));
        Assert.assertEquals(sc.apiToken(), "Bearer abc");
        Assert.assertEquals(sc.salesforceToken(), "Bearer sf");
        Assert.assertEquals(sc.domain(), "example.com");
        Assert.assertNotEquals(sc.apiToken(), sc.salesforceToken(),
                "API and Salesforce tokens must never collapse");
    }

    @Test(groups = {"unit", "context"})
    @Story("snapshot() reports only what actually resolves")
    public void snapshotListsResolvedFieldsOnly() {
        ScenarioContext sc = ScenarioContext.of(ctx(
                "Properties.guestID", "77",
                "unrelated.key", "x"));
        Map<String, String> snap = sc.snapshot();
        Assert.assertEquals(snap.get("guestId"), "77");
        Assert.assertFalse(snap.containsKey("accountId"),
                "absent fields must not appear");
    }

    @Test(groups = {"unit", "context"})
    @Story("Numeric guard")
    public void nonNumericFieldRejectsLongRead() {
        ScenarioContext sc = ScenarioContext.of(ctx());
        try {
            sc.longOf(ScenarioContext.API_TOKEN);
            Assert.fail("expected IllegalArgumentException");
        } catch (IllegalArgumentException expected) {
            // A token is not a number; asking for it as one is a coding error.
        }
    }

    @Test(groups = {"unit", "context"})
    @Story("resolveDeclared never recurses into ctxGet")
    @Description("""
            ImportedScenario.ctxGetRaw calls resolveDeclared, so resolveDeclared
            must not call back into ctxGet -- that would be infinite recursion
            on every lookup in the suite.
            """)
    public void resolveDeclaredIsSelfContained() {
        Map<String, String> m = ctx("PropertiesDetails.accountID", "4242");
        Assert.assertEquals(
                ScenarioContext.resolveDeclared(m, "PropertiesDetails.accountID"),
                "4242");
        // An undeclared key must return "" -- meaning "not my business" --
        // rather than attempting any fallback of its own.
        Assert.assertEquals(
                ScenarioContext.resolveDeclared(m, "some.unknown.key"), "");
    }

    @Test(groups = {"unit", "context"})
    @Story("A declared key resolves through its siblings, in declared order")
    public void declaredResolutionIsDeterministic() {
        // Exact key absent; a lower-priority sibling holds the value.
        Map<String, String> m = ctx("PropertiesaccountID.accountID", "777");
        Assert.assertEquals(
                ScenarioContext.resolveDeclared(m, "PropertiesDetails.accountID"),
                "777", "sibling alias should answer deterministically");

        // Both present -> the FIRST declared alias wins, every time.
        Map<String, String> both = ctx(
                "PropertiesaccountID.accountID", "777",
                "PropertiesDetails.accountID", "111");
        Assert.assertEquals(
                ScenarioContext.resolveDeclared(both, "PropertiesaccountID.accountID"),
                "111", "declared order, not map order");
    }

    @Test(groups = {"unit", "context"})
    @Story("Numbered variants are NOT folded into the base field")
    @Description("""
            guestId1 / guestId2 / accountID2 are second entities in multi-account
            scenarios. Folding them into guestId would be the same defect class
            as merging owner and member.
            """)
    public void numberedVariantsStayDistinct() {
        Assert.assertFalse(ScenarioContext.isDeclared("PropertiesGuestId.guestId1"));
        Assert.assertFalse(ScenarioContext.isDeclared("Properties.guestID1"));
        Assert.assertFalse(ScenarioContext.isDeclared("PropertiesaccountID_2.accountID2"));
        Assert.assertTrue(ScenarioContext.isDeclared("Properties.guestID"));
    }

    @Test(groups = {"unit", "context"})
    @Story("Owner and member ids belong to different fields")
    public void ownerAndMemberMapToDifferentFields() {
        Assert.assertNotSame(
                ScenarioContext.fieldFor("Properties.guestID"),
                ScenarioContext.fieldFor("Properties.memberGuestID"));
    }

    @Test(groups = {"unit", "context"})
    @Story("API and Salesforce tokens never share a field")
    public void tokensAreDistinctFields() {
        Assert.assertNotSame(
                ScenarioContext.fieldFor("tokenId.GeneratedTokenID"),
                ScenarioContext.fieldFor("sftokenId.GeneratedTokenID"));
    }
}
