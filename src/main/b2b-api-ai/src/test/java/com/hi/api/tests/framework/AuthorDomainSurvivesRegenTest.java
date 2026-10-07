package com.hi.api.tests.framework;

import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * A domain the ReadyAPI script typed as a string literal is the subject of
 * the test, and the pre-request identity regeneration must not replace it.
 *
 * <p>amexBackbookDuplicateManagedAccount sets
 * {@code Properties.Domain = "explorer.de"} and expects the account to come
 * back {@code pending / duplicateManagedAccount}. The regeneration decided
 * the domain from the saved row, did not recognise a managed-account domain
 * as one to keep, and enrolled the guest on a random one.</p>
 */
@Epic("API Automation")
@Feature("Identity pack")
public class AuthorDomainSurvivesRegenTest {

    private static Map<String, String> rowFor(String domain) {
        Map<String, String> row = new HashMap<>();
        row.put("Properties.Domain", domain);
        row.put("Properties.Email", "prjut@" + domain);
        return row;
    }

    @Test(groups = {"unit"})
    @Story("A pinned domain is kept and the identity is built on it")
    @Description("Domain, the owner email and websiteDomain all follow the literal the script set.")
    public void pinnedDomain_isKept_andEmailsFollowIt() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain", "explorer.de");
        ImportedScenario.pinAuthorDomain(ctx);

        ImportedScenario.regenRandomProperties(ctx, rowFor("explorer.de"));

        Assert.assertEquals(ctx.get("Properties.Domain"), "explorer.de");
        Assert.assertEquals(ctx.get("Properties.domain"), "explorer.de");
        Assert.assertEquals(ctx.get("Properties.websiteDomain"), "explorer.de");
        Assert.assertTrue(ctx.get("Properties.Email").endsWith("@explorer.de"),
                "owner email left the author's domain: " + ctx.get("Properties.Email"));
    }

    @Test(groups = {"unit"})
    @Story("Without the pin the same row is regenerated")
    @Description("Proves the test above passes because of the pin, not because the row keeps explorer.de anyway.")
    public void sameRowWithoutPin_isRegenerated() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain", "explorer.de");

        ImportedScenario.regenRandomProperties(ctx, rowFor("explorer.de"));

        Assert.assertNotEquals(ctx.get("Properties.Domain"), "explorer.de",
                "the row alone already keeps this domain -- the pin test proves nothing");
    }

    @Test(groups = {"unit"})
    @Story("The pin is verbatim")
    @Description("Case and a leading www. are the author's and are not normalised away.")
    public void pinnedDomain_isVerbatim() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain", "PeaLCenteR.oRg");
        ImportedScenario.pinAuthorDomain(ctx);

        ImportedScenario.regenRandomProperties(ctx, rowFor("pealcenter.org"));

        Assert.assertEquals(ctx.get("Properties.Domain"), "PeaLCenteR.oRg");
    }

    @Test(groups = {"unit"})
    @Story("Pinning nothing changes nothing")
    @Description("A hook that pins before any domain is in ctx must not freeze an empty value.")
    public void pinWithNoDomain_isIgnored() {
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.pinAuthorDomain(ctx);
        Assert.assertNull(ctx.get(ImportedScenario.AUTHOR_DOMAIN));

        ImportedScenario.regenRandomProperties(ctx, new HashMap<>());

        String domain = ctx.get("Properties.Domain");
        Assert.assertTrue(domain != null && !domain.isEmpty(), "regen produced no domain");
    }

    @Test(groups = {"unit"})
    @Story("A second regen in the same attempt keeps the pin")
    @Description("Two enroll steps in one case both run the regeneration.")
    public void pinSurvivesASecondRegen() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain", "explorer.de");
        ImportedScenario.pinAuthorDomain(ctx);

        ImportedScenario.regenRandomProperties(ctx, rowFor("explorer.de"));
        ImportedScenario.regenRandomProperties(ctx, rowFor("explorer.de"));

        Assert.assertEquals(ctx.get("Properties.Domain"), "explorer.de");
    }
}
