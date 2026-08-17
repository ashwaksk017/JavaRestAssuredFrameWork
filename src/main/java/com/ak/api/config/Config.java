// =============================================================================
// Config
// -----------------------------------------------------------------------------
// Single source of truth for environment-specific values. Hierarchy (highest
// priority first):
//   1. System properties (-Dkey=value on mvn command line)
//   2. Environment variables (case-insensitive, dots converted to underscores)
//   3. program_configuration.json (nested per-env JSON; keys flattened per
//      the active env, e.g. `stg.api_config.client_id` accessed as
//      `api_config.client_id`). Legacy aliases surface common lookups
//      (c_id -> api_config.client_id, db.url -> database.host/port/schema, ...).
//   4. application-{env}.properties on the classpath
//   5. application.properties on the classpath
//   6. Built-in defaults (fallback in accessors)
//
// The active env is chosen by (highest priority first): -Denv, TEST_ENV env
// variable, "env" property in application.properties, else "qa".
//
// program_configuration.json format (matches the reference framework layout):
// {
//   "stg": {
//     "database":   { "host", "port", "schema", "username", "password" },
//     "api_config": { "api_end_point", "version", "client_id",
//                      "client_secret", "token_end_point", "token_route",
//                      "grant_type", ... },
//     "sf_config":  { ... salesforce ... },
//     "kafka_config": { ... },
//     "xray_api_config": { ... }
//   },
//   "prod": { ... },
//   ...
// }
// Flattened: `api_config.client_id`, `database.host`, `sf_config.assertion`, ...
// Derived keys (auto-computed from other JSON fields):
//   baseUrl  <- api_config.api_end_point + "/" + api_config.version
//   db.url   <- jdbc:postgresql://<database.host>:<database.port>/<database.schema>
//   db.user  <- database.username
//   db.password <- database.password
//   db.driver   <- org.postgresql.Driver (default; override in JSON if needed)
// =============================================================================

package com.ak.api.config;

import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;
import java.util.Properties;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

public final class Config {

    private static final Properties props = new Properties();
    // Flattened env-scoped view of program_configuration.json. Keys are
    // dot-joined (e.g. `api_config.client_id`). Empty when the file is
    // absent -- other config sources take over.
    private static final Map<String, String> programConfig = new HashMap<>();
    // Legacy alias table for backward compatibility with existing tests
    // and the ra_converter-emitted TestSupport.mergedRow lookups that
    // expect flat keys like `c_id`, `c_sec`, `salesforce_assertion`.
    private static final Map<String, String> LEGACY_ALIASES = new HashMap<>();
    static {
        // Aliases point at the CANONICAL nested key inside program_config.
        // `get()` resolves the alias transparently.
        LEGACY_ALIASES.put("c_id",                 "api_config.client_id");
        LEGACY_ALIASES.put("c_sec",                "api_config.client_secret");
        LEGACY_ALIASES.put("client_id",            "api_config.client_id");
        LEGACY_ALIASES.put("client_secret",        "api_config.client_secret");
        LEGACY_ALIASES.put("grant_type",           "api_config.grant_type");
        LEGACY_ALIASES.put("token_end_point",      "api_config.token_end_point");
        LEGACY_ALIASES.put("token_route",          "api_config.token_route");
        LEGACY_ALIASES.put("api_end_point",        "api_config.api_end_point");
        LEGACY_ALIASES.put("version",              "api_config.version");
        LEGACY_ALIASES.put("salesforce_assertion", "sf_config.assertion");
        LEGACY_ALIASES.put("username",             "sf_config.ui_username");
        LEGACY_ALIASES.put("password",             "sf_config.ui_password");
        LEGACY_ALIASES.put("xray.baseUrl",         "xray_api_config.api_end_point");
        LEGACY_ALIASES.put("xray.token",           "xray_api_config.token");
        LEGACY_ALIASES.put("xray.testExecutionKey","xray_api_config.testExecutionKey");
    }

    static {
        // Base: application.properties
        loadClasspathIntoProps("application.properties");

        // Env overlay: application-{env}.properties
        String envName = detectEnv();
        loadClasspathIntoProps("application-" + envName + ".properties");

        // Nested per-env JSON overlay (matches the reference framework's
        // `program_configuration.json` layout). Flat keys land in
        // `programConfig` scoped to the active env. Silent when absent.
        loadProgramConfiguration(envName);

        // Diagnostic banner: prints the active env, whether program_config
        // JSON loaded, and the effective baseUrl WITH its source. Answers
        // "why did baseUrl fall back to the jsonplaceholder default?"
        // without any debugger step-through.
        logStartupDiagnostics(envName);
    }

    private static void logStartupDiagnostics(String envName) {
        System.out.println("[Config] ============================================================");
        System.out.println("[Config] Active env: " + envName + "  (source: " + detectEnvSource() + ")");
        System.out.println("[Config] program_configuration.json: "
                + (programConfig.isEmpty()
                        ? "NOT LOADED (file missing OR active env block missing)"
                        : "loaded (" + programConfig.size() + " keys under `" + envName + "` block)"));
        // Mirror the new precedence order in baseUrl(): explicit
        // `baseUrl` wins over derived, derived wins over fallback.
        // Order MUST match baseUrl() -- keep them in lockstep or the
        // reported source will lie when both are set.
        String explicit = get("baseUrl", null);
        String derived = deriveBaseUrl();
        String effective = baseUrl();
        String source;
        if (isNonEmpty(System.getProperty("baseUrl"))) {
            source = "-DbaseUrl system property";
        } else if (isNonEmpty(System.getenv("BASEURL"))) {
            source = "BASEURL env var";
        } else if (isNonEmpty(props.getProperty("baseUrl"))) {
            source = "application.properties (or per-env overlay)";
        } else if (isNonEmpty(explicit)) {
            // Rare, but possible via a future config source added to
            // get() -- keep this branch so future additions get a
            // sensible label instead of falling through to "derived".
            source = "`baseUrl` key resolved via Config.get() chain";
        } else if (isNonEmpty(derived)) {
            // Note the derivation inputs themselves can come from -D
            // overrides (get() walks the full chain) -- so this label
            // is deliberately generic. The api_config.* peek block
            // below shows the actual values that produced this URL.
            source = "derived from api_config.api_end_point + api_config.version";
        } else {
            source = "BUILT-IN FALLBACK (jsonplaceholder) -- your config is not reaching this code";
        }
        System.out.println("[Config] Effective baseUrl: " + effective);
        System.out.println("[Config]   source: " + source);
        // Peek a handful of important api_config.* keys (masked for
        // secrets) so misconfiguration is obvious at a glance.
        String[] peek = {
            "api_config.api_end_point", "api_config.version",
            "api_config.token_end_point", "api_config.token_route",
            "api_config.client_id", "api_config.client_secret",
            "api_config.grant_type",
        };
        System.out.println("[Config] api_config.* keys visible:");
        for (String k : peek) {
            String v = programConfig.get(k);
            String display;
            if (v == null || v.isEmpty()) {
                display = "(missing)";
            } else if (k.endsWith("client_secret") || k.endsWith("password")) {
                display = "***" + (v.length() > 4 ? v.substring(v.length() - 4) : "") + " (len=" + v.length() + ")";
            } else {
                display = v;
            }
            System.out.println("[Config]   " + k + " = " + display);
        }
        // Loud warning banner when the run is CERTAIN to fail against the
        // fallback baseUrl (default jsonplaceholder + no api_config keys).
        // Every REST call will 404 -- surface the misconfiguration so it
        // isn't blamed on the target API.
        if ("BUILT-IN FALLBACK (jsonplaceholder) -- your config is not reaching this code".equals(source)
                || (!isNonEmpty(derived) && "https://jsonplaceholder.typicode.com".equals(effective))) {
            System.out.println("[Config] ");
            System.out.println("[Config] *** WARNING ***  Tests will run against JSONPLACEHOLDER (public sample API)");
            System.out.println("[Config] *** WARNING ***  because program_configuration.json is missing or empty.");
            System.out.println("[Config] *** WARNING ***  Expect HTTP 404 on every request. To point tests at your");
            System.out.println("[Config] *** WARNING ***  real env, put a program_configuration.json under");
            System.out.println("[Config] *** WARNING ***  src/main/resources/ (see program_configuration.example.json),");
            System.out.println("[Config] *** WARNING ***  or set -Dapi_config.api_end_point=... on the mvn command line.");
        }
        System.out.println("[Config] ============================================================");
    }

    private static String detectEnvSource() {
        if (isNonEmpty(System.getProperty("env"))) return "-Denv system property";
        if (isNonEmpty(System.getenv("TEST_ENV"))) return "TEST_ENV env var";
        if (isNonEmpty(props.getProperty("env"))) return "application.properties `env`";
        return "default (qa)";
    }

    private Config() {
    }

    // ---------------------------------------------------------------------
    // Public accessors -- typed, with defaults
    // ---------------------------------------------------------------------

    public static String env() {
        return detectEnv();
    }

    public static String baseUrl() {
        // Precedence order (matches the docstring at the top of the
        // class): explicit `baseUrl` (-D / env / props) wins over the
        // JSON-derived value, which in turn wins over the built-in
        // fallback. `get()` walks the full chain, so
        // `-DbaseUrl=https://alt.example` overrides everything and
        // `-Dapi_config.api_end_point=https://alt.example` overrides
        // just the derivation input.
        //
        // NOTE: earlier versions read programConfig directly in
        // deriveBaseUrl(), which silently masked BOTH `-DbaseUrl` and
        // `-Dapi_config.*` overrides -- the exact opposite of what the
        // precedence contract promised.
        String explicit = get("baseUrl", null);
        if (isNonEmpty(explicit)) return explicit;
        String derived = deriveBaseUrl();
        if (isNonEmpty(derived)) return derived;
        return "https://jsonplaceholder.typicode.com";
    }

    private static String deriveBaseUrl() {
        // Each component is read via get() so a `-Dapi_config.*`
        // override is honored per the precedence chain. Reading
        // programConfig directly (as this used to) would silently
        // ignore -D flags and env vars aimed at the derivation
        // inputs.
        String ep = get("api_config.api_end_point", null);
        if (!isNonEmpty(ep)) return null;
        String ver = get("api_config.version", null);
        if (isNonEmpty(ver)) {
            return ep.replaceAll("/+$", "") + "/" + ver.replaceAll("^/+", "");
        }
        return ep.replaceAll("/+$", "");
    }

    public static int connectTimeoutMs() {
        return getInt("connectTimeoutMs", 10_000);
    }

    public static int socketTimeoutMs() {
        return getInt("socketTimeoutMs", 30_000);
    }

    // -------- Auth --------

    public static String authType() {
        return get("auth.type", "none");
    }

    public static String bearerToken() {
        return get("auth.bearer.token", "");
    }

    public static String basicUsername() {
        return get("auth.basic.username", "");
    }

    public static String basicPassword() {
        return get("auth.basic.password", "");
    }

    public static String oauth2TokenUrl() {
        return get("auth.oauth2.tokenUrl", "");
    }

    public static String oauth2ClientId() {
        return get("auth.oauth2.clientId", "");
    }

    public static String oauth2ClientSecret() {
        return get("auth.oauth2.clientSecret", "");
    }

    public static String oauth2Scope() {
        return get("auth.oauth2.scope", "");
    }

    // -------- Retry --------

    public static int retryMaxCount() {
        return getInt("retry.maxCount", 2);
    }

    // -------- Reporting --------

    public static boolean allureEnabled() {
        return getBool("report.allure.enabled", true);
    }

    public static boolean extentEnabled() {
        return getBool("report.extent.enabled", true);
    }

    // ---------------------------------------------------------------------
    // Preflight validation
    // ---------------------------------------------------------------------

    /**
     * Scan required credentials + endpoint config for placeholder or
     * obviously-invalid values that would cause tests to fail at the FIRST
     * HTTP call with a confusing error (UnknownHostException for
     * `api.example.com`, HTTP 401 for `__SET_ME__`). Returns a list of
     * human-readable failure reasons; empty list = green preflight.
     *
     * <p>Detects:</p>
     * <ul>
     *   <li>Placeholder values: {@code __SET_ME__}, {@code CHANGEME},
     *       {@code your-value-here}, {@code <fill-in>}, {@code TODO}</li>
     *   <li>Example-domain hosts: {@code example.com}, {@code example.org},
     *       {@code your-api-host.com}</li>
     *   <li>Missing required keys: empty {@code api_config.client_id},
     *       {@code api_config.client_secret}, {@code api_config.api_end_point}</li>
     * </ul>
     *
     * <p>Called by {@code BaseApiTest.@BeforeSuite} before RestAssured is
     * bootstrapped so a failure fires ONE clear message instead of N
     * per-test {@code UnknownHostException} stacktraces.
     */
    public static java.util.List<String> preflightIssues() {
        java.util.List<String> issues = new java.util.ArrayList<>();
        String[] requiredKeys = {
                "api_config.client_id",
                "api_config.client_secret",
                "api_config.api_end_point"
        };
        String[] placeholders = {
                "__SET_ME__", "CHANGEME", "changeme", "your-value-here",
                "<fill-in>", "TODO", "REPLACE_ME"
        };
        String[] fakeHosts = {
                "example.com", "example.org", "your-api-host.com",
                "api.example.com", "yourdomain.com"
        };
        for (String key : requiredKeys) {
            String v = get(key, "");
            if (v == null || v.isEmpty()) {
                issues.add("required config key `" + key + "` is missing or empty");
                continue;
            }
            for (String ph : placeholders) {
                if (v.contains(ph)) {
                    issues.add("config key `" + key + "` still has placeholder value `"
                            + v + "` -- edit program_configuration.json before running");
                    break;
                }
            }
            if (key.equals("api_config.api_end_point")) {
                String lower = v.toLowerCase();
                for (String fake : fakeHosts) {
                    if (lower.contains(fake)) {
                        issues.add("api_config.api_end_point `" + v
                                + "` points at a placeholder host -- edit "
                                + "program_configuration.json with a real endpoint");
                        break;
                    }
                }
            }
        }
        // Password / username preflight -- only if the token endpoint uses a
        // JSON-password grant (Hilton-style). Empty is fine for pure
        // client_credentials flows.
        String username = get("api_config.username", get("username", ""));
        String password = get("api_config.password", get("password", ""));
        for (String ph : placeholders) {
            if (username.contains(ph)) {
                issues.add("api_config.username still has placeholder value `"
                        + username + "`");
                break;
            }
        }
        for (String ph : placeholders) {
            if (password.contains(ph)) {
                issues.add("api_config.password still has placeholder value `***" +
                        (password.length() > 4 ? password.substring(password.length() - 4) : "")
                        + "`");
                break;
            }
        }
        return issues;
    }

    // ---------------------------------------------------------------------
    // Generic key access
    // ---------------------------------------------------------------------

    public static String get(String key, String fallback) {
        // 1. -Dkey
        String sys = System.getProperty(key);
        if (isNonEmpty(sys)) return sys;

        // 2. env var (dots -> underscores, uppercased)
        String envKey = key.replace('.', '_').toUpperCase();
        String env = System.getenv(envKey);
        if (isNonEmpty(env)) return env;

        // 3. program_configuration.json (env-scoped, flattened)
        String pc = programConfig.get(key);
        if (isNonEmpty(pc)) return pc;

        // 3b. legacy alias -> nested program_configuration key
        String aliasTarget = LEGACY_ALIASES.get(key);
        if (aliasTarget != null) {
            String aliased = programConfig.get(aliasTarget);
            if (isNonEmpty(aliased)) return aliased;
        }

        // 4. properties file (application-{env}.properties overlays
        //    application.properties; both loaded into `props` at init time)
        String prop = props.getProperty(key);
        if (isNonEmpty(prop)) return prop;

        return fallback;
    }

    public static int getInt(String key, int fallback) {
        String v = get(key, null);
        if (v == null) return fallback;
        try {
            return Integer.parseInt(v.trim());
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    public static boolean getBool(String key, boolean fallback) {
        String v = get(key, null);
        if (v == null) return fallback;
        return Boolean.parseBoolean(v.trim());
    }

    // ---------------------------------------------------------------------
    // Internal
    // ---------------------------------------------------------------------

    private static String detectEnv() {
        String d = System.getProperty("env");
        if (isNonEmpty(d)) return d;
        String e = System.getenv("TEST_ENV");
        if (isNonEmpty(e)) return e;
        String p = props.getProperty("env");
        if (isNonEmpty(p)) return p;
        return "qa";
    }

    private static void loadClasspathIntoProps(String resource) {
        try (InputStream in = Config.class.getClassLoader().getResourceAsStream(resource)) {
            if (in != null) {
                props.load(in);
            }
        } catch (IOException e) {
            throw new IllegalStateException("Failed to load classpath resource: " + resource, e);
        }
    }

    /**
     * Load the nested-per-env JSON at classpath:/program_configuration.json,
     * pick the block matching {@code envName}, and flatten its keys into
     * {@link #programConfig} (dot-joined). Also derives synthetic
     * {@code db.url}/{@code db.user}/{@code db.password}/{@code db.driver}
     * entries from the {@code database.*} sub-block so the Db utility
     * finds them without extra config.
     *
     * <p>Silently skipped when the file is absent -- other config sources
     * still apply. Malformed JSON logs to STDERR but does not throw so
     * a broken local file doesn't break every test suite.
     */
    private static void loadProgramConfiguration(String envName) {
        // Verbose diagnostics so a failed load isn't a mystery. Every branch
        // prints to STDOUT with a `[Config-JSON]` prefix so you can grep for
        // exactly what happened. Costs one classpath walk per JVM start;
        // negligible for a test run.
        String resource = "program_configuration.json";
        ClassLoader cl = Config.class.getClassLoader();
        System.out.println("[Config-JSON] classloader = " + cl);
        // Enumerate every URL the classloader knows for this resource. If
        // there are 0, the file isn't on the classpath at all. If there
        // are 2, we'll load the FIRST -- and print all of them so you can
        // see which wins vs which is stale.
        try {
            java.util.Enumeration<java.net.URL> urls = cl.getResources(resource);
            int i = 0;
            while (urls.hasMoreElements()) {
                System.out.println("[Config-JSON]   found #" + (++i)
                        + ": " + urls.nextElement());
            }
            if (i == 0) {
                System.out.println("[Config-JSON]   NO occurrences of `"
                        + resource + "` on the classpath.");
                System.out.println("[Config-JSON]   Expected at "
                        + "target/classes/program_configuration.json after "
                        + "`mvn clean` copies from src/main/resources/.");
                return;
            }
        } catch (IOException e) {
            System.err.println("[Config-JSON] classloader.getResources failed: "
                    + e.getMessage());
        }
        try (InputStream in = cl.getResourceAsStream(resource)) {
            if (in == null) {
                System.out.println("[Config-JSON] getResourceAsStream returned null "
                        + "(despite getResources finding entries -- classpath race?).");
                return;
            }
            ObjectMapper mapper = new ObjectMapper();
            JsonNode root = mapper.readTree(in);
            if (root == null || !root.isObject()) {
                System.err.println("[Config-JSON] parsed JSON is null or not an object");
                return;
            }
            // List every top-level env key so a case mismatch is instantly
            // obvious (e.g. `STG` in the JSON but envName=`stg`).
            java.util.List<String> topKeys = new java.util.ArrayList<>();
            root.fieldNames().forEachRemaining(topKeys::add);
            System.out.println("[Config-JSON] top-level keys in JSON: " + topKeys);
            System.out.println("[Config-JSON] looking for envName = `" + envName + "`");
            JsonNode envNode = root.get(envName);
            if (envNode == null || !envNode.isObject()) {
                System.err.printf(
                    "[Config-JSON] no block for env=`%s`. Try `-Denv=<one-of-%s>` "
                    + "or rename the top-level key in the JSON to `%s`.%n",
                    envName, topKeys, envName);
                return;
            }
            flatten("", envNode, programConfig);
            deriveDbKeys(envNode);
            System.out.println("[Config-JSON] loaded " + programConfig.size()
                    + " keys from `" + envName + "` block");
        } catch (IOException e) {
            System.err.printf(
                "[Config-JSON] failed to parse program_configuration.json: "
                + "%s: %s%n",
                e.getClass().getSimpleName(), e.getMessage());
        }
    }

    /**
     * Recursively flatten a JsonNode into {@code out} with dot-joined keys.
     * Arrays are joined with commas (rarely used at leaf level in the
     * reference framework's config shape; if a test really needs the
     * structured array, it can call `Config.get("some.key")` and split
     * on comma).
     */
    private static void flatten(String prefix, JsonNode node, Map<String, String> out) {
        if (node.isObject()) {
            Iterator<Map.Entry<String, JsonNode>> it = node.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> entry = it.next();
                String key = prefix.isEmpty()
                    ? entry.getKey()
                    : prefix + "." + entry.getKey();
                flatten(key, entry.getValue(), out);
            }
        } else if (node.isArray()) {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < node.size(); i++) {
                if (i > 0) sb.append(",");
                sb.append(node.get(i).asText());
            }
            out.put(prefix, sb.toString());
        } else if (!node.isNull()) {
            out.put(prefix, node.asText());
        }
    }

    /**
     * Populate {@code db.url}, {@code db.user}, {@code db.password},
     * {@code db.driver} in {@link #programConfig} from the env's
     * `database` sub-block. Existing values (e.g. from a user override
     * in application.properties or a `-Ddb.url=...` flag) are NOT
     * overwritten -- the get() precedence order handles that.
     */
    private static void deriveDbKeys(JsonNode envNode) {
        JsonNode db = envNode.get("database");
        if (db == null || !db.isObject()) return;
        String host   = db.path("host").asText("");
        String port   = db.path("port").asText("5432");
        String schema = db.path("schema").asText("");
        String user   = db.path("username").asText("");
        String pass   = db.path("password").asText("");
        String scheme = db.path("scheme").asText("postgresql");  // override for MySQL/Oracle
        String driver = db.path("driver").asText("org.postgresql.Driver");
        if (isNonEmpty(host) && isNonEmpty(schema)) {
            programConfig.put("db.url",
                String.format("jdbc:%s://%s:%s/%s", scheme, host, port, schema));
        }
        if (isNonEmpty(user))   programConfig.put("db.user", user);
        if (isNonEmpty(pass))   programConfig.put("db.password", pass);
        if (isNonEmpty(driver)) programConfig.put("db.driver", driver);
    }

    private static boolean isNonEmpty(String s) {
        return s != null && !s.isBlank();
    }
}
