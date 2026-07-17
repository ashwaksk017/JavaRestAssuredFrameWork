// =============================================================================
// Config
// -----------------------------------------------------------------------------
// Single source of truth for environment-specific values. Hierarchy (highest
// priority first):
//   1. System properties (-Dkey=value on mvn command line)
//   2. Environment variables (case-insensitive, dots converted to underscores)
//   3. application-{env}.properties on the classpath
//   4. application.properties on the classpath
//   5. Built-in defaults (fallback in accessors)
//
// The active env is chosen by (highest priority first): -Denv, TEST_ENV env
// variable, "env" property in application.properties, else "qa".
// =============================================================================

package com.ak.api.config;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

public final class Config {

    private static final Properties props = new Properties();

    static {
        // Base: application.properties
        loadClasspathIntoProps("application.properties");

        // Env overlay: application-{env}.properties
        String envName = detectEnv();
        loadClasspathIntoProps("application-" + envName + ".properties");
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
        return get("baseUrl", "https://jsonplaceholder.typicode.com");
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

        // 3. properties file
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

    private static boolean isNonEmpty(String s) {
        return s != null && !s.isBlank();
    }
}
