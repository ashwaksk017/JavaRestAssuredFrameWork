package com.ak.api.tests.framework;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import org.testng.Assert;
import org.testng.annotations.Test;

import com.ak.api.config.Config;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;

/**
 * Config env-var naming: the real name vs the one everybody guesses.
 *
 * <p>{@code Config.get} resolves an env var as {@code dots -> underscores,
 * uppercase} with no camelCase split, so {@code domains.fromDb.enabled} reads
 * {@code DOMAINS_FROMDB_ENABLED} and the natural-looking
 * {@code DOMAINS_FROM_DB_ENABLED} is silently ignored -- indistinguishable
 * from unset.</p>
 *
 * <p>No process environment is mutated. {@code Config.naiveEnvVarWarning}
 * takes the environment as a lookup function precisely so the warning can be
 * tested deterministically offline; the private miss-path caller simply binds
 * that parameter to {@code System::getenv}.</p>
 */
@Epic("Guards")
@Feature("Config env-var naming")
public class ConfigEnvVarNamingTest {

    /** A fake environment. Nothing here touches the real one. */
    private static Function<String, String> env(Map<String, String> entries) {
        return entries::get;
    }

    private static final Function<String, String> EMPTY_ENV = k -> null;

    // -----------------------------------------------------------------
    // Name computation
    // -----------------------------------------------------------------

    @Test(groups = {"unit", "guards"})
    @Description("realEnvVarName uppercases and swaps dots, but never splits camelCase")
    public void realEnvVarNameDoesNotSplitCamelCase() {
        Assert.assertEquals(Config.realEnvVarName("domains.fromDb.enabled"),
                "DOMAINS_FROMDB_ENABLED");
        Assert.assertEquals(Config.realEnvVarName("auth.tokenCache.ttlMs"),
                "AUTH_TOKENCACHE_TTLMS");
        Assert.assertEquals(Config.realEnvVarName("api_config.client_id"),
                "API_CONFIG_CLIENT_ID");
    }

    @Test(groups = {"unit", "guards"})
    @Description("naiveEnvVarName inserts an underscore at every lower->upper hump")
    public void naiveEnvVarNameInsertsUnderscoreAtHump() {
        Assert.assertEquals(Config.naiveEnvVarName("domains.fromDb.enabled"),
                "DOMAINS_FROM_DB_ENABLED");
        Assert.assertEquals(Config.naiveEnvVarName("xray.testExecutionKey"),
                "XRAY_TEST_EXECUTION_KEY");
    }

    @Test(groups = {"unit", "guards"})
    @Description("Keys with no camelCase hump have nothing to confuse: naive name is null")
    public void naiveEnvVarNameIsNullWithoutAHump() {
        Assert.assertNull(Config.naiveEnvVarName("api_config.client_id"));
        Assert.assertNull(Config.naiveEnvVarName("database.host"));
        Assert.assertNull(Config.naiveEnvVarName("db.url"));
    }

    @Test(groups = {"unit", "guards"})
    @Description("Multiple humps and trailing digit/unit suffixes are all split")
    public void naiveEnvVarNameHandlesMultipleHumps() {
        Assert.assertEquals(Config.naiveEnvVarName("rest.pollSalesforceIdIntervalMs"),
                "REST_POLL_SALESFORCE_ID_INTERVAL_MS");
        Assert.assertEquals(Config.naiveEnvVarName("test.isolateCtxPerMethod"),
                "TEST_ISOLATE_CTX_PER_METHOD");
        // The real name for the same key collapses all of that into one token.
        Assert.assertEquals(Config.realEnvVarName("rest.pollSalesforceIdIntervalMs"),
                "REST_POLLSALESFORCEIDINTERVALMS");
    }

    // -----------------------------------------------------------------
    // The warning itself
    // -----------------------------------------------------------------

    @Test(groups = {"unit", "guards"})
    @Description("No warning when the naive variant is not set -- this is the normal case")
    public void noWarningWhenNaiveVariantUnset() {
        Assert.assertNull(Config.naiveEnvVarWarning("domains.fromDb.enabled", EMPTY_ENV));
    }

    @Test(groups = {"unit", "guards"})
    @Description("Warning names BOTH spellings and says the naive one is ignored")
    public void warningNamesBothSpellings() {
        String msg = Config.naiveEnvVarWarning("domains.fromDb.enabled",
                env(Map.of("DOMAINS_FROM_DB_ENABLED", "true")));
        Assert.assertNotNull(msg, "naive variant was set; a warning was expected");
        Assert.assertTrue(msg.contains("DOMAINS_FROM_DB_ENABLED"),
                "warning must name the ignored spelling: " + msg);
        Assert.assertTrue(msg.contains("DOMAINS_FROMDB_ENABLED"),
                "warning must name the spelling that actually works: " + msg);
        Assert.assertTrue(msg.contains("domains.fromDb.enabled"),
                "warning must name the config key: " + msg);
        Assert.assertTrue(msg.contains("IGNORED"),
                "warning must state the naive variant is ignored: " + msg);
        Assert.assertFalse(msg.contains("true"),
                "warning must not echo the value (may be a secret): " + msg);
    }

    @Test(groups = {"unit", "guards"})
    @Description("A key with no hump never warns, even when that exact name is set")
    public void noWarningForKeyWithoutAHump() {
        Assert.assertNull(Config.naiveEnvVarWarning("api_config.client_id",
                env(Map.of("API_CONFIG_CLIENT_ID", "abc"))));
    }

    @Test(groups = {"unit", "guards"})
    @Description("A blank naive value is treated as unset -- no noise")
    public void noWarningWhenNaiveValueIsBlank() {
        Assert.assertNull(Config.naiveEnvVarWarning("domains.fromDb.enabled",
                env(Map.of("DOMAINS_FROM_DB_ENABLED", "   "))));
    }

    // -----------------------------------------------------------------
    // Documentation coverage
    // -----------------------------------------------------------------

    private static final Pattern CONFIG_GET = Pattern.compile(
            "Config\\.get(?:Bool|Int|Long)?\\(\"([a-zA-Z0-9_.]+)\"");

    /**
     * Scans hand-written sources for camelCase config keys.
     *
     * <p>{@code com/ak/api/support/} is deliberately excluded: it is generated
     * and gitignored, so its contents differ per machine and would make this
     * assertion non-deterministic.</p>
     */
    private static Set<String> camelCaseConfigKeys() throws IOException {
        Set<String> keys = new LinkedHashSet<>();
        for (String root : new String[] {"src/main/java", "src/test/java"}) {
            Path dir = Paths.get(root);
            if (!Files.isDirectory(dir)) continue;
            try (Stream<Path> files = Files.walk(dir)) {
                for (Path f : (Iterable<Path>) files
                        .filter(Files::isRegularFile)
                        .filter(f -> f.toString().endsWith(".java"))
                        .filter(f -> !f.toString().replace('\\', '/')
                                .contains("/com/ak/api/support/"))::iterator) {
                    String src = new String(Files.readAllBytes(f), StandardCharsets.UTF_8);
                    Matcher m = CONFIG_GET.matcher(src);
                    while (m.find()) {
                        String key = m.group(1);
                        if (key.contains(".") && Config.naiveEnvVarName(key) != null) {
                            keys.add(key);
                        }
                    }
                }
            }
        }
        return keys;
    }

    @Test(groups = {"unit", "guards"})
    @Description("Every camelCase config key is listed in the CreateTestCase.md env-var table")
    public void documentedTableCoversEveryCamelCaseKey() throws IOException {
        Path doc = Paths.get("CreateTestCase.md");
        Assert.assertTrue(Files.isRegularFile(doc),
                "CreateTestCase.md not found at " + doc.toAbsolutePath()
                        + " -- run this suite from the project root");
        String text = new String(Files.readAllBytes(doc), StandardCharsets.UTF_8);

        Set<String> keys = camelCaseConfigKeys();
        Assert.assertFalse(keys.isEmpty(), "source scan found no camelCase config keys at all");

        StringBuilder missing = new StringBuilder();
        for (String key : keys) {
            if (!text.contains("`" + key + "`")) {
                missing.append("\n  key not documented:      ").append(key);
            } else if (!text.contains("`" + Config.realEnvVarName(key) + "`")) {
                missing.append("\n  real env var missing:    ")
                        .append(Config.realEnvVarName(key));
            }
        }
        Assert.assertEquals(missing.length(), 0,
                "CreateTestCase.md section 8 env-var table has drifted from the code."
                        + " Regenerate it." + missing);
    }
}
