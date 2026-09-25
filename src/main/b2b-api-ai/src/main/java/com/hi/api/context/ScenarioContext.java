package com.hi.api.context;

import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Typed view over the scenario context.
 *
 * <h2>The problem this solves</h2>
 *
 * {@code ctx} is a {@code Map<String,String>} carrying 678 distinct keys across
 * ~19,700 call sites. The same logical value is spelled many ways: an account id
 * appears under 26 keys ({@code PropertiesDetails.accountID},
 * {@code PropertiesaccountID.accountID}, {@code accountID}, ...), a guest id
 * under 17, a member id under 8.
 *
 * <p>Because nothing declares which spelling means what,
 * {@code ImportedScenario.ctxGet} resolves them by HEURISTIC at runtime --
 * walking the map for a matching trailing field, flipping leading case, ranking
 * suffixes. That guessing is a known defect source: it is what lets an owner's
 * email resolve where a member's was meant.</p>
 *
 * <p>This class replaces the guess with a DECLARED, ORDERED alias table, derived
 * from the actual corpus rather than invented. Resolution becomes: try each
 * declared alias in order, first non-empty wins.</p>
 *
 * <h2>Deliberately a view, not a replacement</h2>
 *
 * The underlying {@code Map} is kept and shared, NOT copied. That is what makes
 * this adoptable:
 * <ul>
 *   <li>{@code mergedRow}, {@code PlaceholderResolver} and template resolution
 *       still need a string map, and keep getting one.</li>
 *   <li>All 1,111 generated {@code *Support} classes keep compiling and
 *       behaving identically -- this is purely additive.</li>
 *   <li>Writes through this class are visible to code still using the raw map,
 *       and vice versa.</li>
 * </ul>
 *
 * <p>Anything not declared here falls back to {@code ImportedScenario.ctxGet},
 * so a lookup through this class can never resolve WORSE than the raw map --
 * only better, and only where a declaration exists.</p>
 *
 * <h2>Owner and member stay distinct</h2>
 *
 * {@link #guestId()} deliberately excludes {@code memberGuestID}; the member's
 * guest id is {@link #memberGuestId()}. A generic "key contains guestid" match
 * would silently merge them, which is the exact regression the project's
 * do-not-regress list warns about.
 */
public final class ScenarioContext {

    private static final Logger LOG = LoggerFactory.getLogger(ScenarioContext.class);

    /** One logical value and every key it is spelled under, most-used first. */
    public record Field(String name, boolean numeric, List<String> aliases) {
        public Field {
            aliases = List.copyOf(aliases);
        }
    }

    // Alias lists are ORDERED: most-used spelling first, so the common case
    // resolves on the first probe. Extracted from the converted corpus -- add
    // a spelling here rather than widening a pattern, so the mapping stays
    // reviewable.
    public static final Field ACCOUNT_ID = new Field("accountId", true, Arrays.asList(
            "PropertiesDetails.accountID",
            "PropertiesaccountID.accountID",
            "accountID",
            "PropertiesDetails.accountId",
            "Properties.accountID",
            "accountId"));

    public static final Field GUEST_ID = new Field("guestId", true, Arrays.asList(
            "Properties.guestID",
            "PropertiesGuestId.guestId",
            "guestID",
            "Properties.guestId",
            "guestId"));

    /** The MEMBER's guest id -- never folded into {@link #GUEST_ID}. */
    public static final Field MEMBER_GUEST_ID = new Field("memberGuestId", true,
            Arrays.asList(
            "Properties.memberGuestID",
            "Properties.memberGuestId"));

    public static final Field MEMBER_ID = new Field("memberId", true, Arrays.asList(
            "PropertiesaccountID.hilton-member-id",
            "PropertiesDetails.hilton-member-id",
            "hiltonmemberid",
            "employeeProp.hilton-member-id",
            "PropertiesDetails.memberID",
            "memberID",
            "memberId"));

    public static final Field TRAVEL_AGENT_ID = new Field("travelAgentId", true,
            Arrays.asList(
            "Properties.travelAgentID",
            "Properties.travelAgentId",
            "travelAgentID"));

    public static final Field API_TOKEN = new Field("apiToken", false, Arrays.asList(
            "tokenId.GeneratedTokenID",
            // Trailing on purpose: tokenId.GeneratedTokenID still wins.
            // `accessToken` is the key AuthHelper.primeClientCredentialsToken
            // writes and the only one ImportedScenario.begin() preserves across
            // its ctx clear -- without this alias the priming helper and this
            // reader were wired to DIFFERENT keys, so a hand-written chain read
            // "" and sent an empty bearer with no error. The generated clients
            // normalise the prefix (`startsWith("Bearer ") ? token : "Bearer "
            // + token`), so the unprefixed form stored here is safe to return.
            "accessToken"));

    public static final Field SALESFORCE_TOKEN = new Field("salesforceToken", false,
            Arrays.asList(
            "sftokenId.GeneratedTokenID"));

    public static final Field TOTP_CODE = new Field("totpCode", false, Arrays.asList(
            "Properties.totpCode",
            "Properties.totpCodeDB",
            "totpCode"));

    public static final Field WEBSITE_DOMAIN = new Field("websiteDomain", false,
            Arrays.asList(
            "Properties.websiteDomain",
            "websiteDomain",
            "newWebsiteDomain"));

    public static final Field DOMAIN = new Field("domain", false, Arrays.asList(
            "Properties.Domain",
            "Properties.domain",
            "domain"));

    public static final Field EMAIL = new Field("email", false, Arrays.asList(
            "Properties.Email",
            "Properties.email",
            "Properties.EmailAddress",
            "emailAddress"));

    public static final List<Field> FIELDS = List.of(
            ACCOUNT_ID, GUEST_ID, MEMBER_GUEST_ID, MEMBER_ID, TRAVEL_AGENT_ID,
            API_TOKEN, SALESFORCE_TOKEN, TOTP_CODE, WEBSITE_DOMAIN, DOMAIN, EMAIL);

    /** key -> owning field, built once from {@link #FIELDS}. */
    private static final Map<String, Field> BY_KEY = buildKeyIndex();

    private static Map<String, Field> buildKeyIndex() {
        Map<String, Field> m = new java.util.HashMap<>();
        for (Field f : FIELDS) {
            for (String alias : f.aliases()) {
                // First declaration wins: a key listed under two fields would
                // be ambiguous, and silently picking the later one is how an
                // owner id starts answering for a member id.
                m.putIfAbsent(alias, f);
            }
        }
        return Map.copyOf(m);
    }

    /**
     * Declared-alias resolution ONLY -- no heuristic, no legacy fallback.
     *
     * <p>This is the entry point {@code ImportedScenario.ctxGetRaw} calls, so
     * it must never call back into {@code ctxGet}: that would recurse.</p>
     *
     * <p>Returns {@code ""} when {@code primaryKey} belongs to no declared
     * field, or when no alias of its field holds a value. The caller then
     * continues to its own fallback exactly as before.</p>
     *
     * @return the resolved value, or {@code ""} to mean "not my business"
     */
    public static String resolveDeclared(Map<String, String> ctx, String primaryKey) {
        if (ctx == null || primaryKey == null) {
            return "";
        }
        Field f = BY_KEY.get(primaryKey);
        if (f == null) {
            return "";
        }
        for (String alias : f.aliases()) {
            String v = ctx.get(alias);
            if (v != null && !v.isEmpty()) {
                return v;
            }
        }
        return "";
    }

    /** True when this key participates in a declared field. */
    public static boolean isDeclared(String primaryKey) {
        return primaryKey != null && BY_KEY.containsKey(primaryKey);
    }

    /** The field a key belongs to, or null. Exposed for diagnostics/tests. */
    public static Field fieldFor(String primaryKey) {
        return primaryKey == null ? null : BY_KEY.get(primaryKey);
    }

    private final Map<String, String> ctx;

    private ScenarioContext(Map<String, String> ctx) {
        this.ctx = ctx;
    }

    /** Wrap the live ctx map. Not a copy -- writes are visible both ways. */
    public static ScenarioContext of(Map<String, String> ctx) {
        if (ctx == null) {
            throw new IllegalArgumentException("ScenarioContext.of(null)");
        }
        return new ScenarioContext(ctx);
    }

    /** The underlying map, for callers that need a string map (mergedRow, templates). */
    public Map<String, String> asMap() {
        return ctx;
    }

    // =====================================================================
    // Resolution
    // =====================================================================

    /**
     * First non-empty declared alias, else the legacy heuristic lookup.
     *
     * <p>The fallback is what makes adoption safe: an undeclared key still
     * resolves exactly as it does today, so this can only ever improve a
     * lookup, never degrade one.</p>
     */
    public String rawOf(Field field) {
        for (String alias : field.aliases()) {
            String v = ctx.get(alias);
            if (v != null && !v.isEmpty()) {
                return v;
            }
        }
        String legacy = legacyLookup(field.aliases().isEmpty()
                ? field.name() : field.aliases().get(0));
        if (legacy != null && !legacy.isEmpty()) {
            LOG.debug(" .. [ScenarioContext] {} resolved by legacy fallback, not a "
                    + "declared alias -- consider declaring the spelling", field.name());
        }
        return legacy == null ? "" : legacy;
    }

    /** Typed read. Empty when absent, or present but not a number. */
    public Optional<Long> longOf(Field field) {
        if (!field.numeric()) {
            throw new IllegalArgumentException(
                    "field " + field.name() + " is not numeric");
        }
        String raw = rawOf(field);
        if (raw == null || raw.isEmpty()) {
            return Optional.empty();
        }
        try {
            return Optional.of(Long.parseLong(raw.trim()));
        } catch (NumberFormatException e) {
            // An unresolved placeholder (@Properties_accountID@, #x#) reaches
            // here. Empty rather than throwing: the caller decides whether a
            // missing id is fatal, and requireXxx() gives a message that names
            // the actual value instead of a bare NumberFormatException.
            LOG.debug(" .. [ScenarioContext] {} is not numeric: {}", field.name(), raw);
            return Optional.empty();
        }
    }

    /** Typed read that fails with a message naming the field and what was found. */
    public long require(Field field) {
        return longOf(field).orElseThrow(() -> new IllegalStateException(
                "ScenarioContext: " + field.name() + " is not available. Raw value: '"
                + rawOf(field) + "'. Declared aliases tried: " + field.aliases()
                + ". An earlier phase must publish it."));
    }

    public String requireText(Field field) {
        String v = rawOf(field);
        if (v == null || v.isEmpty()) {
            throw new IllegalStateException(
                    "ScenarioContext: " + field.name() + " is empty. Declared aliases "
                    + "tried: " + field.aliases() + ". An earlier phase must publish it.");
        }
        return v;
    }

    // =====================================================================
    // Named accessors -- the readable surface
    // =====================================================================

    public Optional<Long> accountId()      { return longOf(ACCOUNT_ID); }
    public Optional<Long> guestId()        { return longOf(GUEST_ID); }
    public Optional<Long> memberGuestId()  { return longOf(MEMBER_GUEST_ID); }
    public Optional<Long> memberId()       { return longOf(MEMBER_ID); }
    public Optional<Long> travelAgentId()  { return longOf(TRAVEL_AGENT_ID); }

    public long requireAccountId()     { return require(ACCOUNT_ID); }
    public long requireGuestId()       { return require(GUEST_ID); }
    public long requireMemberId()      { return require(MEMBER_ID); }

    public String apiToken()        { return rawOf(API_TOKEN); }
    public String salesforceToken() { return rawOf(SALESFORCE_TOKEN); }
    public String totpCode()        { return rawOf(TOTP_CODE); }
    public String websiteDomain()   { return rawOf(WEBSITE_DOMAIN); }
    public String domain()          { return rawOf(DOMAIN); }
    public String email()           { return rawOf(EMAIL); }

    /** Raw access for the long tail (per-step RawRequest keys and friends). */
    public String raw(String key) {
        return legacyLookup(key);
    }

    // =====================================================================
    // Writes
    // =====================================================================

    /**
     * Write through the canonical (first) alias, and mirror onto any other
     * declared alias already present so code still reading an older spelling
     * sees the update. Delegates to the existing publisher so dual-case
     * mirroring and empty-value semantics are unchanged.
     */
    public ScenarioContext put(Field field, String value) {
        if (value == null || value.isEmpty()) {
            return this;
        }
        String canonical = field.aliases().isEmpty()
                ? field.name() : field.aliases().get(0);
        legacyPut(canonical, value);
        for (String alias : field.aliases()) {
            if (!alias.equals(canonical) && ctx.containsKey(alias)) {
                legacyPut(alias, value);
            }
        }
        return this;
    }

    public ScenarioContext put(Field field, long value) {
        return put(field, Long.toString(value));
    }

    /** Every declared field that currently resolves, for logging / debugging. */
    public Map<String, String> snapshot() {
        Map<String, String> out = new LinkedHashMap<>();
        for (Field f : FIELDS) {
            String v = rawOf(f);
            if (v != null && !v.isEmpty()) {
                out.put(f.name(), v);
            }
        }
        return Collections.unmodifiableMap(out);
    }

    // =====================================================================
    // Bridge to the existing helper.
    //
    // Reflective so this package does not hard-depend on the generated
    // support package: a fresh clone with no conversions still compiles, and
    // a --clean that removes support/ degrades to plain map access rather
    // than failing to build.
    // =====================================================================

    private String legacyLookup(String key) {
        try {
            Class<?> is = Class.forName("com.hi.api.support.ImportedScenario");
            Object v = is.getMethod("ctxGet", Map.class, String.class)
                    .invoke(null, ctx, key);
            return v == null ? "" : v.toString();
        } catch (ReflectiveOperationException | RuntimeException e) {
            String v = ctx.get(key);
            return v == null ? "" : v;
        }
    }

    private void legacyPut(String key, String value) {
        try {
            Class<?> is = Class.forName("com.hi.api.support.ImportedScenario");
            is.getMethod("putExtracted", Map.class, String.class, String.class)
                    .invoke(null, ctx, key, value);
        } catch (ReflectiveOperationException | RuntimeException e) {
            ctx.put(key, value);
        }
    }
}
