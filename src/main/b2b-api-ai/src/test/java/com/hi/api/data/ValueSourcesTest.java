package com.hi.api.data;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.Test;

/**
 * Where a story test's value comes from when its data sheet does not have
 * it (ValueSources), and ManualBody asking those sources in the converted
 * path's order: configuration < data sheet row < captured values. Before,
 * only the row was asked, so a story test sent nothing (or stopped) where a
 * converted test sent the configured value.
 *
 * <p>No HTTP, no project files: the scans run over temporary folders.</p>
 */
public class ValueSourcesTest {

    private static final String[] PROPS = {
            "guard_enroll_source_code", "guard_enroll.source_code", "test_data.guard_only_in_test_data",
            "guard.enroll.prop"};

    @AfterMethod(alwaysRun = true)
    public void clear() {
        for (String k : PROPS) {
            System.clearProperty(k);
        }
    }

    @Test(groups = {"guards"})
    public void aNameIsLookedUpUnderItsDottedAndTestDataForms() {
        List<String> keys = ValueSources.configKeysFor("enroll_source_code");
        Assert.assertEquals(keys.get(0), "enroll_source_code", "its own name first");
        Assert.assertTrue(keys.contains("enroll.source_code"), keys.toString());
        Assert.assertTrue(keys.contains("enroll_source.code"), keys.toString());
        Assert.assertTrue(keys.contains("enroll.source.code"), keys.toString());
        Assert.assertEquals(keys.get(keys.size() - 1), "test_data.enroll_source_code", "test_data last");
    }

    @Test(groups = {"guards"})
    public void theConfigurationIsAskedUnderEachForm() {
        Assert.assertNull(ValueSources.fromConfig("guard_enroll_source_code"), "nothing configured: null");
        System.setProperty("guard_enroll.source_code", " ABC123 ");
        String[] got = ValueSources.fromConfig("guard_enroll_source_code");
        Assert.assertNotNull(got);
        Assert.assertEquals(got[0], "ABC123", "trimmed");
        Assert.assertEquals(got[1], "guard_enroll.source_code", "says which key it came from");

        System.setProperty("test_data.guard_only_in_test_data", "x1");
        Assert.assertEquals(ValueSources.fromConfig("guard_only_in_test_data")[1],
                "test_data.guard_only_in_test_data");
        Assert.assertNull(ValueSources.fromConfig(" "));
        Assert.assertNull(ValueSources.fromConfig(null));
    }

    @Test(groups = {"guards"})
    public void theEnrolCodesAreTheOnesMostConvertedBodiesSend() throws Exception {
        Path dir = Files.createTempDirectory("vs-templates");
        write(dir.resolve("SuiteA/enroll1.json"), "{\"enrollSourceCode\": \"SRC1\", \"propCode\": \"PRP1\"}");
        write(dir.resolve("SuiteA/enroll2.json"), "{\"enrollSourceCode\":\"SRC1\",\"propCode\":\"PRP1\"}");
        write(dir.resolve("SuiteB/enroll.json"), "{\"enrollSourceCode\": \"SRC2\", \"propCode\": \"PRP2\"}");
        // a body naming a column, not carrying a code, does not count
        write(dir.resolve("SuiteC/enroll.json"), "{\"enrollSourceCode\": \"#enrollSourceCode#\", \"propCode\": \"#propCode#\"}");
        write(dir.resolve("SuiteC/other.json"), "{\"name\": \"no codes here\"}");

        String[] got = ValueSources.scanEnrollCodes(dir);
        Assert.assertEquals(got[0], "SRC1");
        Assert.assertEquals(got[1], "PRP1");
        Assert.assertTrue(got[2].startsWith("2 converted enrol bodies, e.g. templates/SuiteA/"), got[2]);

        Assert.assertEquals(ValueSources.scanEnrollCodes(dir.resolve("missing")).length, 0, "no folder: none");
    }

    @Test(groups = {"guards"})
    public void theDomainIsTheOneTheConvertedDataUsesMost() throws Exception {
        Path dir = Files.createTempDirectory("vs-defaults");
        write(dir.resolve("a.json"), "{\"Properties.Hardcodeddomain\": \"www.Example.test\"}");
        write(dir.resolve("b.json"), "{\"Properties.Domain\": \"example.test\", \"Other\": \"other.test\"}");
        write(dir.resolve("c.json"), "{\"Properties.Domain\": \"second.test\"}");
        write(dir.resolve("d.json"), "{\"Properties.Domain\": \"not a domain\"}");
        Assert.assertEquals(ValueSources.scanDomain(dir), "example.test");
        Assert.assertEquals(ValueSources.scanDomain(dir.resolve("missing")), "");
    }

    @Test(groups = {"guards"})
    public void aBodyTakesTheConfigurationThenTheRowThenWhatTheTestCaptured() throws Exception {
        System.setProperty("guard_enroll.source_code", "CFG1");
        Map<String, String> row = new LinkedHashMap<>();
        row.put("Properties_Email", "someone@example.test");
        Map<String, String> ctx = new LinkedHashMap<>();
        ctx.put("captured_member_id", "778899");

        String body = ManualBody.render("templates/manual/value_sources_example.json", row, ctx);
        Assert.assertTrue(body.contains("\"fromRow\": \"someone@example.test\""), body);
        Assert.assertTrue(body.contains("\"fromCaptured\": \"778899\""), body);
        Assert.assertTrue(body.contains("\"fromConfig\": \"CFG1\""), body);

        // the row wins over the configuration, a captured value over both
        row.put("guard_enroll_source_code", "ROW1");
        body = ManualBody.render("templates/manual/value_sources_example.json", row, ctx);
        Assert.assertTrue(body.contains("\"fromConfig\": \"ROW1\""), body);
        ctx.put("guard_enroll_source_code", "CTX1");
        body = ManualBody.render("templates/manual/value_sources_example.json", row, ctx);
        Assert.assertTrue(body.contains("\"fromConfig\": \"CTX1\""), body);
    }

    @Test(groups = {"guards"})
    public void aNameNoSourceHasStopsTheBody() {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("Properties_Email", "someone@example.test");
        try {
            ManualBody.render("templates/manual/value_sources_example.json", row, new LinkedHashMap<>());
            Assert.fail("a #name# with no value must not be sent as \"null\"");
        } catch (Exception e) {
            Assert.assertTrue(String.valueOf(e.getMessage()).contains("captured_member_id")
                    || String.valueOf(e.getMessage()).contains("guard_enroll_source_code"), String.valueOf(e));
        }
    }

    @Test(groups = {"guards"})
    public void aGeneratorNothingKnowsStopsTheBody() {
        try {
            ManualBody.render("templates/manual/value_sources_leftover.json", new LinkedHashMap<>(), new LinkedHashMap<>());
            Assert.fail("<<no_such_generator>> must not reach the server verbatim");
        } catch (Exception e) {
            Assert.assertTrue(String.valueOf(e.getMessage()).contains("<<no_such_generator>>"), String.valueOf(e));
        }
    }

    @Test(groups = {"guards"})
    public void aFieldTakesTheValueTheConvertedBodiesSendMost() throws Exception {
        Path dir = Files.createTempDirectory("vs-literals");
        write(dir.resolve("A/enroll1.json"), "{\"address\": {\"company\": \"Plain Co\"}}");
        write(dir.resolve("A/enroll2.json"), "{\"address\": {\"company\":\"Plain Co\"}}");
        write(dir.resolve("B/enroll.json"), "{\"address\": {\"company\": \"Other Co\"}}");
        // a placeholder or an empty string is not a value
        write(dir.resolve("C/enroll.json"), "{\"company\": \"#tpl_company#\", \"x\": {\"company\": \"\"}}");
        write(dir.resolve("C/more.json"), "{\"company\": \"<<company>>\"}");
        String[] got = ValueSources.scanLiteral(dir, "company");
        Assert.assertEquals(got[0], "Plain Co");
        Assert.assertTrue(got[1].startsWith("2 converted bodies, e.g. templates/A/"), got[1]);
        Assert.assertEquals(ValueSources.scanLiteral(dir, "nowhere").length, 0);
    }

    @Test(groups = {"guards"})
    public void aPersonOrACredentialIsNeverTakenFromAnotherSuitesBody() throws Exception {
        Path dir = Files.createTempDirectory("vs-private");
        write(dir.resolve("A/b.json"), "{\"password\": \"Hunter2!\", \"emailAddress\": \"a@example.test\","
                + " \"username\": \"someone\", \"guestId\": \"123\"}");
        for (String f : new String[] {"password", "emailAddress", "username", "guestId"}) {
            Assert.assertEquals(ValueSources.scanLiteral(dir, f).length, 0, f);
            Assert.assertNull(ValueSources.convertedLiteral(f), f);
        }
    }

    @Test(groups = {"guards"})
    public void aPlaceholdersFieldIsTheJsonKeyItIsTheValueOf() {
        String t = "{\"address\": {\"company\": \"#enroll_company#\"}, \"ratePlanName\" : \"#rate_plan_name#\","
                + " \"note\": \"see #inline# here\"}";
        Assert.assertEquals(ManualBody.fieldOf(t, "enroll_company"), "company");
        Assert.assertEquals(ManualBody.fieldOf(t, "rate_plan_name"), "ratePlanName");
        Assert.assertNull(ManualBody.fieldOf(t, "inline"), "part of a string: no field");
        Assert.assertNull(ManualBody.fieldOf(t, "absent"));
    }

    private static void write(Path p, String text) throws Exception {
        Files.createDirectories(p.getParent());
        Files.write(p, text.getBytes(StandardCharsets.UTF_8));
    }
}
