package com.hi.api.tests.framework;

import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.data.PlaceholderResolver;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Two resolvers, one contract.
 *
 * <p>{@code RestUtilities.substitute} has long carried a case-folded
 * index, with the reason written beside it: ReadyAPI resolves
 * {@code ${Step#prop}} without regard to case and these projects rely on
 * it. {@code PlaceholderResolver} -- the resolver assertion cells and URL
 * substitution go through -- never learned that, so
 * {@code ${groupId#roomTypeCode}} looked for {@code groupId.roomTypeCode}
 * while the Properties step publishes {@code groupid.roomTypeCode},
 * missed on one capital letter, and left the literal in place. The
 * assertion then asked whether the response contained the text
 * {@code ${groupId#roomTypeCode}}, which it never can.</p>
 *
 * <p>Also covers the empty-collection miss: an extract that resolved to
 * an empty list rendered as the two characters {@code []} and went to
 * the wire as a room type code.</p>
 */
@Epic("Framework")
@Feature("Key resolution")
public class ResolverSpellingTest {

    private static Map<String, String> ctx(String... kv) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        return m;
    }

    @Test(groups = {"unit", "framework"})
    @Story("a dollar ref resolves against a ctx key that differs only by case")
    @Description("The live failure, reduced: ${groupId#roomTypeCode} "
            + "against a step publishing groupid.")
    public void aDollarRefResolvesCaseInsensitively() {
        String got = PlaceholderResolver.resolveAll(
                "${groupId#roomTypeCode}", ctx("groupid.roomTypeCode", "KPVN"));

        Assert.assertEquals(got, "KPVN");
    }

    @Test(groups = {"unit", "framework"})
    @Story("a hash ref resolves the same way")
    public void aHashRefResolvesCaseInsensitively() {
        String got = PlaceholderResolver.resolveAll(
                "#groupId_roomTypeCode#", ctx("groupid.roomTypeCode", "KPVN"));

        Assert.assertEquals(got, "KPVN");
    }

    @Test(groups = {"unit", "framework"})
    @Story("an exact match still wins over a case-folded one")
    public void exactBeatsFolded() {
        String got = PlaceholderResolver.resolveAll(
                "${a.b}", ctx("a.b", "exact", "A.B", "folded"));

        Assert.assertEquals(got, "exact");
    }

    @Test(groups = {"unit", "framework"})
    @Story("an ambiguous fold resolves to nothing, not to a guess")
    @Description("""
            Two keys differing only by case, holding different values,
            mean the author meant something this cannot know. Carried
            across from RestUtilities' own index rather than reinvented.
            """)
    public void anAmbiguousFoldIsNotGuessed() {
        String got = PlaceholderResolver.resolveAll(
                "${a.x}", ctx("A.X", "one", "a.X", "two"));

        // unresolved refs are left in place as a visible marker
        Assert.assertEquals(got, "${a.x}");
    }

    @Test(groups = {"unit", "framework"})
    @Story("an unknown ref is still left in place")
    public void anUnknownRefIsLeftAsAMarker() {
        Assert.assertEquals(
                PlaceholderResolver.resolveAll("${nothing#here}", ctx()),
                "${nothing#here}");
    }

    @Test(groups = {"unit", "framework"})
    @Story("plain text is untouched")
    public void plainTextIsUntouched() {
        for (String s : new String[] {"ACTIVE", "2026-10-31", "", "12"}) {
            Assert.assertEquals(PlaceholderResolver.resolveAll(s, ctx()), s);
        }
    }

    @Test(groups = {"unit", "framework"})
    @Story("an empty collection is a MISS, not the value \"[]\"")
    @Description("""
            jp.getString() renders a path that resolved to an empty list
            as the two characters []. It is neither null nor empty, so it
            read as a successful extract and went to the wire as
            "roomTypeCode": "[]" -- and the server answered "String must
            match the specified regular expression", which sends the
            reader after the data rather than after the extract.
            """)
    public void anEmptyCollectionIsAMiss() throws Exception {
        Method m = Class.forName("com.hi.api.rest.utilities.RestUtilities")
                .getDeclaredMethod("jsonPathMiss", String.class);
        m.setAccessible(true);

        for (String miss : new String[] {null, "", "null", "[]", "{}"}) {
            Assert.assertTrue((Boolean) m.invoke(null, miss),
                    "should be a miss: " + miss);
        }
        for (String hit : new String[] {"KPVN", "0", "false", "[1]", "{\"a\":1}"}) {
            Assert.assertFalse((Boolean) m.invoke(null, hit),
                    "should be a value: " + hit);
        }
    }
}
