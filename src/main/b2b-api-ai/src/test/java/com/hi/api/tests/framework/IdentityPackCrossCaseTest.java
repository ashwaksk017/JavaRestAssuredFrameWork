package com.hi.api.tests.framework;

import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.hi.api.data.PlaceholderResolver;
import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.support.ImportedScenario;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Runtime lock for the two ReadyAPI enroll conventions that share one
 * identity pack: 6801 uses {@code generatedemailAddress1} on member
 * enroll; 7816 uses {@code Properties.Email} for the member guest.
 * Overlay must fix 7816 without rewriting 6801 create-account's owner
 * email or the 6801 member placeholder.
 */
@Epic("API Automation")
@Feature("Identity pack")
public class IdentityPackCrossCaseTest {

    private static final String OWNER_ENROLL =
            "{\"username\":\"#Properties_username#\","
            + "\"email\":{\"emailAddress\":\"#Properties_generatedemailAddress#\"}}";
    private static final String MEMBER_ENROLL_6801 =
            "{\"username\":\"#Properties_usernamemember#\","
            + "\"email\":{\"emailAddress\":\"#Properties_generatedemailAddress1#\"}}";
    private static final String MEMBER_ENROLL_7816 =
            "{\"username\":\"#Properties_usernamemember#\","
            + "\"email\":{\"emailAddress\":\"#Properties_Email#\"}}";
    private static final String CREATE_ACCOUNT =
            "{\"contactInfo\":{\"ownerEmailAddress\":\"#Properties_Email#\"},"
            + "\"ownerInfo\":{\"contactInfo\":{\"emailAddress\":"
            + "\"#Properties_generatedemailAddress#\"}}}";

    private static String resolve(Map<String, String> ctx, String step, String template)
            throws Exception {
        Map<String, String> resolveCtx = ImportedScenario.ctxForStep(ctx, step);
        String mapped = RestUtilities.mapJsonValues(
                template, ImportedScenario.mergedRow(new HashMap<>(), resolveCtx), false);
        return PlaceholderResolver.resolveAll(mapped, resolveCtx);
    }

    @Test(groups = {"unit"})
    @Story("6801 and 7816 enroll emails stay distinct after overlay")
    @Description("One identity pack: owner enroll, 6801 member enroll, 7816 member enroll, and create-account owner email.")
    public void onePack_servesBothEnrollConventions_andCreateAccountKeepsOwner()
            throws Exception {
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx);
        String ownerEmail = ctx.get("Properties.Email");
        String memberEmail = ctx.get("Properties.generatedemailAddress1");
        Assert.assertNotNull(ownerEmail);
        Assert.assertNotNull(memberEmail);
        Assert.assertNotEquals(ownerEmail, memberEmail);

        String ownerBody = resolve(ctx, "HHonorsEnroll", OWNER_ENROLL);
        Assert.assertTrue(ownerBody.contains(ownerEmail), ownerBody);
        Assert.assertFalse(ownerBody.contains(memberEmail), ownerBody);

        String member6801 = resolve(ctx, "MemberHHonorsEnroll", MEMBER_ENROLL_6801);
        Assert.assertTrue(member6801.contains(memberEmail), member6801);
        Assert.assertFalse(member6801.contains(ownerEmail), member6801);

        String member7816 = resolve(ctx, "MemberHHonorsEnroll", MEMBER_ENROLL_7816);
        Assert.assertTrue(member7816.contains(memberEmail), member7816);
        Assert.assertFalse(member7816.contains(ownerEmail), member7816);

        String create = resolve(ctx, "http_request_200_1", CREATE_ACCOUNT);
        Assert.assertTrue(create.contains(ownerEmail), create);
        Assert.assertFalse(create.contains("\"ownerEmailAddress\":\"" + memberEmail + "\""),
                create);

        Assert.assertEquals(ctx.get("Properties.Email"), ownerEmail,
                "session ctx must still hold owner email after member overlay");
        Assert.assertEquals(ctx.get("Properties.generatedemailAddress1"), memberEmail);
    }

    @Test(groups = {"unit"})
    @Story("overlay is enroll-step scoped")
    @Description("CreatePendingAccountmember / confirmValidation must not inherit the member-enroll Email overlay.")
    public void overlayDoesNotApplyToNonEnrollMemberSteps() {
        Map<String, String> ctx = new HashMap<>();
        ImportedScenario.regenRandomProperties(ctx);
        String owner = ctx.get("Properties.Email");
        Assert.assertSame(ImportedScenario.ctxForStep(ctx, "http_request_200_1"), ctx);
        Assert.assertSame(ImportedScenario.ctxForStep(ctx,
                "http_request_200_3-CreatePendingAccountmember"), ctx);
        Assert.assertSame(ImportedScenario.ctxForStep(ctx, "http_confirmValidation_200"), ctx);
        Assert.assertEquals(
                ImportedScenario.ctxForStep(ctx, "http_request_200_1").get("Properties.Email"),
                owner);
        Assert.assertFalse(ImportedScenario.isMemberEnrollStep(
                "http_request_200_3-CreatePendingAccountmember"));
    }
}
