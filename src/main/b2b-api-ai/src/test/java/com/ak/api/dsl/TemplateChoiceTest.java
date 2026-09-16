package com.ak.api.dsl;

import java.util.HashMap;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Which body a hand-written phase sends.
 *
 * <p>The rule lives in {@code CustomerOnboarding.chooseTemplate}, which is
 * package-private precisely so it can be checked here without a live HTTP
 * exchange. Getting this wrong is silent -- the request still succeeds, it
 * just carries the wrong payload -- so it is worth pinning down.</p>
 */
@Epic("Manual")
@Feature("Template selection")
public class TemplateChoiceTest {

    private static final String PHASE = "createProgramAccount";

    @AfterMethod(alwaysRun = true)
    public void clearSuiteOverride() {
        System.clearProperty("manual.suite");
    }

    @Test(groups = {"unit"})
    @Story("an explicit Template wins over the CSV column")
    @Description("using(...) is the author stating intent; it must not be overridden by data.")
    public void explicitTemplateBeatsTheCsvColumn() {
        System.setProperty("manual.suite", "programaccountregression");
        Map<String, String> row = new HashMap<>();
        row.put("template_" + PHASE, "templates/from/the/csv.json");

        String chosen = CustomerOnboarding.chooseTemplate(
                Template.amexInviteLinkAccount, row, PHASE);

        Assert.assertNotEquals(chosen, "templates/from/the/csv.json", chosen);
        Assert.assertEquals(chosen, Template.amexInviteLinkAccount.resolve(), chosen);
    }

    @Test(groups = {"unit"})
    @Story("the CSV column is used when no Template is named")
    public void csvColumnUsedWhenNoExplicitTemplate() {
        Map<String, String> row = new HashMap<>();
        row.put("template_" + PHASE, "templates/from/the/csv.json");

        Assert.assertEquals(
                CustomerOnboarding.chooseTemplate(null, row, PHASE),
                "templates/from/the/csv.json");
    }

    @Test(groups = {"unit"})
    @Story("nothing chosen leaves the converter default in place")
    @Description("""
            null means "do not call RestStep.template(...)", so the step keeps
            whatever the converter gave it. An empty CSV cell must behave the
            same as an absent column, not as an empty template path.
            """)
    public void absentOrEmptyMeansNoOverride() {
        Assert.assertNull(CustomerOnboarding.chooseTemplate(null, null, PHASE));

        Map<String, String> empty = new HashMap<>();
        Assert.assertNull(CustomerOnboarding.chooseTemplate(null, empty, PHASE));

        Map<String, String> blank = new HashMap<>();
        blank.put("template_" + PHASE, "");
        Assert.assertNull(CustomerOnboarding.chooseTemplate(null, blank, PHASE));
    }

    @Test(groups = {"unit"})
    @Story("the column is per phase, not global")
    @Description("A template named for one phase must not bleed into another.")
    public void columnIsScopedToItsPhase() {
        Map<String, String> row = new HashMap<>();
        row.put("template_someOtherPhase", "templates/other/phase.json");

        Assert.assertNull(CustomerOnboarding.chooseTemplate(null, row, PHASE));
        // positive control: the matching phase DOES pick it up
        Assert.assertEquals(
                CustomerOnboarding.chooseTemplate(null, row, "someOtherPhase"),
                "templates/other/phase.json");
    }
}
