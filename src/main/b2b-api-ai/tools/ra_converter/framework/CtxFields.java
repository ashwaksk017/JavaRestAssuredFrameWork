package com.ak.api.support;

// ra_converter-framework-rev: 6
// Bumped whenever this bundled file changes. The converter
// SKIPS author-editable files that already exist, so without a
// revision it cannot tell an author's edit from a copy left by
// an older converter -- and an in-method change (no new symbol)
// would silently never reach existing trees.

import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

import com.ak.api.config.Config;
import com.ak.api.data.FakeData;

/**
 * Dual-case ctx writes and name-shape identity generation for imported
 * ReadyAPI tests.
 *
 * <p>Replaces the 80-line {@code DataGenInput} Groovy translation that
 * currently sits at the top of every generated {@code @Test} method.
 * Shape rules match {@code groovy_translator.py} (~1157–1174):</p>
 * <ul>
 *   <li>phone / hhonorsNumber → 9-digit numeric</li>
 *   <li>names containing {@code guestid}, {@code memberid}, or
 *       {@code accountid} → 9-digit numeric ({@code customerId} /
 *       {@code buyerId} are <em>not</em> in that set -- they stay
 *       usernames, same as the converter)</li>
 *   <li>domain / websiteDomain / weburl → a domain from {@code ALLOWED_DOMAINS}
 *       (see {@link #allowedDomainOrNull()}), else {@code word.com}</li>
 *   <li>{@code *email*} → {@code word@<that domain>}</li>
 *   <li>everything else → username (6 lowercase letters)</li>
 * </ul>
 *
 * <p>Each field is written under both <em>first-letter</em> casings
 * ({@code Properties.Email} and {@code Properties.email}) so
 * {@code #Properties_Username#} and {@code #Properties_username#}
 * resolve to the same value. Trailing {@code Id}/{@code ID} is a
 * different key ({@code guestId} vs {@code guestID}); the converter
 * generates both, and enroll uses {@code #Properties_guestID#}.</p>
 *
 * <p><b>Before</b> (B2B-9098 enroll setup, ~80 lines of {@code FakeData}
 * + dual {@code ctx.put}):</p>
 * <pre>
 * String genV_Email = FakeData.username() + "@" + Config.get("ALLOWED_DOMAIN", "example.com");
 * ctx.put("Properties.Email", genV_Email);
 * ctx.put("Properties.email", genV_Email);
 * // ... 30 more fields ...
 * TestSupport.putIfNonEmpty(ctx, "Properties.Domain", TestSupport.testData(row, "Properties.Domain"));
 * TestSupport.putIfNonEmpty(ctx, "Properties.Domain", "qegwtbxd.com");
 * </pre>
 *
 * <p><b>After:</b></p>
 * <pre>
 * CtxFields.generateStandard(ctx, "Properties", CtxFields.B2B9098_EXTRA_FIELDS);
 * CtxFields.seedFromRow(ctx, row, "Properties.");
 * </pre>
 *
 * <p>{@link #generateStandard(Map, String)} alone is the converter ALWAYS
 * list -- not enough for B2B-9098 (missing {@code name}, {@code Firstname},
 * {@code Hardcodeddomain} is a seed key, not a generated one, etc.).</p>
 *
 * <p>Does <em>not</em> replace {@link ImportedScenario#regenRandomProperties}:
 * that still runs immediately before each REST step that submits fresh
 * identity data, and it deliberately leaves extracted IDs alone.</p>
 */
public final class CtxFields {

    /**
     * Converter ALWAYS list from groovy_translator.py -- fields populated
     * even when the SoapUI DataGenInput script did not set them, so
     * templates that reference a variant never see a stale CSV value.
     */
    public static final String[] STANDARD_FIELDS = {
            "Username", "usernamemember", "usernameM",
            "Email", "EmailMember", "guestMemberEmail",
            "Phone", "phoneNumber", "hhonorsNumber",
            "Domain", "websiteDomain",
            "generatedemailAddress", "generatedEmail",
            "guestId", "guestID", "memberGuestID",
            "accountId", "accountID",
            "memberId", "memberID",
            "partnerAccountId", "partnerAccountID"
    };

    /**
     * B2B-9098 DataGenInput {@code setPropertyValue} targets that are
     * <em>not</em> in {@link #STANDARD_FIELDS}. Pass these as extras to
     * {@link #generateStandard(Map, String, String...)} so {@code name},
     * {@code websitedomain} (distinct from {@code websiteDomain}),
     * {@code Firstname}/{@code Lastname}, {@code customerId}/{@code buyerId},
     * and {@code uuid3}/{@code uuid4} exist. {@code Hardcodeddomain} is
     * seeded, not generated -- {@link #seedFromRow} picks it up from
     * {@link ImportedScenario#testData}.
     */
    public static final String[] B2B9098_EXTRA_FIELDS = {
            "name", "websitedomain",
            "Firstname", "Lastname",
            "customerId", "buyerId",
            "uuid3", "uuid4"
    };

    private CtxFields() {}

    /**
     * Write {@code namespace.field} and the first-letter-flipped sibling
     * ({@code Properties.Email} + {@code Properties.email}) to the same
     * value. Overwrites existing keys (DataGenInput semantics).
     */
    public static void putBothCases(Map<String, String> ctx, String namespace,
                                    String field, String value) {
        if (ctx == null || field == null || field.isEmpty()) return;
        String ns = (namespace == null || namespace.isEmpty()) ? "" : namespace + ".";
        ctx.put(ns + field, value);
        String flipped = flipFirst(field);
        if (!flipped.equals(field)) {
            ctx.put(ns + flipped, value);
        }
    }

    /**
     * Write a dotted key ({@code Properties.Email}) plus its first-letter
     * field-casing sibling. If {@code namespacedField} has no dot, the
     * whole string is treated as the field under no namespace.
     */
    public static void putBothCases(Map<String, String> ctx, String namespacedField,
                                    String value) {
        if (ctx == null || namespacedField == null || namespacedField.isEmpty()) return;
        int dot = namespacedField.lastIndexOf('.');
        if (dot < 0) {
            putBothCases(ctx, "", namespacedField, value);
            return;
        }
        putBothCases(ctx, namespacedField.substring(0, dot),
                namespacedField.substring(dot + 1), value);
    }

    /**
     * Generate a unique name-shape-appropriate value for each field and
     * write both casings into {@code ctx} under {@code namespace}.
     * Duplicate field names that differ only by <em>first-letter</em>
     * case ({@code Email}/{@code email}) are collapsed so the last
     * casing does not clobber the first with a second random value.
     * Trailing {@code Id}/{@code ID} is not a first-letter pair
     * ({@code guestId} vs {@code guestID}) and both are generated --
     * enroll query params use {@code #Properties_guestID#}.
     */
    public static void generate(Map<String, String> ctx, String namespace,
                                String... fields) {
        if (ctx == null || fields == null || fields.length == 0) return;
        // One website/email domain for the whole pack so owner enroll,
        // member enroll, and create-account websiteDomain stay coherent.
        // Standalone valueFor("Email") still uses ALLOWED_DOMAIN.
        String sharedDomain = null;
        boolean needsDomain = false;
        for (String field : fields) {
            if (field == null || field.isEmpty()) continue;
            String p = field.toLowerCase();
            if (isDomainField(field) || isWebsiteField(field) || p.contains("email")) {
                needsDomain = true;
                break;
            }
        }
        if (needsDomain) {
            sharedDomain = allowedDomainOrRandom();
        }
        Set<String> seenFirstLetterPair = new LinkedHashSet<>();
        for (String field : fields) {
            if (field == null || field.isEmpty()) continue;
            if (!seenFirstLetterPair.add(field)) continue;
            String flipped = flipFirst(field);
            if (!flipped.equals(field)) seenFirstLetterPair.add(flipped);
            String p = field.toLowerCase();
            String value;
            if (sharedDomain != null && isDomainField(field)) {
                value = sharedDomain;
            } else if (sharedDomain != null && isWebsiteField(field)) {
                value = "www." + sharedDomain;
            } else if (sharedDomain != null && p.contains("email")) {
                value = FakeData.username() + "@" + sharedDomain;
            } else {
                value = valueFor(field);
            }
            putBothCases(ctx, namespace, field, value);
        }
    }

    /**
     * Populate the converter ALWAYS set under {@code namespace}
     * (typically {@code "Properties"} or {@code "Properties_2"}).
     * Does <em>not</em> include script-only fields such as {@code name}
     * or {@code Firstname} -- pass those as {@code extraFields} or use
     * {@link #B2B9098_EXTRA_FIELDS}.
     */
    public static void generateStandard(Map<String, String> ctx, String namespace) {
        generate(ctx, namespace, STANDARD_FIELDS);
    }

    /**
     * Script extras first (SoapUI {@code setPropertyValue} targets), then
     * the ALWAYS list -- same order as groovy_translator. First-letter
     * pairs that appear in both lists get one value; {@code guestId} and
     * {@code guestID} both get values.
     */
    public static void generateStandard(Map<String, String> ctx, String namespace,
                                        String... extraFields) {
        if (extraFields == null || extraFields.length == 0) {
            generateStandard(ctx, namespace);
            return;
        }
        String[] all = new String[extraFields.length + STANDARD_FIELDS.length];
        System.arraycopy(extraFields, 0, all, 0, extraFields.length);
        System.arraycopy(STANDARD_FIELDS, 0, all, extraFields.length, STANDARD_FIELDS.length);
        generate(ctx, namespace, all);
    }

    /**
     * Copy Properties-step values into ctx via
     * {@link ImportedScenario#putIfNonEmpty} + {@link ImportedScenario#testData}
     * (putIfAbsent: generated values win; CSV / {@code test_data.*} /
     * bundled JSON defaults fill keys DataGen did not write).
     *
     * <p>SoapUI XML literals are <em>not</em> passed from the test
     * method -- they already live in the suite
     * {@code test_data_defaults/*.json} as
     * {@link ImportedScenario#testData}'s last fallback. Emitting
     * {@code putIfNonEmpty(ctx, key, xmlLiteral)} after this call was
     * a no-op whenever the JSON had the key.</p>
     *
     * <p>When {@code fields} is non-empty, only those names are seeded
     * ({@code keyPrefix + field}, or the name as-is when it already
     * starts with the prefix). When {@code fields} is omitted, walks
     * CSV keys and bundled JSON defaults with {@code keyPrefix} --
     * this is what generated tests emit so SoapUI literals populate
     * ctx without appearing in the test class.</p>
     */
    public static void seedFromRow(Map<String, String> ctx, Map<String, String> row,
                                   String keyPrefix, String... fields) {
        if (ctx == null || keyPrefix == null) return;
        if (fields != null && fields.length > 0) {
            for (String f : fields) {
                if (f == null || f.isEmpty()) continue;
                String key = f.startsWith(keyPrefix) ? f : keyPrefix + f;
                if (isCapturedSalesforceSessionKey(key)) continue;
                ImportedScenario.putIfNonEmpty(ctx, key, ImportedScenario.testData(row, key));
            }
            return;
        }
        Set<String> keys = new LinkedHashSet<>();
        if (row != null) {
            for (String key : row.keySet()) {
                if (key != null && key.startsWith(keyPrefix)) keys.add(key);
            }
        }
        for (String key : ImportedScenario.testDataDefaultKeys()) {
            if (key != null && key.startsWith(keyPrefix)) keys.add(key);
        }
        for (String key : keys) {
            if (isCapturedSalesforceSessionKey(key)) continue;
            ImportedScenario.putIfNonEmpty(ctx, key, ImportedScenario.testData(row, key));
        }
        // Underscore-separated CSV columns for the same prefix.
        //
        // The converter emits CSV columns for `${Step#field}` references
        // through two paths. The frozen-Properties path names them with a
        // DOT (`Properties.Domain`); the placeholder classifier names them
        // with an UNDERSCORE (`Properties_topicenv`). ctx keys and every
        // `ctxGet` call use the dot form, so an underscore column was
        // written to the CSV and then never seeded -- `ctxGet(ctx,
        // "Properties.topicenv")` returned "" and the Kafka partition URL
        // lost its topic segment (`/topics//partitions/3`).
        //
        // Seed those under the DOTTED ctx key. putIfNonEmpty means a real
        // dot column already seeded above always wins.
        if (row != null && keyPrefix.endsWith(".")) {
            String underscorePrefix =
                    keyPrefix.substring(0, keyPrefix.length() - 1) + "_";
            for (String key : row.keySet()) {
                if (key == null || !key.startsWith(underscorePrefix)) continue;
                String dotted =
                        keyPrefix + key.substring(underscorePrefix.length());
                if (isCapturedSalesforceSessionKey(dotted)) continue;
                ImportedScenario.putIfNonEmpty(
                        ctx, dotted, ImportedScenario.testData(row, key));
            }
        }
    }

    /**
     * ReadyAPI {@code sftokenId.GeneratedTokenID} XML captures a session
     * from the last local run. Groovy {@code sf-Token} overwrites it after
     * {@code sf-token-Request}. Seeding that captured Bearer would mask a
     * failed token extract with {@code INVALID_SESSION_ID}.
     */
    public static boolean isCapturedSalesforceSessionKey(String key) {
        if (key == null || key.isEmpty()) {
            return false;
        }
        String n = key.toLowerCase().replace("_", "").replace("-", "");
        return n.contains("sftokenid") && n.endsWith("generatedtokenid");
    }

    /** US state codes, for the {@code state} name shape. */
    private static final String[] US_STATES = {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
        "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
        "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
        "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
        "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    };

    /** Name-shape generator matching groovy_translator._generator_expr. */
    public static String valueFor(String field) {
        if (field == null || field.isEmpty()) return FakeData.username();
        String p = field.toLowerCase();
        if (p.contains("phone") || p.equals("hhonorsnumber")) {
            return FakeData.faker().numerify("#########");
        }
        if (p.contains("guestid") || p.contains("memberid") || p.contains("accountid")) {
            return FakeData.numericId();
        }
        // Domain-shaped names first: "emailDomain" contains "email" and was
        // generated as an ADDRESS, which the API rejected as a constraint
        // violation on emailDomains (13 rows in one regression digest).
        // ReadyAPI's DataGenInput builds emailDomain, RandomDomain, Domain2..9
        // and Website from the same label; "Website" is "www." + that domain.
        if (isDomainField(field)) {
            return allowedDomainOrRandom();
        }
        if (isWebsiteField(field)) {
            return "www." + allowedDomainOrRandom();
        }
        if (p.contains("email")) {
            String allowed = allowedDomainOrNull();
            return FakeData.username() + "@"
                    + (allowed != null ? allowed : Config.get("ALLOWED_DOMAIN", "example.com"));
        }
        // Address fields the API validates by shape. ReadyAPI's DataGenInput
        // builds them with its own generators -- getRandomPostalCode() is
        // (10000 + nextInt(89999)), city is "City_" + nextInt(100),
        // addressLine1 is "Address_" + nextInt(1000) + "Blvd", country is the
        // literal "US" -- and their values reach contactInfo.address in 9
        // templates. A word-shaped fallback sent postalCode "hkdmsz".
        // seedFromRow cannot repair it: putIfAbsent means the generated value
        // wins over the CSV value and the bundled default.
        // The suite names numbered variants of every address field --
        // postalCode2, state2, city2, country2, addressLine1_2 -- so the
        // shape test has to ignore a trailing ordinal. Matching only the
        // bare name sent postalCode2 a word.
        String bare = p.replaceAll("[_0-9]+$", "");
        if (bare.equals("postalcode") || bare.endsWith("postalcode")) {
            return String.valueOf(FakeData.intBetween(10000, 99999));
        }
        if (bare.equals("state") || bare.endsWith("state")) {
            return FakeData.oneOf(US_STATES);
        }
        if (bare.equals("city") || bare.endsWith("city")) {
            return "City_" + FakeData.intBetween(0, 99);
        }
        if (p.contains("addressline")) {
            return "Address_" + FakeData.intBetween(0, 999) + "Blvd";
        }
        if (bare.equals("country") || bare.endsWith("country")) {
            return "US";
        }
        return FakeData.username();
    }

    private static final java.util.concurrent.atomic.AtomicBoolean WARNED_NO_ALLOWED_DOMAINS =
            new java.util.concurrent.atomic.AtomicBoolean(false);

    /**
     * A domain from the ReadyAPI project property {@code ALLOWED_DOMAINS},
     * or null when it is not configured.
     *
     * <p>ReadyAPI's DataGenInput builds the account's emailDomains, the
     * websiteDomain and every owner/member email on this one domain. The
     * translated DataGenInput read the property into ctx and never used it,
     * so identity fell back to a random {@code word.com} the target's domain
     * allowlist does not know -- CreatePendingAccountmember then 400s.</p>
     */
    public static String allowedDomainOrNull() {
        String picked = pickAllowedDomain(Config.get("ALLOWED_DOMAINS", ""),
                java.util.concurrent.ThreadLocalRandom.current());
        if (picked == null && WARNED_NO_ALLOWED_DOMAINS.compareAndSet(false, true)) {
            org.slf4j.LoggerFactory.getLogger(CtxFields.class).warn(
                    "ALLOWED_DOMAINS is not configured -- generated identity falls back to"
                    + " random word.com domains, which the target's domain allowlist"
                    + " rejects. Copy the ReadyAPI project property ALLOWED_DOMAINS into"
                    + " program_configuration.json, or pass -DALLOWED_DOMAINS=a.com,b.com.");
        }
        return picked;
    }

    /** {@link #allowedDomainOrNull()}, else a random {@code word.com}. */
    public static String allowedDomainOrRandom() {
        String allowed = allowedDomainOrNull();
        return allowed != null ? allowed : FakeData.username() + ".com";
    }

    /**
     * Pick from a comma-separated domain list the way the ReadyAPI Groovy
     * does: {@code allowedDomains[new Random().nextInt(allowedDomains.length-1)]}.
     * With more than one entry the LAST is never chosen -- kept for parity,
     * since the suite was only ever proven against the domains ReadyAPI picks.
     * A single entry is returned as-is (the Groovy would throw there).
     */
    public static String pickAllowedDomain(String csv, java.util.Random rnd) {
        if (csv == null || csv.trim().isEmpty()) return null;
        java.util.List<String> domains = new java.util.ArrayList<>();
        for (String raw : csv.split(",")) {
            String d = raw.trim().toLowerCase(java.util.Locale.ROOT);
            if (d.startsWith("www.")) d = d.substring(4);
            if (!d.isEmpty()) domains.add(d);
        }
        if (domains.isEmpty()) return null;
        if (domains.size() == 1) return domains.get(0);
        return domains.get(rnd.nextInt(domains.size() - 1));
    }

    static boolean isDomainField(String field) {
        if (field == null) return false;
        String p = stripOrdinal(field.toLowerCase());
        // Anything named *domain (Domain, Domain2, emailDomain, RandomDomain,
        // Hardcodeddomain, websiteDomain, newWebsiteDomain) holds a domain.
        return p.equals("weburl") || p.endsWith("domain");
    }

    /** {@code Website}, {@code Website2}: ReadyAPI stores "www." + domain. */
    static boolean isWebsiteField(String field) {
        if (field == null) return false;
        return stripOrdinal(field.toLowerCase()).equals("website");
    }

    /** {@code domain2} -> {@code domain}, {@code website_3} -> {@code website}. */
    private static String stripOrdinal(String p) {
        int end = p.length();
        while (end > 0 && (Character.isDigit(p.charAt(end - 1)) || p.charAt(end - 1) == '_')) {
            end--;
        }
        return p.substring(0, end);
    }

    /** Flip the first character's case; rest of the string unchanged. */
    static String flipFirst(String field) {
        if (field == null || field.isEmpty()) return field;
        char c = field.charAt(0);
        char alt = Character.isUpperCase(c)
                ? Character.toLowerCase(c)
                : Character.toUpperCase(c);
        return alt + field.substring(1);
    }
}
