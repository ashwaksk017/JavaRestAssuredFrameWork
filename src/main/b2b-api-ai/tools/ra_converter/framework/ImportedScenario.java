package com.ak.api.support;

// ra_converter-framework-rev: 19
// Bumped whenever this bundled file changes. The converter
// SKIPS author-editable files that already exist, so without a
// revision it cannot tell an author's edit from a copy left by
// an older converter -- and an in-method change (no new symbol)
// would silently never reach existing trees.

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.net.URISyntaxException;
import java.net.URL;
import java.util.Arrays;
import java.util.Collections;
import java.util.Enumeration;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.asserts.SoftAssert;

import com.ak.api.auth.TokenCache;
import com.ak.api.config.Config;
import com.ak.api.data.FakeData;
import com.ak.api.data.PlaceholderResolver;
import com.ak.api.rest.utilities.RestLoggerUtilityDataHolder;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Framework bindings + ctx helpers for every imported ReadyAPI suite.
 *
 * <p>Generated {@code CustomerOnboarding.start(row)} (and per-case Support)
 * reach the test's client, ctx, softAssert, and holder without putting
 * those parameters on every {@code @Test}. Shared fluent steps in
 * {@code com.ak.api.support.scenario} call these methods so they are not
 * compiled against one suite's {@code TestSupport}.</p>
 *
 * <p>RestStep and CtxFields call helpers here rather than a suite
 * {@code TestSupport}, so {@code mvn compile} succeeds even when only
 * one XML (or none) has been converted.</p>
 *
 * <p>RestStep still uses {@code Map ctx} internally. Fluent helpers copy
 * record-relevant extracts onto ctx at the helper boundary, then read
 * them back in {@code complete()}.</p>
 */
public final class ImportedScenario {

    private static final Logger LOG = LoggerFactory.getLogger(ImportedScenario.class);
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final ConcurrentHashMap<String, Map<String, String>> JSON_CACHE =
            new ConcurrentHashMap<>();
    private static volatile Map<String, String> ALL_TEST_DATA_DEFAULTS;

    private static final java.util.regex.Pattern HASH_REF =
            java.util.regex.Pattern.compile("#([A-Za-z0-9_.-]+)#");
    private static final java.util.regex.Pattern AT_REF =
            java.util.regex.Pattern.compile("@([A-Za-z0-9_.-]+)@");

    private ImportedScenario() {}

    public static final class Session {
        public Object client;
        public Map<String, String> ctx;
        public Map<String, String> row;
        public SoftAssert softAssert;
        public RestLoggerUtilityDataHolder holder;
        public String testCaseId;
        /** Last fluent builder on this thread (so verify helpers share Response fields). */
        public Object flow;
        /** Converter suite id (XML basename, e.g. {@code programaccountregression}). */
        public String suiteName;
    }

    private static final ThreadLocal<Session> CURRENT = new ThreadLocal<>();

    public static void bind(Object client,
                            Map<String, String> ctx,
                            SoftAssert softAssert,
                            RestLoggerUtilityDataHolder holder) {
        bind(client, ctx, softAssert, holder, null);
    }

    public static void bind(Object client,
                            Map<String, String> ctx,
                            SoftAssert softAssert,
                            RestLoggerUtilityDataHolder holder,
                            String suiteName) {
        Session s = new Session();
        s.client = client;
        s.softAssert = threadOr(softAssert, "softAssert");
        s.holder = threadOrHolder(holder);
        s.suiteName = suiteName;
        s.ctx = isolateCtx(ctx);
        CURRENT.set(s);
    }

    /**
     * Prefer the map bound on this thread so {@code parallel="methods"}
     * cannot share extracted ids across sibling {@code @Test}s.
     */
    public static Map<String, String> boundCtxOr(Map<String, String> fallback) {
        Session s = CURRENT.get();
        if (s != null && s.ctx != null) {
            return s.ctx;
        }
        return fallback;
    }

    private static SoftAssert threadOr(SoftAssert fallback, String unused) {
        SoftAssert tl = TestThreadState.softAssert();
        return tl != null ? tl : fallback;
    }

    private static RestLoggerUtilityDataHolder threadOrHolder(
            RestLoggerUtilityDataHolder fallback) {
        RestLoggerUtilityDataHolder tl = TestThreadState.holder();
        return tl != null ? tl : fallback;
    }

    private static Map<String, String> isolateCtx(Map<String, String> classCtx) {
        if (!Config.getBool("test.isolateCtxPerMethod", true)) {
            return classCtx;
        }
        Map<String, String> isolated =
                java.util.Collections.synchronizedMap(new java.util.LinkedHashMap<>());
        if (classCtx != null) {
            String auth = classCtx.get("accessToken");
            if (auth != null && !auth.isEmpty()) {
                isolated.put("accessToken", auth);
            }
        }
        return isolated;
    }

    public static void unbind() {
        CURRENT.remove();
    }

    /**
     * Per-row ctx reset + CSV placeholder expand. Lives in {@code main}
     * so generated {@code CustomerOnboarding.start} can call it.
     */
    public static Map<String, String> begin(Map<String, String> ctx,
                                            Map<String, String> row,
                                            String testCaseId) {
        if (ctx != null) {
            String auth = ctx.get("accessToken");
            ctx.clear();
            if (auth != null) {
                ctx.put("accessToken", auth);
            }
        }
        Map<String, String> resolved = PlaceholderResolver.resolveRow(row, ctx);
        attachAllure(resolved, testCaseId);
        return resolved;
    }

    private static void attachAllure(Map<String, String> row, String testCaseId) {
        if (testCaseId == null && (row == null || row.isEmpty())) {
            return;
        }
        try {
            io.qameta.allure.Allure.getLifecycle().updateTestCase(tc -> {
                if (testCaseId != null && !testCaseId.isEmpty()) {
                    tc.setName(testCaseId);
                }
                if (row == null) {
                    return;
                }
                for (Map.Entry<String, String> e : row.entrySet()) {
                    String v = e.getValue();
                    if (v == null || v.isEmpty()) {
                        continue;
                    }
                    String d = v.length() > 120 ? v.substring(0, 120) + "..." : v;
                    tc.getParameters().add(
                            new io.qameta.allure.model.Parameter()
                                    .setName(e.getKey())
                                    .setValue(d));
                }
            });
        } catch (RuntimeException ignored) {
            // Allure is a no-op outside a running test.
        }
    }

    public static Session current() {
        Session s = CURRENT.get();
        if (s == null) {
            throw new IllegalStateException(
                    "ImportedScenario.bind() was not called from @BeforeMethod");
        }
        return s;
    }

    public static Long asLong(Map<String, String> ctx, String... keys) {
        String raw = first(ctx, keys);
        if (raw == null) {
            return null;
        }
        String digits = raw.replaceAll("[^0-9-]", "");
        if (digits.isEmpty() || "-".equals(digits)) {
            return null;
        }
        try {
            return Long.valueOf(digits);
        } catch (NumberFormatException ex) {
            return null;
        }
    }

    public static String asString(Map<String, String> ctx, String... keys) {
        return first(ctx, keys);
    }

    private static String first(Map<String, String> ctx, String... keys) {
        if (ctx == null || keys == null) {
            return null;
        }
        for (String key : keys) {
            if (key == null) {
                continue;
            }
            String v = ctx.get(key);
            if (v != null && !v.isEmpty()) {
                return v;
            }
        }
        return null;
    }

    public static String ctxGet(Map<String, String> ctx, String primaryKey) {
        return expandPlaceholders(ctx, ctxGetRaw(ctx, primaryKey));
    }

    static String ctxGetRaw(Map<String, String> ctx, String primaryKey) {
        if (ctx == null || primaryKey == null) {
            return "";
        }
        if (ctx.containsKey(primaryKey)) {
            String direct = ctx.get(primaryKey);
            if (direct != null && !direct.isEmpty()) {
                return direct;
            }
            if (isHiltonTokenKey(primaryKey)) {
                String cached = hiltonTokenFallback(primaryKey, ctx);
                if (!cached.isEmpty()) {
                    return cached;
                }
            }
            return direct == null ? "" : direct;
        }
        // ReadyAPI resolves ${Step#prop} case-insensitively: 16 cases read
        // ${PropertiesGuestId#guestID} from a step that only ever holds
        // guestId, and its recorded request still carries the real guest id.
        // Without this, the lookup below fell to DataGenInput's random
        // Properties.guestID. Only an unambiguous match is used.
        String sameKeyOtherCase = caseInsensitiveExact(ctx, primaryKey);
        if (sameKeyOtherCase != null) {
            return sameKeyOtherCase;
        }
        // Declared aliases before the heuristic.
        //
        // The exact key is absent, so SOME cross-key resolution is going to
        // happen either way -- lookupByTrailingField already walks the map for
        // a matching trailing field, ranked by suffix. The order it picks
        // depends on map iteration, which is why an owner value could answer
        // where a member value was meant.
        //
        // ScenarioContext.resolveDeclared consults an explicit, reviewable,
        // frequency-ordered alias list for the ~11 concepts that account for
        // 85% of lookups. Same class of resolution, deterministic order.
        //
        // Deliberately NOT applied when the key is present-but-empty (the
        // branch above returns early): a blank id is often the point of a
        // negative test, and filling it from a sibling would turn an expected
        // 400 into a 200 -- silently weakening the assertion.
        String declared = com.ak.api.context.ScenarioContext.resolveDeclared(
                ctx, primaryKey);
        if (declared != null && !declared.isEmpty()) {
            return declared;
        }
        int lastDot = primaryKey.lastIndexOf('.');
        String field = (lastDot >= 0) ? primaryKey.substring(lastDot + 1) : primaryKey;
        String fieldAlt = flipTrailingCase(field);
        String byField = lookupByTrailingField(ctx, primaryKey, field, fieldAlt);
        if (byField != null && !byField.isEmpty()) {
            return byField;
        }
        if (isHiltonTokenKey(primaryKey)) {
            return hiltonTokenFallback(primaryKey, ctx);
        }
        return "";
    }

    /**
     * The value under a key that equals {@code primaryKey} ignoring case, or
     * null when there is none -- or more than one with different values, in
     * which case the caller's existing resolution is kept.
     */
    static String caseInsensitiveExact(Map<String, String> ctx, String primaryKey) {
        String found = null;
        for (Map.Entry<String, String> e : ctx.entrySet()) {
            String k = e.getKey();
            String v = e.getValue();
            if (k == null || v == null || v.isEmpty() || k.equals(primaryKey)
                    || !k.equalsIgnoreCase(primaryKey)) {
                continue;
            }
            if (found == null) {
                found = v;
            } else if (!found.equals(v)) {
                LOG.warn(" .. [ctxGet] {} matches several keys that differ only in case,"
                        + " with different values -- not guessing", primaryKey);
                return null;
            }
        }
        return found;
    }

    /**
     * Prefer {@code Properties.<field>} before walking other dotted keys so
     * HashMap order cannot swap owner {@code Email} with another step's
     * {@code Email}. {@code Email} vs {@code EmailMember} stay distinct.
     */
    static String lookupByTrailingField(Map<String, String> ctx, String primaryKey,
            String field, String fieldAlt) {
        String exact = ctx.get("Properties." + field);
        if (exact != null && !exact.isEmpty()) {
            return exact;
        }
        if (fieldAlt != null) {
            exact = ctx.get("Properties." + fieldAlt);
            if (exact != null && !exact.isEmpty()) {
                return exact;
            }
        }
        String best = null;
        int bestRank = Integer.MAX_VALUE;
        String bestKey = null;
        for (Map.Entry<String, String> e : ctx.entrySet()) {
            String k = e.getKey();
            String val = e.getValue();
            if (k == null || val == null || val.isEmpty() || k.equals(primaryKey)) {
                continue;
            }
            int kDot = k.lastIndexOf('.');
            String kField = (kDot >= 0) ? k.substring(kDot + 1) : k;
            if (!(kField.equals(field) || (fieldAlt != null && kField.equals(fieldAlt)))) {
                continue;
            }
            int rank = PlaceholderResolver.suffixRank(k);
            if (rank < bestRank
                    || (rank == bestRank && (bestKey == null || k.compareTo(bestKey) < 0))) {
                bestRank = rank;
                bestKey = k;
                best = val;
            }
        }
        if (best != null) {
            return best;
        }
        String bare = ctx.get(field);
        return (bare != null && !bare.isEmpty()) ? bare : "";
    }

    static boolean isHiltonTokenKey(String primaryKey) {
        if (primaryKey == null || primaryKey.isEmpty()) {
            return false;
        }
        String n = primaryKey.toLowerCase(Locale.ROOT);
        return n.endsWith("generatedtokenid")
                || n.equals("accesstoken")
                || n.endsWith(".accesstoken");
    }

    /**
     * Publish a ctx value chosen by the ACTIVE ENVIRONMENT.
     *
     * <p>ReadyAPI scripts select literals per environment:</p>
     * <pre>
     *   def env = context.testCase.testSuite.project
     *                    .getActiveEnvironment().getName()
     *   if (env == "EKS_TST")      p.setPropertyValue("topicenv", "programaccounts-test")
     *   else if (env == "EKS_STG") p.setPropertyValue("topicenv", "programaccounts-stg")
     * </pre>
     *
     * <p>The converter cannot know at emit time which branch runs, so it
     * emits every branch as a ReadyAPI-label to literal map and resolves
     * here. Resolution order:</p>
     * <ol>
     *   <li>Explicit pin -- a "readyapi_env.&lt;activeEnv&gt;" key in
     *       program_configuration.json naming the ReadyAPI label.</li>
     *   <li>Normalised containment -- active "stg" matches ReadyAPI
     *       "EKS_STG". Needs 2+ alphanumeric chars on both sides so a
     *       one-letter env name cannot match everything.</li>
     *   <li>No match -- WARN naming the ctx key, the active env and the
     *       candidates, then use the first branch. Loud, never silent:
     *       before this existed these keys got a RANDOM generated value.</li>
     * </ol>
     */
    public static void putEnvScoped(Map<String, String> ctx, String key,
                                    java.util.LinkedHashMap<String, String> byReadyApiEnv) {
        if (ctx == null || key == null || byReadyApiEnv == null
                || byReadyApiEnv.isEmpty()) return;
        String active = Config.env();
        String chosen = null;
        String chosenLabel = null;

        // 1. Explicit pin wins.
        String pinned = Config.get("readyapi_env." + active, "");
        if (pinned != null && !pinned.trim().isEmpty()) {
            for (Map.Entry<String, String> e : byReadyApiEnv.entrySet()) {
                if (e.getKey().equalsIgnoreCase(pinned.trim())) {
                    chosen = e.getValue();
                    chosenLabel = e.getKey();
                    break;
                }
            }
        }

        // 2. Normalised containment.
        if (chosen == null) {
            String a = normalizeEnvName(active);
            if (a.length() >= 2) {
                for (Map.Entry<String, String> e : byReadyApiEnv.entrySet()) {
                    String l = normalizeEnvName(e.getKey());
                    if (l.length() >= 2 && (l.contains(a) || a.contains(l))) {
                        chosen = e.getValue();
                        chosenLabel = e.getKey();
                        break;
                    }
                }
            }
        }

        // 3. Undecidable -- say so rather than guess quietly.
        if (chosen == null) {
            Map.Entry<String, String> first =
                    byReadyApiEnv.entrySet().iterator().next();
            chosen = first.getValue();
            chosenLabel = first.getKey();
            LOG.warn(" .. [putEnvScoped] active env '{}' matches none of {}"
                    + " for ctx key {} -- falling back to ReadyAPI env '{}'."
                    + " Pin it by adding readyapi_env.{} to"
                    + " program_configuration.json.",
                    active, byReadyApiEnv.keySet(), key, chosenLabel, active);
        }

        LOG.debug(" .. [putEnvScoped] {} <- {}  (active env {} -> ReadyAPI {})",
                key, chosen, active, chosenLabel);
        ctx.put(key, chosen);
    }

    /** Lowercase alphanumerics only, for tolerant env-name comparison. */
    private static String normalizeEnvName(String s) {
        if (s == null) return "";
        StringBuilder sb = new StringBuilder();
        for (char c : s.toCharArray()) {
            if (Character.isLetterOrDigit(c)) sb.append(Character.toLowerCase(c));
        }
        return sb.toString();
    }

    /**
     * Ordered ReadyAPI-env label to literal map, built from alternating
     * key/value args. Order matters: putEnvScoped falls back to the FIRST
     * entry when no env matches, and that must be the branch the ReadyAPI
     * script declared first -- a HashMap would make it arbitrary.
     */
    public static java.util.LinkedHashMap<String, String> envMap(String... kv) {
        java.util.LinkedHashMap<String, String> m = new java.util.LinkedHashMap<>();
        if (kv == null) return m;
        for (int i = 0; i + 1 < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        return m;
    }

    /**
     * Client-credentials token from ctx.accessToken or {@link TokenCache}
     * when {@code tokenId.GeneratedTokenID} was never written (tokenRequest
     * cache hit + extract miss). Negative auth tests pass a literal empty
     * string into the client, not ctxGet, so they stay unauthorized.
     */
    static String hiltonTokenFallback(String primaryKey, Map<String, String> ctx) {
        String access = null;
        if (ctx != null) {
            access = ctx.get("accessToken");
        }
        if (access == null || access.isEmpty()) {
            access = TokenCache.getAccessToken();
        }
        if (access == null || access.isEmpty()) {
            return "";
        }
        boolean wantBearer = primaryKey != null
                && primaryKey.toLowerCase(Locale.ROOT).contains("generatedtoken");
        boolean hasBearer = access.regionMatches(true, 0, "Bearer ", 0, 7);
        if (wantBearer) {
            return hasBearer ? access : "Bearer " + access;
        }
        return hasBearer ? access.substring("Bearer ".length()) : access;
    }

    private static String expandPlaceholders(Map<String, String> ctx, String value) {
        if (value == null || value.isEmpty()) {
            return value == null ? "" : value;
        }
        if (value.indexOf('#') < 0 && value.indexOf('@') < 0) {
            return value;
        }
        String cur = value;
        for (int i = 0; i < 5; i++) {
            String next = expandOnce(cur, HASH_REF, ctx);
            next = expandOnce(next, AT_REF, ctx);
            if (next.equals(cur)) {
                return next;
            }
            cur = next;
        }
        return cur;
    }

    private static String expandOnce(String text, java.util.regex.Pattern pattern,
                                     Map<String, String> ctx) {
        java.util.regex.Matcher m = pattern.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {
            String key = m.group(1);
            String v = ctxGetRaw(ctx, key);
            if (v == null || v.isEmpty()) {
                m.appendReplacement(out, java.util.regex.Matcher.quoteReplacement(m.group()));
            } else {
                m.appendReplacement(out, java.util.regex.Matcher.quoteReplacement(v));
            }
        }
        m.appendTail(out);
        return out.toString();
    }

    private static String flipTrailingCase(String field) {
        if (field == null || field.length() < 2) {
            return null;
        }
        int n = field.length();
        char last = field.charAt(n - 1);
        char alt;
        if (last == 'D') {
            alt = 'd';
        } else if (last == 'd') {
            alt = 'D';
        } else {
            return null;
        }
        return field.substring(0, n - 1) + alt;
    }

    public static void putIfNonEmpty(Map<String, String> ctx, String key, String value) {
        if (ctx == null || key == null) {
            return;
        }
        if (value == null || value.isEmpty()) {
            return;
        }
        ctx.putIfAbsent(key, value);
    }

    public static void putExtracted(Map<String, String> ctx, String key, String value) {
        if (ctx == null || key == null) {
            return;
        }
        if (value == null || value.isEmpty()) {
            LOG.debug(" .. [putExtracted SKIPPED] key={} value=<empty> "
                    + "(ctx keeps its prior value, if any -- typically a "
                    + "stale generator default from DataGenInput regen)", key);
            return;
        }
        LOG.debug(" .. [putExtracted] {} <- {}", key,
                value.length() > 60 ? value.substring(0, 60) + "..." : value);
        ctx.put(key, value);
        String flipped = flipTrailingCase(key);
        if (flipped != null && !flipped.equals(key)) {
            ctx.put(flipped, value);
        }
    }

    public static Map<String, String> mergedRow(Map<String, String> row,
                                                Map<String, String> ctx) {
        Map<String, String> merged = new HashMap<>();
        for (String k : suiteConfigKeys()) {
            String v = Config.get(k, null);
            if (v != null) {
                putWithAliases(merged, k, v);
            }
        }
        if (row != null) {
            for (Map.Entry<String, String> e : row.entrySet()) {
                putWithAliases(merged, e.getKey(), e.getValue());
            }
        }
        if (ctx != null) {
            for (Map.Entry<String, String> e : ctx.entrySet()) {
                putWithAliases(merged, e.getKey(), e.getValue());
            }
        }
        return merged;
    }

    private static void putWithAliases(Map<String, String> merged, String key, String value) {
        if (key == null) {
            return;
        }
        merged.put(key, value);
        String underscoreForm = key.replace('.', '_').replace('-', '_');
        if (!underscoreForm.equals(key)) {
            merged.put(underscoreForm, value);
        }
        String dotForm = key.replace('-', '_');
        if (!dotForm.equals(key) && !dotForm.equals(underscoreForm)) {
            merged.put(dotForm, value);
        }
        int lastDot = key.lastIndexOf('.');
        String field = (lastDot >= 0) ? key.substring(lastDot + 1) : key;
        String snake = camelToSnakeLower(field);
        if (snake != null && !snake.equals(field)
                && !snake.equals(key) && !snake.equals(underscoreForm)) {
            merged.put(snake, value);
        }
    }

    private static String camelToSnakeLower(String s) {
        if (s == null || s.isEmpty()) {
            return null;
        }
        String withUnderscores = s
                .replaceAll("([a-z0-9])([A-Z])", "$1_$2")
                .replaceAll("([A-Z]+)([A-Z][a-z])", "$1_$2");
        return withUnderscores.toLowerCase();
    }

    private static String[] suiteConfigKeys() {
        String suite;
        try {
            suite = current().suiteName;
        } catch (IllegalStateException e) {
            return new String[0];
        }
        if (suite == null || suite.isEmpty()) {
            return new String[0];
        }
        try {
            java.lang.reflect.Field f = Class.forName(
                    "com.ak.api.support." + suite + ".TestSupport")
                    .getDeclaredField("CONFIG_KEYS");
            f.setAccessible(true);
            Object v = f.get(null);
            return v instanceof String[] ? (String[]) v : new String[0];
        } catch (ReflectiveOperationException e) {
            return new String[0];
        }
    }

    /**
     * Dispatch {@code SetupHelper.flow_X} for the bound suite so shared
     * fluent steps are not compiled against one suite's helper class.
     */
    public static void runSetup(String flowId, Object client, Map<String, String> ctx,
                                Map<String, String> row,
                                org.testng.asserts.SoftAssert softAssert,
                                RestLoggerUtilityDataHolder holder,
                                String testCaseId) {
        String suite = requireSuite();
        try {
            Class<?> helper = Class.forName("com.ak.api.support." + suite + ".SetupHelper");
            for (java.lang.reflect.Method m : helper.getMethods()) {
                if (flowId.equals(m.getName()) && m.getParameterCount() == 6) {
                    m.invoke(null, com.ak.api.domain.DomainApis.unwrapRaw(client),
                            ctx, row, softAssert, holder, testCaseId);
                    return;
                }
            }
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(
                    "SetupHelper." + flowId + " for suite " + suite, e);
        }
        throw new IllegalStateException(
                "No SetupHelper." + flowId + " on suite " + suite);
    }

    private static String requireSuite() {
        String suite = current().suiteName;
        if (suite == null || suite.isEmpty()) {
            throw new IllegalStateException(
                    "ImportedScenario.bind(..., suiteName) was not called");
        }
        return suite;
    }

    private static String optionalSuiteName() {
        Session s = CURRENT.get();
        if (s == null) {
            return null;
        }
        String n = s.suiteName;
        return (n == null || n.isEmpty()) ? null : n;
    }

    /**
     * Dump Properties/tokenId ctx entries. Used by RestStep around identity
     * regen so a stale value's origin is visible in the log.
     */
    public static void traceCtx(Map<String, String> ctx, String tag) {
        if (ctx == null || ctx.isEmpty()) {
            LOG.info(" .. [ctx@{}] <empty>", tag);
            return;
        }
        TreeMap<String, String> sorted = new TreeMap<>();
        for (Map.Entry<String, String> e : ctx.entrySet()) {
            String k = e.getKey();
            if (k == null) {
                continue;
            }
            if (k.startsWith("Properties") || k.startsWith("tokenId")
                    || k.startsWith("PropertiesDetails")
                    || k.startsWith("PropertiesGuestId")) {
                sorted.put(k, e.getValue());
            }
        }
        if (sorted.isEmpty()) {
            LOG.info(" .. [ctx@{}] <no Properties/tokenId keys>", tag);
            return;
        }
        // Full dump only at DEBUG. This fires before AND after regen on
        // every REST step, and ctx carries 450+ keys by mid-suite, so at
        // INFO it emitted roughly 950 lines per step -- enough to truncate
        // a run log and bury the request/response lines that matter.
        if (!LOG.isDebugEnabled()) {
            LOG.info(" .. [ctx@{}] {} keys (enable DEBUG on com.ak.api"
                    + " for the full dump)", tag, sorted.size());
            return;
        }
        StringBuilder sb = new StringBuilder(sorted.size() * 40);
        for (Map.Entry<String, String> e : sorted.entrySet()) {
            sb.append("\n     ").append(e.getKey()).append(" = ")
                    .append(e.getValue());
        }
        LOG.debug(" .. [ctx@{}] ({} keys):{}", tag, sorted.size(), sb);
    }

    /**
     * Set after {@link #regenRandomProperties} so later REST steps in the
     * same attempt reuse the owner/member pack instead of minting a new
     * domain (which made create-member emails miss the account allowlist).
     */
    public static final String IDENTITY_PACK_READY = "_identityPackReady";

    public static boolean identityPackReady(Map<String, String> ctx) {
        return ctx != null && "true".equals(ctx.get(IDENTITY_PACK_READY));
    }

    public static void markIdentityPackReady(Map<String, String> ctx) {
        if (ctx != null) {
            ctx.put(IDENTITY_PACK_READY, "true");
        }
    }

    /**
     * True for the second HHonors enroll (member guest), whose ReadyAPI
     * body often reuses {@code ${Properties#Email}} even though the
     * identity pack binds that key to the <em>owner</em>.
     */
    public static boolean isMemberEnrollStep(String stepName) {
        if (stepName == null || stepName.isEmpty()) {
            return false;
        }
        String n = stepName.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]", "");
        return n.contains("memberhhonorsenroll")
                || n.contains("hhonorsenrollmember")
                || (n.contains("member") && n.contains("hhonorsenroll"));
    }

    /**
     * Ctx used to resolve a REST step's body/query. For member enroll,
     * {@code Properties.Email} / {@code EmailAddress} overlay the member
     * slot ({@code generatedemailAddress1}) so the request does not 409
     * as a duplicate of the owner guest. Session ctx is not mutated --
     * later create-account (6801) still sees owner email on {@code Properties.Email}.
     * 7816 member enroll templates that still say {@code #Properties_Email#}
     * resolve to {@code generatedemailAddress1}; 6801 templates that
     * already use that member slot are unchanged.
     */
    public static Map<String, String> ctxForStep(Map<String, String> ctx,
                                                 String stepName) {
        if (ctx == null || !isMemberEnrollStep(stepName)) {
            return ctx;
        }
        String memberEmail = firstNonBlank(ctx,
                "Properties.generatedemailAddress1",
                "Properties.GeneratedemailAddress1",
                "Properties.EmailMember",
                "Properties.emailMember",
                "Properties.guestMemberEmail");
        if (memberEmail == null || memberEmail.isEmpty()) {
            return ctx;
        }
        String ownerEmail = ctx.get("Properties.Email");
        if (memberEmail.equals(ownerEmail)) {
            return ctx;
        }
        // Two users in the pack (owner enrolls with generatedemailAddress,
        // member with Email -- 871 of 1101 cases): Email IS the member's,
        // and the later steps that send it again (CreatePendingAccountmember
        // "guestId": memberGuestID + "emailAddress": Email; a second member
        // enroll on generatedemailAddress1) must see the same address. The
        // overlay is for the one-user convention only, where owner and
        // member enrolls both read Email.
        String ownerGen = ctx.get("Properties.generatedemailAddress");
        if (ownerGen != null && !ownerGen.isEmpty() && ownerEmail != null
                && !ownerGen.equalsIgnoreCase(ownerEmail)) {
            return ctx;
        }
        Map<String, String> copy = new HashMap<>(ctx);
        CtxFields.putBothCases(copy, "Properties", "Email", memberEmail);
        CtxFields.putBothCases(copy, "Properties", "EmailAddress", memberEmail);
        LOG.info(" .. [member-enroll overlay] step={} Properties.Email {} -> {}",
                stepName, ownerEmail, memberEmail);
        return copy;
    }

    private static String firstNonBlank(Map<String, String> ctx, String... keys) {
        if (ctx == null) {
            return null;
        }
        for (String k : keys) {
            String v = ctx.get(k);
            if (v != null && !v.isEmpty()) {
                return v;
            }
        }
        return null;
    }

    /**
     * Refresh username/email/phone/domain/hhonorsNumber before a REST step
     * that submits fresh identity. Extracted IDs are left alone. Frozen
     * {@code Properties.Hardcodeddomain} is preserved when present so
     * Hilton stg's domain allowlist stays coherent with the body.
     *
     * <p>CSV domains win over the frozen allowlist when ReadyAPI Properties
     * last-write-wins a <em>negative</em> emailDomain: freemail
     * ({@code yahoo.com}) or competitor/managed create-400
     * ({@code wyn.com}, {@code coraltravel.com}). RestStep
     * {@code regenIdentity} must not replace those with {@code laafd.com}
     * (B2B-6238 / B2B-6324 expected 400).
     *
     * <p>Owner and member get <em>distinct</em> usernames and emails on the
     * <em>same</em> domain. ReadyAPI DataGenInput does that in one Groovy
     * step; the previous pack wrote the same username/email into every
     * slot so MemberHHonorsEnroll 409'd as a duplicate guest.
     */
    public static void regenRandomProperties(Map<String, String> ctx) {
        regenRandomProperties(ctx, null);
    }

    public static void regenRandomProperties(Map<String, String> ctx,
                                             Map<String, String> row) {
        if (ctx == null) {
            return;
        }
        String frozen = ctx.getOrDefault("Properties.Hardcodeddomain",
                ctx.getOrDefault("Properties.hardcodeddomain", ""));
        String csvDomain = firstNonBlank(row,
                "Properties.Domain", "Properties.domain", "Domain");
        // DataGen never writes a "www." into Domain; the amex cases' Groovy
        // does (def generatedDomain = 'www.amexoneclickuser234.com', 13
        // rows, emails on it too). That is the author's literal, verbatim.
        boolean literalWwwDomain = csvDomain != null
                && csvDomain.trim().toLowerCase(Locale.ROOT).startsWith("www.");
        boolean keepCsvDomain = csvDomain != null && !csvDomain.isEmpty()
                && (isFreemailDomain(csvDomain) || expectedCreate400(row) || literalWwwDomain);
        boolean hasFrozenDomain = !keepCsvDomain
                && frozen != null && !frozen.isEmpty();
        // Hardcodeddomain is a Properties value the row always carries; its
        // presence does not mean the case built identity on it. In 211 of the
        // 218 laafd.com rows ReadyAPI's DataGenInput generated a random domain
        // per run, and the saved values show it. Freezing those sent a domain
        // every test shares: B2B-3233 posts ${Properties#Domain} as a NEW
        // email domain expecting 201 and got 409, and verify-by-domain calls
        // matched other tests' accounts. Follow the row, as the email split
        // does; with no saved identity values the freeze stays.
        boolean usingFrozenDomain = hasFrozenDomain && !rowUsedOwnDomain(row, frozen);
        String domain;
        if (literalWwwDomain) {
            domain = csvDomain.trim();
        } else if (keepCsvDomain) {
            domain = normalizeDomain(csvDomain);
        } else if (usingFrozenDomain) {
            domain = frozen;
        } else if (hasFrozenDomain) {
            domain = freshDomainLike(normalizeDomain(csvDomain));
        } else {
            // ReadyAPI's DataGenInput draws from ALLOWED_DOMAINS; a random
            // word.com is only the fallback when that is not configured.
            domain = CtxFields.allowedDomainOrRandom();
        }
        String ownerUname = FakeData.username();
        String memberUname = distinctUsername(ownerUname);
        String extraUname = distinctUsername(ownerUname, memberUname);
        // A SECOND enrolled person, not another name for the member. It used
        // to be assigned memberUname, so a test that enrolled a member and
        // then enrolled "guest 2" submitted a username the tenant already
        // had -- a deterministic 409 every run, which no amount of extra
        // entropy would have fixed. Mirrors the email block, where
        // generatedemailAddress2 has always been distinct.
        String uname2 = distinctUsername(ownerUname, memberUname, extraUname);
        String uname3 = distinctUsername(ownerUname, memberUname, extraUname, uname2);
        String ownerEmail = FakeData.username() + "@" + domain;
        String memberEmail = distinctEmail(domain, ownerEmail);
        String email2 = distinctEmail(domain, ownerEmail, memberEmail);
        String email3 = distinctEmail(domain, ownerEmail, memberEmail, email2);
        String phone = FakeData.faker().numerify("#########");
        String hhon = FakeData.numericId();

        CtxFields.putBothCases(ctx, "Properties", "Username", ownerUname);
        CtxFields.putBothCases(ctx, "Properties", "usernamemember", memberUname);
        // usernameM IS the member under another name -- alias on purpose.
        CtxFields.putBothCases(ctx, "Properties", "usernameM", memberUname);
        CtxFields.putBothCases(ctx, "Properties", "usernamemember2",
                distinctUsername(ownerUname, memberUname, extraUname, uname2, uname3));
        CtxFields.putBothCases(ctx, "Properties", "Username2", uname2);
        CtxFields.putBothCases(ctx, "Properties", "username3", uname3);
        CtxFields.putBothCases(ctx, "Properties", "username1", extraUname);

        // generatedemailAddress is a DIFFERENT person from Email in most
        // ReadyAPI cases: DataGenInput builds them from two random users, and
        // e.g. B2B-6860 creates the account with ownerEmailAddress=Email but
        // adds the pending member with emailAddress=generatedemailAddress.
        // Writing the owner's email into both made that member call send an
        // address the account already has -- 400 code 509 "The email address
        // is already in use by a member". The row carries ReadyAPI's saved
        // values, which differ exactly when the script used two users (871 of
        // 871 cases) and match when it used one (81 of 81), so follow them.
        String generatedEmailAddress = rowHasDistinctGeneratedEmail(row)
                ? distinctEmail(domain, ownerEmail, memberEmail, email2, email3)
                : ownerEmail;
        CtxFields.putBothCases(ctx, "Properties", "Email", ownerEmail);
        CtxFields.putBothCases(ctx, "Properties", "EmailAddress", ownerEmail);
        CtxFields.putBothCases(ctx, "Properties", "GeneratedEmail", ownerEmail);
        CtxFields.putBothCases(ctx, "Properties", "generatedemailAddress", generatedEmailAddress);

        CtxFields.putBothCases(ctx, "Properties", "EmailMember", memberEmail);
        CtxFields.putBothCases(ctx, "Properties", "guestMemberEmail", memberEmail);
        CtxFields.putBothCases(ctx, "Properties", "generatedemailAddress1", memberEmail);
        CtxFields.putBothCases(ctx, "Properties", "generatedemailAddress2", email2);
        CtxFields.putBothCases(ctx, "Properties", "generatedemailAddress3", email3);
        // The suite also spells these slots with an underscore
        // (username_1 / generatedemailAddress_1, B2B-7504/7505). Written only
        // when the row names them, so ctx does not grow for everyone else.
        String[][] underscored = {
            {"username1", "username_1"}, {"Username2", "Username_2"},
            {"username3", "username_3"},
        };
        for (String[] pair : underscored) {
            if (row != null && (row.containsKey("Properties." + pair[1])
                    || row.containsKey("Properties." + CtxFields.flipFirst(pair[1])))) {
                CtxFields.putBothCases(ctx, "Properties", pair[1],
                        ctx.getOrDefault("Properties." + pair[0], ""));
            }
        }

        // Numbered owner/member emails the suite keeps as saved literals
        // (B2B-3503 Email1..3, B2B-7505 Email_1..3). Saved on the identity
        // domain -> fresh addresses on the regenerated one, distinct from
        // each other; saved on another slot -> bindEmailsToSavedDomains.
        String[] usedEmails = {ownerEmail, memberEmail, email2, email3, generatedEmailAddress};
        for (String k : new String[] {"Email1", "Email2", "Email3", "Email_1", "Email_2", "Email_3"}) {
            String savedN = firstNonBlank(row, "Properties." + k);
            int atN = savedN == null ? -1 : savedN.lastIndexOf('@');
            if (atN <= 0) {
                continue;
            }
            if (sameDomainOrLabel(normalizeDomain(savedN.substring(atN + 1)), normalizeDomain(csvDomain))) {
                String fresh = distinctEmail(domain, usedEmails);
                usedEmails = java.util.Arrays.copyOf(usedEmails, usedEmails.length + 1);
                usedEmails[usedEmails.length - 1] = fresh;
                CtxFields.putBothCases(ctx, "Properties", k, fresh);
            }
        }
        CtxFields.putBothCases(ctx, "Properties", "Phone", phone);
        CtxFields.putBothCases(ctx, "Properties", "phoneNumber", phone);
        CtxFields.putBothCases(ctx, "Properties", "Domain", domain);
        CtxFields.putBothCases(ctx, "Properties", "websiteDomain", domain);
        CtxFields.putBothCases(ctx, "Properties", "weburl", domain);
        // DataGenInput derives these from the SAME label as Domain/Email:
        //   def emailDomain = generatedDomain + ".com"
        //   def websiteDomain = "www." + generatedDomain + ".com"
        // and createAccount sends emailDomains: ["${Properties#emailDomain}"].
        // Left alone they kept the pack's random value while the owner email
        // moved here -> 400/503 "Email address domain must match".
        String savedEmailDomain = firstNonBlank(row, "Properties.emailDomain",
                "Properties.EmailDomain", "Properties.emaildomain");
        if (savedEmailDomain == null || savedEmailDomain.isEmpty()) {
            // not in the row (a generator pack may still have put one in ctx)
            if (firstNonBlank(ctx, "Properties.emailDomain", "Properties.emaildomain",
                    "Properties.EmailDomain") != null) {
                CtxFields.putBothCases(ctx, "Properties", "emailDomain", domain);
            }
        } else if (sameDomainOrLabel(normalizeDomain(savedEmailDomain), normalizeDomain(csvDomain))) {
            CtxFields.putBothCases(ctx, "Properties", "emailDomain", domain);
        } else {
            // the amex rows keep EmailDomain = sa.hilton.com, a literal on a
            // domain that is not the identity: the author's, kept as saved
            CtxFields.putBothCases(ctx, "Properties", "emailDomain", savedEmailDomain);
        }
        String savedSite = firstNonBlank(row, "Properties.Website", "Properties.website");
        if (savedSite != null && !savedSite.isEmpty()
                && sameDomainOrLabel(normalizeDomain(savedSite), normalizeDomain(csvDomain))) {
            CtxFields.putBothCases(ctx, "Properties", "Website", "www." + domain);
        }
        if (hasFrozenDomain) {
            // These stay ON Hardcodeddomain. An earlier fix dragged them onto
            // the identity domain instead, to make B2B-3216's create coherent
            // (owner generatedemailAddress vs emailDomains
            // ["${Properties#Hardcodeddomain}"]). That collapsed
            // Hardcodeddomain into Domain, and B2B-3233 posts
            // ${Properties#Domain} as a NEW email domain expecting 201 -- the
            // account already owned it, so it got 409. Coherence is restored
            // the other way round instead: bindEmailsToSavedDomains moves the
            // EMAIL onto whichever domain the row shows it on.
            String hcEmail = extraUname + "@" + frozen;
            CtxFields.putBothCases(ctx, "Properties", "hardcodedemail", hcEmail);
            CtxFields.putBothCases(ctx, "Properties", "Hardcodeddomain", frozen);
            String updatedEmail = "bh" + extraUname + "jff@" + frozen;
            CtxFields.putBothCases(ctx, "Properties", "updatedemail", updatedEmail);
            CtxFields.putBothCases(ctx, "Properties", "updatedmailAddress", updatedEmail);
        }
        regenNumberedDomains(ctx, row);
        bindEmailsToSavedDomains(ctx, row);
        regenRowShapedIdentity(ctx, row, domain, csvDomain);
        CtxFields.putBothCases(ctx, "Properties", "hhonorsNumber", hhon);
        applyCsvRandomDomains(ctx, row);
        alignPackEmailsToIdentity(ctx, row, domain, frozen);
        restoreAuthorLiteralEmail(ctx, row);
    }

    /**
     * The translated DataGen pack generates its extra email fields
     * (generatedemailAddress_2, Email4, employeeEmail2 ...) on ONE random
     * domain before regen runs; regen then moves the identity to a fresh
     * domain and those emails stay behind (B2B-3233: the member enrolled on
     * the random domain, add-member 503 "must match an allowed domain").
     * ReadyAPI builds every pack email on generatedDomain, so a pack email
     * that is on none of the domains the row or regen own is moved onto
     * the identity domain, local part kept. Row-saved values and the keys
     * regen writes are not pack emails and are left alone.
     */
    static void alignPackEmailsToIdentity(Map<String, String> ctx, Map<String, String> row,
                                          String domain, String frozen) {
        if (ctx == null || domain == null || domain.isEmpty()) {
            return;
        }
        String identity = normalizeDomain(domain);
        Set<String> keep = new HashSet<>();
        keep.add(identity);
        if (frozen != null && !frozen.isEmpty()) {
            keep.add(normalizeDomain(frozen));
        }
        for (String f : BINDABLE_DOMAINS) {
            String d = normalizeDomain(firstNonBlank(row, "Properties." + f));
            if (!d.isEmpty()) {
                keep.add(d);
            }
            d = normalizeDomain(firstNonBlank(ctx, "Properties." + f));
            if (!d.isEmpty()) {
                keep.add(d);
            }
        }
        for (String key : new java.util.ArrayList<>(ctx.keySet())) {
            if (key == null || !key.startsWith("Properties.")) {
                continue;
            }
            String field = key.substring("Properties.".length());
            String fl = field.toLowerCase(Locale.ROOT);
            if (NAMED_IDENTITY_KEYS.contains(fl) || !fl.contains("mail")) {
                continue;
            }
            if (row != null && (row.containsKey(key)
                    || row.containsKey("Properties." + CtxFields.flipFirst(field)))) {
                continue;   // a saved value: regenRowShapedIdentity's business
            }
            String v = ctx.get(key);
            int at = v == null ? -1 : v.lastIndexOf('@');
            if (at <= 0 || at == v.length() - 1) {
                continue;
            }
            String d = normalizeDomain(v.substring(at + 1));
            if (d.isEmpty() || keep.contains(d) || isFreemailDomain(d)) {
                continue;
            }
            CtxFields.putBothCases(ctx, "Properties", field, v.substring(0, at) + "@" + domain);
        }
    }

    /** Keys the named regeneration above already decided; the generic pass leaves them. */
    private static final Set<String> NAMED_IDENTITY_KEYS = Set.of(
            "email", "emailaddress", "generatedemail", "generatedemailaddress",
            "generatedemailaddress1", "generatedemailaddress2", "generatedemailaddress3",
            // generatedemailAddress_N is no longer written by regen (it lives on
            // Domain_N when saved, and is a pack email when not): not named here
            "emailmember", "guestmemberemail", "email1", "email2", "email3",
            "email_1", "email_2", "email_3", "hardcodedemail", "updatedemail",
            "updatedmailaddress", "phone", "phonenumber", "hhonorsnumber");

    /**
     * Every OTHER email- or phone-shaped saved Properties value follows what
     * the row says ReadyAPI's DataGen did with it (coupling scan over 697
     * rows: updateemail 51, generateEmail 37, employeeEmail 9,
     * updateEmployeeEmail 5, guestMemberEmail1 4; Phone2 95, newPhone 15,
     * updatedPhone 6, Phone_1..3, phoneNumber1 -- all Groovy-set per run):
     * <ul>
     *   <li>saved on the identity domain -> fresh local part on the
     *       regenerated identity domain (left alone, it pointed at the OLD
     *       domain and the API answered 503 "must match an allowed domain",
     *       or re-sent the same address every run);</li>
     *   <li>saved on another saved slot (Domain2, Hardcodeddomain) ->
     *       already moved by bindEmailsToSavedDomains, untouched here;</li>
     *   <li>saved on a foreign literal domain (sa.hilton.com/) -> the
     *       author's domain, fresh local part;</li>
     *   <li>digits-only phone -> a fresh number of the same length, except
     *       names that say "exist" (existPhoneNo is an author literal).</li>
     * </ul>
     */
    static void regenRowShapedIdentity(Map<String, String> ctx, Map<String, String> row,
                                       String domain, String csvDomain) {
        if (ctx == null || row == null || row.isEmpty()) {
            return;
        }
        Set<String> slotDomains = new HashSet<>();
        for (String f : BINDABLE_DOMAINS) {
            String d = normalizeDomain(firstNonBlank(row, "Properties." + f));
            if (!d.isEmpty()) {
                slotDomains.add(d);
            }
        }
        Set<String> used = new HashSet<>();
        for (Map.Entry<String, String> e : ctx.entrySet()) {
            if (e.getKey() != null && e.getKey().startsWith("Properties.") && e.getValue() != null
                    && e.getValue().indexOf('@') > 0) {
                used.add(e.getValue().toLowerCase(Locale.ROOT));
            }
        }
        for (Map.Entry<String, String> e : row.entrySet()) {
            String key = e.getKey();
            String saved = e.getValue() == null ? "" : e.getValue().trim();
            if (key == null || !key.startsWith("Properties.") || saved.isEmpty()) {
                continue;
            }
            String field = key.substring("Properties.".length());
            String fl = field.toLowerCase(Locale.ROOT);
            if (NAMED_IDENTITY_KEYS.contains(fl)) {
                continue;
            }
            int at = saved.lastIndexOf('@');
            if (at > 0 && at < saved.length() - 1 && fl.contains("mail")) {
                String savedDom = saved.substring(at + 1);
                String nd = normalizeDomain(savedDom);
                String target;
                if (sameDomainOrLabel(nd, normalizeDomain(csvDomain))) {
                    target = domain;
                } else if (slotDomains.contains(nd)) {
                    continue;
                } else {
                    target = savedDom;          // author's literal domain, verbatim
                }
                String fresh = FakeData.username() + "@" + target;
                for (int i = 0; i < 8 && used.contains(fresh.toLowerCase(Locale.ROOT)); i++) {
                    fresh = FakeData.username() + "@" + target;
                }
                used.add(fresh.toLowerCase(Locale.ROOT));
                CtxFields.putBothCases(ctx, "Properties", field, fresh);
                continue;
            }
            if (fl.contains("phone") && !fl.contains("exist") && saved.length() >= 9
                    && saved.chars().allMatch(Character::isDigit)) {
                CtxFields.putBothCases(ctx, "Properties", field,
                        FakeData.faker().numerify("#".repeat(saved.length())));
            }
        }
    }

    /**
     * The author typed a DOMAIN into Properties.Email (blackstone.com for the
     * blocklist case, qmekr.com for post_emaildomain) and the template reads
     * it as one: emailDomains: ["${Properties#Email}"], "emailDomain":
     * "${Properties#Email}". Regenerating it sent an address and the
     * negative test stopped testing what it was written for. A value with no
     * '@' cannot be a generated email, so it is the author's, and so are the
     * generatedemailAddress / websiteDomain saved beside it.
     */
    private static boolean looksLikeDomain(String v) {
        String d = normalizeDomain(v);
        int dot = d.lastIndexOf('.');
        if (dot <= 0 || dot == d.length() - 1) {
            return false;
        }
        for (int i = 0; i < d.length(); i++) {
            char c = d.charAt(i);
            if (!(Character.isLetterOrDigit(c) || c == '.' || c == '-')) {
                return false;
            }
        }
        return true;
    }

    static void restoreAuthorLiteralEmail(Map<String, String> ctx, Map<String, String> row) {
        if (ctx == null || row == null) {
            return;
        }
        String saved = firstNonBlank(row, "Properties.Email", "Properties.email");
        if (saved == null || saved.isEmpty() || saved.indexOf('@') >= 0
                || !looksLikeDomain(saved)) {
            return;   // an address, or a manual-suite marker like <<email>>
        }
        for (String k : new String[] {"Email", "websiteDomain"}) {
            String v = firstNonBlank(row, "Properties." + k, "Properties." + CtxFields.flipFirst(k));
            if (v != null && !v.isEmpty()) {
                CtxFields.putBothCases(ctx, "Properties", k, v);
            }
        }
        // The saved generatedemailAddress (umzgxl@blackstone.com) is
        // Username + "@" + that literal domain; re-sending it verbatim made
        // the owner enroll a 409 on every run after the first (13 rows).
        // Fresh local part, the author's domain.
        // B2B-5264: Email = linkedin.com, generatedemailAddress =
        // yvwlsf@test.highbook.com, websiteDomain = test.highbook.com -- the
        // owner sits on the WEBSITE domain, a third one. Whatever domain the
        // author put the saved address on is the one the API is meant to
        // see beside that websiteDomain; only the local part is fresh.
        String savedGen = firstNonBlank(row, "Properties.generatedemailAddress",
                "Properties.GeneratedemailAddress");
        int genAt = savedGen == null ? -1 : savedGen.lastIndexOf('@');
        if (genAt > 0 && genAt < savedGen.length() - 1) {
            String local = firstNonBlank(ctx, "Properties.Username", "Properties.username");
            if (local == null || local.isEmpty()) {
                local = FakeData.username();
            }
            CtxFields.putBothCases(ctx, "Properties", "generatedemailAddress",
                    local + "@" + savedGen.substring(genAt + 1).trim());
        }
    }

    /**
     * ReadyAPI Properties last-write-wins {@code RandomDomain} /
     * {@code RandomDomain2} (competitor/managed add/verify). DataGen
     * putIfAbsent would otherwise keep a generated {@code word.com}.
     */
    private static void applyCsvRandomDomains(Map<String, String> ctx,
                                              Map<String, String> row) {
        String d1 = firstNonBlank(row,
                "Properties.RandomDomain", "Properties.randomDomain",
                "RandomDomain");
        String d2 = firstNonBlank(row,
                "Properties.RandomDomain2", "Properties.randomDomain2",
                "RandomDomain2");
        // The CSV values are a FROZEN snapshot of what ReadyAPI's Groovy
        // picked live from account_rules. Offer the live rows instead when
        // that is switched on; returns the CSV values untouched otherwise.
        String[] resolved = com.ak.api.db.repo.DomainRules.overrideOrCsv(row, d1, d2);
        boolean live1 = resolved[0] != null && !resolved[0].equals(d1);
        boolean live2 = resolved[1] != null && !resolved[1].equals(d2);
        d1 = resolved[0];
        d2 = resolved[1];
        // ReadyAPI saved RandomDomain = the row's own domain in 11 cases
        // (createAccount emailDomains: ["${Properties#RandomDomain}"]). The
        // identity domain is regenerated above, so re-putting the CSV value
        // paired a fresh owner email with the OLD domain -> 400/503 "Email
        // address domain must match an allowed domain". A live (DB) override
        // is deliberate and is kept.
        if (!live1 && d1 != null && !d1.isEmpty() && savedIdentityDomain(row, d1)) {
            String current = firstNonBlank(ctx, "Properties.Domain", "Properties.domain");
            if (current != null && !current.isEmpty()) {
                d1 = current;
            }
        }
        if (!live2 && d2 != null && !d2.isEmpty()) {
            String saved2 = normalizeDomain(firstNonBlank(row, "Properties.Domain2", "Properties.domain2"));
            String current2 = firstNonBlank(ctx, "Properties.Domain2", "Properties.domain2");
            if (!saved2.isEmpty() && saved2.equalsIgnoreCase(normalizeDomain(d2))
                    && current2 != null && !current2.isEmpty()) {
                d2 = current2;
            }
        }
        if (d1 != null && !d1.isEmpty()) {
            CtxFields.putBothCases(ctx, "Properties", "RandomDomain", d1);
        }
        if (d2 != null && !d2.isEmpty()) {
            CtxFields.putBothCases(ctx, "Properties", "RandomDomain2", d2);
        }
    }

    /**
     * True when this case's saved ReadyAPI values give {@code Email} and
     * {@code generatedemailAddress} different addresses -- i.e. its
     * DataGenInput built them from two random users. No row, or either value
     * missing, keeps the previous behaviour (one owner email for both).
     */
    static boolean rowHasDistinctGeneratedEmail(Map<String, String> row) {
        String email = firstNonBlank(row, "Properties.Email", "Properties.email");
        String generated = firstNonBlank(row,
                "Properties.generatedemailAddress", "Properties.GeneratedemailAddress");
        if (email == null || generated == null
                || !email.contains("@") || !generated.contains("@")) {
            return false;
        }
        return !email.trim().equalsIgnoreCase(generated.trim());
    }

    /**
     * Fresh values for Domain1..Domain9 and the Website slot built on them.
     *
     * <p>ReadyAPI's DataGenInput generates every numbered domain per run --
     * {@code Domain2 = generatedWebsite + ".com"} in 13 of the 18 cases that
     * send one, and B2B-3553 builds four distinct shapes
     * ({@code "8-"+rand+".com"}, {@code "a1."+rand+".org"},
     * {@code "88"+rand+".in"}, {@code rand+".co.uk"}). We generated none of
     * them, so the request carried the value saved in the CSV / bundled
     * defaults -- a domain the author's own run already registered, which is
     * what an account create rejects.</p>
     *
     * <p>The seeded value is the shape template: its prefix and TLD are kept
     * and only the random middle is replaced, so {@code a1.y7cycf.org} stays
     * an {@code a1.*.org}. {@code Website} is regenerated ONLY when it was
     * {@code "www." + <that domain>}, because every case that sends it does
     * exactly {@code Website = "www." + Domain2} -- the pair has to stay
     * consistent or the account's websiteDomain stops matching its
     * emailDomains. A freemail value, or a row whose create expects 400, is
     * left alone: there the domain IS the point of the test.</p>
     */
    static void regenNumberedDomains(Map<String, String> ctx, Map<String, String> row) {
        if (ctx == null || expectedCreate400(row)) {
            return;
        }
        for (int n = 1; n <= 9; n++) {
            // both spellings: Domain2 (most cases) and Domain_1 (B2B-7505 family)
            for (String sep : new String[] {"", "_"}) {
                regenNumberedDomain(ctx, row, n, sep);
            }
        }
    }

    private static void regenNumberedDomain(Map<String, String> ctx, Map<String, String> row,
                                            int n, String sep) {
        {
            String key = "Properties.Domain" + sep + n;
            String seeded = firstNonBlank(ctx, key, "Properties.domain" + sep + n);
            if (seeded == null || seeded.isEmpty()) {
                seeded = firstNonBlank(row, key, "Properties.domain" + sep + n);   // row not yet seeded into ctx
            }
            if (seeded == null || seeded.isEmpty() || isFreemailDomain(seeded)) {
                return;
            }
            // The row's saved DomainN is the shape to preserve; the ctx value
            // may already be a word the translated generator pack put there.
            String savedN = firstNonBlank(row, key, "Properties.domain" + sep + n);
            String shape = savedN != null && !savedN.isEmpty() ? savedN : seeded;
            String fresh = freshDomainLike(normalizeDomain(shape));
            CtxFields.putBothCases(ctx, "Properties", "Domain" + sep + n, fresh);
            for (String siteKey : new String[] {"Website", "Website" + sep + n,
                    "websitedomain" + sep + n, "websiteDomain" + sep + n}) {
                String site = firstNonBlank(ctx, "Properties." + siteKey);
                String savedSiteN = firstNonBlank(row, "Properties." + siteKey);
                boolean follows = (site != null && site.equalsIgnoreCase("www." + seeded))
                        || (savedSiteN != null && savedN != null && !savedN.isEmpty()
                            && normalizeDomain(savedSiteN).equalsIgnoreCase(normalizeDomain(savedN)));
                if (follows) {
                    CtxFields.putBothCases(ctx, "Properties", siteKey, "www." + fresh);
                }
            }
        }
    }

    /** Identity email slots, and the domain slots a row can bind them to. */
    private static final String[] BINDABLE_EMAILS = {
        "Email", "EmailAddress", "GeneratedEmail", "generatedemailAddress",
        "generatedemailAddress1", "generatedemailAddress2", "generatedemailAddress3",
        "generatedemailAddress_1", "generatedemailAddress_2", "generatedemailAddress_3",
        "Email1", "Email2", "Email3", "Email_1", "Email_2", "Email_3",
        "EmailMember", "guestMemberEmail",
    };

    private static final String[] BINDABLE_DOMAINS = {
        "Hardcodeddomain", "Domain2", "Domain1", "Domain3", "Domain4",
        "Domain5", "Domain6", "Domain7", "Domain8", "Domain9",
        "Domain_1", "Domain_2", "Domain_3", "Domain_4", "Domain_5",
        "Domain_6", "Domain_7", "Domain_8", "Domain_9",
    };

    /**
     * Put each generated email on the domain ITS OWN saved value sat on.
     *
     * <p>regen builds every address on the single identity domain, but
     * ReadyAPI does not: across the 691 rows, 1,248 saved emails sit on
     * {@code Domain} while 62 sit on {@code Domain2} and 35 on
     * {@code Hardcodeddomain}. The account create pairs a specific email with
     * a specific domain list -- B2B-5530 sends ownerEmailAddress=Email with
     * emailDomains=[Domain2]; B2B-3216 sends generatedemailAddress with
     * emailDomains=[Hardcodeddomain] -- so an email on the wrong domain is
     * rejected with 400 code 997/503 "Email address domain must match an
     * allowed domain within program account".</p>
     *
     * <p>The row decides, per email: only a saved value whose domain matches a
     * saved domain slot OTHER than {@code Domain} is moved, onto whatever that
     * slot now holds (already regenerated). No saved value, or one already on
     * {@code Domain}, is left exactly as regen built it.</p>
     */
    static void bindEmailsToSavedDomains(Map<String, String> ctx, Map<String, String> row) {
        if (ctx == null || row == null || row.isEmpty()) {
            return;
        }
        String savedIdentity = normalizeDomain(
                firstNonBlank(row, "Properties.Domain", "Properties.domain"));
        for (String emailField : BINDABLE_EMAILS) {
            String savedEmail = firstNonBlank(row, "Properties." + emailField);
            int at = savedEmail == null ? -1 : savedEmail.lastIndexOf('@');
            if (at <= 0) {
                continue;
            }
            String savedEmailDomain = normalizeDomain(savedEmail.substring(at + 1));
            if (savedEmailDomain.isEmpty()
                    || sameDomainOrLabel(savedEmailDomain, savedIdentity)) {
                continue;   // regen's identity domain is already right
            }
            for (String domainField : BINDABLE_DOMAINS) {
                String savedDomain = normalizeDomain(
                        firstNonBlank(row, "Properties." + domainField));
                if (savedDomain.isEmpty()
                        || !savedDomain.equalsIgnoreCase(savedEmailDomain)) {
                    continue;
                }
                String current = normalizeDomain(
                        firstNonBlank(ctx, "Properties." + domainField));
                if (current.isEmpty()) {
                    break;
                }
                String existing = firstNonBlank(ctx, "Properties." + emailField);
                int existingAt = existing == null ? -1 : existing.lastIndexOf('@');
                String local = existingAt > 0
                        ? existing.substring(0, existingAt) : FakeData.username();
                CtxFields.putBothCases(ctx, "Properties", emailField, local + "@" + current);
                break;
            }
        }
    }

    /**
     * True when the row's saved ReadyAPI values put an email on the saved
     * {@code Properties.Domain} and that domain is not {@code frozen} -- i.e.
     * DataGenInput generated its own domain for this case.
     */
    /**
     * "kkzgg.com" is the same domain as the bare label "kkzgg": DataGenInput
     * stores Domain as the label and builds Email / emailDomain /
     * websiteDomain as label + ".com" (22 rows). Exact match otherwise.
     */
    static boolean sameDomainOrLabel(String emailDomain, String identity) {
        if (emailDomain == null || identity == null || identity.isEmpty()) {
            return false;
        }
        String e = emailDomain.trim().toLowerCase(Locale.ROOT);
        String i = identity.trim().toLowerCase(Locale.ROOT);
        return e.equals(i) || (i.indexOf('.') < 0 && e.startsWith(i + "."));
    }

    /** Was {@code d} the row's own identity domain (Domain, or the saved owner email's)? */
    private static boolean savedIdentityDomain(Map<String, String> row, String d) {
        if (row == null) {
            return false;
        }
        String nd = normalizeDomain(d);
        String saved = normalizeDomain(firstNonBlank(row, "Properties.Domain", "Properties.domain"));
        if (!saved.isEmpty() && sameDomainOrLabel(nd, saved)) {
            return true;
        }
        for (String k : new String[] {"Properties.Email", "Properties.EmailAddress",
                "Properties.generatedemailAddress", "Properties.GeneratedEmail"}) {
            String v = row.get(k);
            int at = v == null ? -1 : v.lastIndexOf('@');
            if (at > 0 && normalizeDomain(v.substring(at + 1)).equalsIgnoreCase(nd)) {
                return true;
            }
        }
        return false;
    }

    static boolean rowUsedOwnDomain(Map<String, String> row, String frozen) {
        String saved = firstNonBlank(row, "Properties.Domain", "Properties.domain");
        if (saved == null || frozen == null) {
            return false;
        }
        String domain = normalizeDomain(saved);
        if (domain.isEmpty() || domain.equalsIgnoreCase(normalizeDomain(frozen))) {
            return false;
        }
        // A saved hardcodedemail ON the frozen domain means the case is built
        // around that domain itself: B2B-4913 sends
        // ownerEmailAddress=${Properties#hardcodedemail} with
        // emailDomains=[${Properties#Hardcodeddomain}] and expects 400
        // "duplicate managed account" -- which only happens because
        // explorer.de is a managed domain on the target. Generating a fresh
        // domain there would turn that 400 into a 200. Only 11 of the 245
        // rows carrying Hardcodeddomain have this column.
        String hcEmailSaved = firstNonBlank(row,
                "Properties.hardcodedemail", "Properties.Hardcodedemail");
        int hcAt = hcEmailSaved == null ? -1 : hcEmailSaved.lastIndexOf('@');
        if (hcAt > 0 && hcEmailSaved.substring(hcAt + 1).trim()
                .equalsIgnoreCase(normalizeDomain(frozen))) {
            return false;
        }
        for (String k : new String[] {"Properties.Email", "Properties.generatedemailAddress",
                "Properties.EmailAddress", "Properties.GeneratedEmail",
                "Properties.Email1", "Properties.Email2", "Properties.Email3",
                "Properties.Email_1", "Properties.Email_2", "Properties.Email_3"}) {
            String v = row.get(k);
            int at = v == null ? -1 : v.lastIndexOf('@');
            if (at > 0 && sameDomainOrLabel(v.substring(at + 1).trim(), domain)) {
                return true;
            }
        }
        return false;
    }

    /**
     * A new domain of the same kind as ReadyAPI's saved one: from
     * ALLOWED_DOMAINS when the saved domain is on that list, otherwise a
     * random name with the saved TLD, as DataGenInput builds it.
     */
    static String freshDomainLike(String savedDomain) {
        String saved = savedDomain == null ? "" : savedDomain.toLowerCase(Locale.ROOT);
        for (String d : Config.get("ALLOWED_DOMAINS", "").split(",")) {
            if (!saved.isEmpty() && saved.equals(d.trim().toLowerCase(Locale.ROOT))) {
                return CtxFields.allowedDomainOrRandom();
            }
        }
        // Keep BOTH ends of ReadyAPI's shape: B2B-3553 builds "8-<rand>.com",
        // "a1.<rand>.org", "88<rand>.in" and "<rand>.co.uk", and the account
        // create treats those as different domains. The TLD is the trailing
        // run of letters-only labels (".org", ".co.uk"). In what precedes the
        // random middle, the prefix runs to the last "." or "-" of the head,
        // else to its leading digits ("88lhtzqo" -> "88"). A regex tried this
        // first and dropped the "a1." prefix, because that prefix starts with
        // a letter: a1.y7cycf.org regenerated as tdupwa.org.
        String tld = "";
        for (int i = 0; i < saved.length(); i++) {
            if (saved.charAt(i) != '.') {
                continue;
            }
            String candidate = saved.substring(i);
            boolean lettersOnly = true;
            for (int j = 1; j < candidate.length(); j++) {
                char c = candidate.charAt(j);
                if (c != '.' && (c < 'a' || c > 'z')) {
                    lettersOnly = false;
                    break;
                }
            }
            if (lettersOnly) {
                tld = candidate;
                break;
            }
        }
        if (tld.isEmpty()) {
            tld = ".com";
        }
        // A bare label ("kkzgg": DataGenInput's Domain, with emailDomain =
        // label + ".com") has nothing to strip; chopping tld.length() chars
        // off it left one letter.
        String head = saved.endsWith(tld)
                ? saved.substring(0, saved.length() - tld.length()) : saved;
        int sep = Math.max(head.lastIndexOf('.'), head.lastIndexOf('-'));
        String prefix;
        if (sep >= 0) {
            prefix = head.substring(0, sep + 1);
        } else {
            int d = 0;
            while (d < head.length() && head.charAt(d) >= '0' && head.charAt(d) <= '9') {
                d++;
            }
            prefix = head.substring(0, d);
        }
        return prefix + FakeData.username() + tld;
    }

    /** True when the CSV create step is an expected-400 emailDomain case. */
    static boolean expectedCreate400(Map<String, String> row) {
        if (row == null || row.isEmpty()) {
            return false;
        }
        for (Map.Entry<String, String> e : row.entrySet()) {
            String k = e.getKey();
            if (k == null) {
                continue;
            }
            String kl = k.toLowerCase(Locale.ROOT);
            if (kl.startsWith("expected_") && kl.contains("http_request_400")
                    && kl.endsWith("status_code")
                    && "400".equals((e.getValue() == null ? "" : e.getValue()).trim())) {
                return true;
            }
        }
        return false;
    }

    /** Consumer freemail domains ReadyAPI uses for expected-400 emailDomain cases. */
    private static final Set<String> FREEMAIL_DOMAINS = Set.of(
            "yahoo.com", "gmail.com", "hotmail.com", "aol.com", "outlook.com",
            "live.com", "msn.com", "icloud.com", "mail.com", "ymail.com",
            "protonmail.com", "gmx.com", "zoho.com", "me.com", "mac.com");

    static boolean isFreemailDomain(String raw) {
        String d = normalizeDomain(raw);
        return d != null && FREEMAIL_DOMAINS.contains(d);
    }

    /**
     * Lower-cased, trimmed, {@code www.}-stripped -- and NEVER null.
     *
     * <p>Every caller compares the result with {@code .isEmpty()} or
     * {@code .equalsIgnoreCase(...)} directly. The previous version had an
     * unreachable second {@code null} branch AND a tail that returned null for
     * a blank input, so a saved email of {@code user@} or a domain of literally
     * {@code www.} threw NullPointerException inside bindEmailsToSavedDomains
     * instead of simply not matching. Empty string is the one contract.</p>
     */
    private static String normalizeDomain(String raw) {
        if (raw == null) {
            return "";
        }
        String d = raw.trim().toLowerCase(Locale.ROOT);
        if (d.startsWith("www.")) {
            d = d.substring(4);
        }
        return d;
    }

    private static String distinctUsername(String... used) {
        for (int i = 0; i < 8; i++) {
            String u = FakeData.username();
            if (!containsIgnoreCase(u, used)) {
                return u;
            }
        }
        return FakeData.username() + "x";
    }

    private static String distinctEmail(String domain, String... used) {
        for (int i = 0; i < 8; i++) {
            String e = FakeData.username() + "@" + domain;
            if (!containsIgnoreCase(e, used)) {
                return e;
            }
        }
        return FakeData.username() + "x@" + domain;
    }

    private static boolean containsIgnoreCase(String needle, String... hay) {
        if (needle == null || hay == null) {
            return false;
        }
        for (String h : hay) {
            if (needle.equalsIgnoreCase(h)) {
                return true;
            }
        }
        return false;
    }

    /**
     * CSV row, then {@code test_data.*} config, then the bound suite's
     * {@code test_data_defaults/<suite>.json}. When no suite is bound
     * (unit tests), merges every JSON in that folder.
     */
    public static String testData(Map<String, String> row, String key) {
        if (row != null) {
            String v = row.get(key);
            if (v != null && !v.isEmpty()) {
                return v;
            }
        }
        String cfg = Config.get("test_data." + key, null);
        if (cfg != null && !cfg.isEmpty()) {
            return cfg;
        }
        return testDataDefaults().getOrDefault(key, "");
    }

    public static Set<String> testDataDefaultKeys() {
        return Collections.unmodifiableSet(testDataDefaults().keySet());
    }

    private static Map<String, String> testDataDefaults() {
        String suite = optionalSuiteName();
        if (suite != null) {
            return loadJsonResource("test_data_defaults/" + suite + ".json");
        }
        return loadAllTestDataDefaults();
    }

    private static Map<String, String> loadAllTestDataDefaults() {
        Map<String, String> cached = ALL_TEST_DATA_DEFAULTS;
        if (cached != null) {
            return cached;
        }
        Map<String, String> merged = new HashMap<>();
        try {
            Enumeration<URL> roots = ImportedScenario.class.getClassLoader()
                    .getResources("test_data_defaults");
            while (roots.hasMoreElements()) {
                URL url = roots.nextElement();
                if (!"file".equals(url.getProtocol())) {
                    continue;
                }
                File dir = new File(url.toURI());
                File[] files = dir.listFiles((d, n) -> n.endsWith(".json"));
                if (files == null) {
                    continue;
                }
                Arrays.sort(files, (a, b) -> a.getName().compareTo(b.getName()));
                for (File f : files) {
                    merged.putAll(loadJsonResource("test_data_defaults/" + f.getName()));
                }
            }
        } catch (IOException | URISyntaxException ignored) {
            // Missing folder -> defaults stay empty; row + config still work.
        }
        ALL_TEST_DATA_DEFAULTS = merged;
        return merged;
    }

    private static Map<String, String> loadJsonResource(String path) {
        return JSON_CACHE.computeIfAbsent(path, ImportedScenario::readJsonResource);
    }

    private static Map<String, String> readJsonResource(String path) {
        Map<String, String> m = new HashMap<>();
        try (InputStream in = ImportedScenario.class.getClassLoader()
                .getResourceAsStream(path)) {
            if (in == null) {
                return m;
            }
            JsonNode root = JSON.readTree(in);
            Iterator<Map.Entry<String, JsonNode>> it = root.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                m.put(e.getKey(), e.getValue().asText(""));
            }
        } catch (IOException ignored) {
            // File missing / unreadable -> defaults stay empty.
        }
        return m;
    }

}
