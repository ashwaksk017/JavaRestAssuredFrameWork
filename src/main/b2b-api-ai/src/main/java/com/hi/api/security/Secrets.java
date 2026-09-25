package com.hi.api.security;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import com.hi.api.config.Config;

/**
 * Central credential redactor. The ONLY place a real credential value is
 * allowed to exist is {@code program_configuration.json}; every rendering
 * of an HTTP exchange -- Extent HTML, per-test logs, console output,
 * Allure attachments -- goes through {@link #redact(String)} first.
 *
 * <p>Two independent layers, because neither alone is sufficient:</p>
 *
 * <ol>
 *   <li><b>Value-based.</b> Harvests the actual secret values from
 *       {@link Config} once, then replaces any literal occurrence
 *       anywhere in the text. This is what guarantees the value is never
 *       visible -- it does not care whether the value shows up in a JSON
 *       body, a query string, a SQL statement, a stack trace, or a URL.</li>
 *   <li><b>Key-based.</b> Masks the <em>value beside</em> a known
 *       credential key ({@code client_secret}, {@code password},
 *       {@code assertion}, {@code access_token}, ...) in JSON and
 *       form-encoded text, plus {@code Bearer} / {@code Basic} header
 *       values. This catches secrets that are NOT in config and so cannot
 *       be harvested -- above all the bearer token the server mints at
 *       runtime.</li>
 * </ol>
 *
 * <p>Disable for a local debugging session with
 * {@code -Dsecurity.redaction.enabled=false}. It defaults to ON, so a
 * default run can never leak.</p>
 *
 * <p>Thread-safe: the harvested value set is built once under a lock and
 * then only read.</p>
 */
public final class Secrets {

    /** What a redacted value renders as. */
    public static final String MASK = "***REDACTED***";

    /**
     * Values shorter than this are never value-redacted. A 1-3 char
     * "secret" (usually an unset or stub value) would otherwise match
     * everywhere and shred every log line in the run.
     */
    private static final int MIN_SECRET_LEN = 6;

    /**
     * Config keys whose values are credentials. Harvested at first use.
     * Missing / empty / placeholder keys are skipped, so this list is
     * safe to over-populate -- add a key the moment one is introduced.
     */
    private static final String[] SECRET_KEYS = {
            // Hilton API (client-credentials)
            "api_config.client_secret", "api_config.client_id",
            "api_config.password", "api_config.access_token",
            // Kafka reuses the same credential pair
            "kafka_config.client_secret", "kafka_config.client_id",
            "kafka_config.password", "kafka_config.access_token",
            // Salesforce JWT-bearer
            "sf_config.assertion", "sf_config.ui_password",
            "sf_config.access_token",
            // Database
            "database.password", "db.password",
            // Jira / Xray
            "xray_api_config.token", "xray.clientSecret", "xray.clientId",
            // Framework-level auth knobs
            "auth.bearer.token", "auth.basic.password",
            "auth.oauth2.clientSecret", "auth.oauth2.clientId",
            // Misc integrations
            "gitlab.token", "demoshop.password",
    };

    /**
     * Placeholder / structural values that are NOT secrets. Redacting
     * these would hide the very banner that tells the user to fill the
     * config in.
     */
    private static final Set<String> NON_SECRETS = Set.of(
            "__SET_ME__", "CHANGEME", "changeme", "your-value-here",
            "<fill-in>", "TODO", "REPLACE_ME", "access_token",
            "client_credentials", "password", "true", "false", "null");

    /** JSON {@code "key": "value"} where the key is credential-ish. */
    private static final Pattern JSON_SECRET = Pattern.compile(
            "(?i)(\"(?:client_secret|clientSecret|client_id|clientId|password|pwd|passwd"
            + "|assertion|access_token|accessToken|refresh_token|refreshToken"
            + "|id_token|idToken|secret|api_key|apiKey|apikey|authorization"
            + "|session_id|sessionId)\"\\s*:\\s*\")([^\"]*)(\")");

    /** Form-encoded / query-string {@code key=value}. */
    private static final Pattern FORM_SECRET = Pattern.compile(
            "(?i)\\b(client_secret|client_id|password|pwd|assertion|access_token"
            + "|refresh_token|id_token|secret|api_key|apikey)=([^&\\s\"'<>]+)");

    /** {@code Bearer <jwt>} anywhere in text. */
    private static final Pattern BEARER = Pattern.compile(
            "(?i)(bearer\\s+)([A-Za-z0-9._~+/=-]{16,})");

    /** {@code Basic <base64>} anywhere in text. */
    private static final Pattern BASIC = Pattern.compile(
            "(?i)(basic\\s+)([A-Za-z0-9+/=]{8,})");

    private static volatile List<String> harvested;
    private static final Object LOCK = new Object();

    private Secrets() {}

    public static boolean enabled() {
        return !"false".equalsIgnoreCase(
                Config.get("security.redaction.enabled", "true"));
    }

    /**
     * Redact every credential from {@code text}. Null-safe and
     * idempotent -- running it twice yields the same string, so it is
     * safe to apply at more than one layer (and we deliberately do).
     */
    public static String redact(String text) {
        if (text == null || text.isEmpty() || !enabled()) {
            return text;
        }
        String out = text;
        // Layer 1 -- literal values from config. Longest first so a
        // secret containing another secret as a substring is fully
        // masked rather than partially.
        for (String secret : harvestedValues()) {
            if (out.contains(secret)) {
                out = out.replace(secret, MASK);
            }
        }
        // Layer 2 -- key-based, for runtime-issued secrets.
        out = replaceGroup(JSON_SECRET, out, 2);
        out = replaceGroup(FORM_SECRET, out, 2);
        out = replaceGroup(BEARER, out, 2);
        out = replaceGroup(BASIC, out, 2);
        return out;
    }

    /** Redact a header value by name -- masks the whole value for auth headers. */
    public static String redactHeader(String name, String value) {
        if (name == null || value == null || !enabled()) {
            return value;
        }
        String n = name.toLowerCase(Locale.ROOT);
        if (n.equals("authorization") || n.equals("proxy-authorization")
                || n.equals("cookie") || n.equals("set-cookie")
                || n.contains("api-key") || n.contains("apikey")
                || n.contains("secret") || n.contains("token")) {
            return MASK;
        }
        return redact(value);
    }

    /**
     * Config values that are real secrets, longest-first. Built once.
     * Never logs what it found -- that would defeat the purpose.
     */
    static List<String> harvestedValues() {
        List<String> local = harvested;
        if (local != null) {
            return local;
        }
        synchronized (LOCK) {
            if (harvested != null) {
                return harvested;
            }
            Set<String> values = new LinkedHashSet<>();
            for (String key : SECRET_KEYS) {
                String v;
                try {
                    v = Config.get(key, "");
                } catch (RuntimeException e) {
                    // Config not loadable -- the key-based layer still applies.
                    continue;
                }
                if (isRedactable(v)) {
                    values.add(v);
                }
            }
            List<String> sorted = new ArrayList<>(values);
            sorted.sort((a, b) -> Integer.compare(b.length(), a.length()));
            harvested = sorted;
            return harvested;
        }
    }

    /**
     * A value worth masking: long enough, and not a known placeholder.
     * Public so the redaction contract can be asserted from the test tree.
     */
    public static boolean isRedactable(String v) {
        if (v == null || v.length() < MIN_SECRET_LEN) {
            return false;
        }
        if (NON_SECRETS.contains(v)) {
            return false;
        }
        for (String ph : NON_SECRETS) {
            if (v.contains(ph)) {
                return false;
            }
        }
        return true;
    }

    /** Replace one capture group with {@link #MASK}, leaving the rest intact. */
    private static String replaceGroup(Pattern p, String text, int group) {
        Matcher m = p.matcher(text);
        StringBuilder sb = null;
        int last = 0;
        while (m.find()) {
            String captured = m.group(group);
            // An already-masked or empty value needs no second pass --
            // this is what keeps redact() idempotent.
            if (captured == null || captured.isEmpty() || MASK.equals(captured)) {
                continue;
            }
            if (sb == null) {
                sb = new StringBuilder(text.length() + 32);
            }
            sb.append(text, last, m.start(group)).append(MASK);
            last = m.end(group);
        }
        if (sb == null) {
            return text;
        }
        sb.append(text, last, text.length());
        return sb.toString();
    }

    /** Test hook -- forces a re-harvest after Config changes. */
    public static void resetForTest() {
        synchronized (LOCK) {
            harvested = null;
        }
    }

    /** Diagnostic: how many config-sourced secrets are masked. Never the values. */
    public static int harvestedCount() {
        return harvestedValues().size();
    }

    static List<String> secretKeys() {
        return Arrays.asList(SECRET_KEYS);
    }
}
