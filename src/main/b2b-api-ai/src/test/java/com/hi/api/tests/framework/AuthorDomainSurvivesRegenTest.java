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
    @Story("An owner email the script builds on its own domain goes out on that domain")
    @Description("leadspace: def hardcodedemail = generatedUser + \"@\" + \"apollomessenger.com\" with "
            + "emailDomains [\"apollomessenger.com\"]. hardcodedemail is a named identity key, so the "
            + "generic passes skipped it, and the block that writes it only ran for a frozen domain. "
            + "The pack's value on a random allowed domain went out: 400/503, nine tests.")
    public void hardcodedEmail_followsThePinnedDomain() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain", "apollomessenger.com");
        // what the generator pack left: an address on its own shared domain
        ctx.put("Properties.hardcodedemail", "oewqis@thetustingroup.com");
        ImportedScenario.pinAuthorDomain(ctx);
        Map<String, String> row = rowFor("apollomessenger.com");
        row.put("Properties.hardcodedemail", "0sl8l@apollomessenger.com");
        row.put("Properties.Hardcodeddomain", "rteet.com");

        ImportedScenario.regenRandomProperties(ctx, row);

        String sent = ctx.get("Properties.hardcodedemail");
        Assert.assertTrue(sent.endsWith("@apollomessenger.com"),
                "the owner email left the case's email domain: " + sent);
        Assert.assertNotEquals(sent, "0sl8l@apollomessenger.com",
                "ReadyAPI generates a new user each run; the saved address is last run's");
        Assert.assertNotEquals(sent, ctx.get("Properties.Email"),
                "and it is its own address, not a second name for the owner");
        Assert.assertEquals(ctx.get("Properties.Hardcodedemail"), sent, "both spellings");
    }

    @Test(groups = {"unit"})
    @Story("The pinned domain is matched without regard to case")
    @Description("The suite types peaLCenteR.oRg; ReadyAPI saved the owner on PeaLCenteR.oRg and the row "
            + "holds pealcenter.org. All three are one domain.")
    public void hardcodedEmail_matchesThePinnedDomainIgnoringCase() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain", "peaLCenteR.oRg");
        ctx.put("Properties.hardcodedemail", "abc@thetustingroup.com");
        ImportedScenario.pinAuthorDomain(ctx);
        Map<String, String> row = rowFor("pealcenter.org");
        row.put("Properties.hardcodedemail", "5rruy@PeaLCenteR.oRg");

        ImportedScenario.regenRandomProperties(ctx, row);

        Assert.assertTrue(ctx.get("Properties.hardcodedemail").toLowerCase().endsWith("@pealcenter.org"),
                ctx.get("Properties.hardcodedemail"));
    }

    @Test(groups = {"unit"})
    @Story("A saved address on some OTHER domain keeps that domain")
    @Description("NEGATIVE CONTROL: only an address saved on the case's identity domain moves to it. One "
            + "the author saved elsewhere stays on the author's domain, with a fresh local part.")
    public void hardcodedEmail_onAForeignDomain_keepsIt() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain", "apollomessenger.com");
        ctx.put("Properties.hardcodedemail", "abc@thetustingroup.com");
        ImportedScenario.pinAuthorDomain(ctx);
        Map<String, String> row = rowFor("apollomessenger.com");
        row.put("Properties.hardcodedemail", "someone@partner-owned.example");

        ImportedScenario.regenRandomProperties(ctx, row);

        Assert.assertTrue(ctx.get("Properties.hardcodedemail").endsWith("@partner-owned.example"),
                ctx.get("Properties.hardcodedemail"));
    }

    @Test(groups = {"unit"})
    @Story("An address the script never writes is the author's and is left exactly as saved")
    @Description("NEGATIVE CONTROL: with no generator value in ctx the Properties seed has put the row's "
            + "own address there. Nothing says it is per-run data, so it is not regenerated.")
    public void hardcodedEmail_theScriptNeverWrote_isUntouched() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain", "apollomessenger.com");
        ctx.put("Properties.hardcodedemail", "fixed.person@apollomessenger.com");   // seeded from the row
        ImportedScenario.pinAuthorDomain(ctx);
        Map<String, String> row = rowFor("apollomessenger.com");
        row.put("Properties.hardcodedemail", "fixed.person@apollomessenger.com");

        ImportedScenario.regenRandomProperties(ctx, row);

        Assert.assertEquals(ctx.get("Properties.hardcodedemail"), "fixed.person@apollomessenger.com");
    }

    @Test(groups = {"unit"})
    @Story("updatedmailAddress follows the same rule, and a key nobody has is not invented")
    public void updatedMailAddress_followsIdentity_andAbsentKeysStayAbsent() {
        Map<String, String> ctx = new HashMap<>();
        ctx.put("Properties.Domain", "apollomessenger.com");
        ctx.put("Properties.updatedmailAddress", "pack@thetustingroup.com");
        ImportedScenario.pinAuthorDomain(ctx);
        Map<String, String> row = rowFor("apollomessenger.com");
        row.put("Properties.updatedmailAddress", "197462857@apollomessenger.com");

        ImportedScenario.regenRandomProperties(ctx, row);

        Assert.assertTrue(ctx.get("Properties.updatedmailAddress").endsWith("@apollomessenger.com"),
                ctx.get("Properties.updatedmailAddress"));
        Assert.assertNull(ctx.get("Properties.hardcodedemail"), "not in the row, not generated: not invented");
        Assert.assertNull(ctx.get("Properties.updatedemail"));
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
