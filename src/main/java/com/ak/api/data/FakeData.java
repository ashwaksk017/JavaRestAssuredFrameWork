// =============================================================================
// FakeData -- thin wrapper around net.datafaker for common test-data needs
// -----------------------------------------------------------------------------
// Why a wrapper: exposes only what API tests actually reach for, and gives
// tests one obvious import instead of scattered Faker instances. The
// underlying Faker is accessible via faker() for anything custom.
//
// Usage:
//   String title  = FakeData.sentence(6);        // 6-word sentence
//   String email  = FakeData.email();            // realistic email
//   String uuid   = FakeData.uuid();             // random UUID
//   int    userId = FakeData.intBetween(1, 10);
//
//   Map<String,String> row = FakeData.postDataMap();   // ready-to-substitute
//                                                       // into createPost.json
//   Faker f = FakeData.faker();                  // escape hatch for anything
//   String catch_ = f.company().catchPhrase();   //  the wrapper doesn't cover
//
// Seed control (repeatable tests): -Dfake.seed=12345
//   Everything is deterministic when a seed is provided; without it, defaults
//   to a fresh RNG each JVM.
// =============================================================================

package com.ak.api.data;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.UUID;

import com.ak.api.config.Config;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

import net.datafaker.Faker;

public final class FakeData {

    // ThreadLocal Faker: each thread gets its own Faker with its own
    // seeded Random, so `-Dfake.seed=X` produces a stable sequence
    // PER THREAD across reruns. The old single-static Faker was
    // shared across parallel="classes" threads that consumed its
    // internal RNG in non-deterministic order -- reruns of the same
    // seed produced different rows every time.
    //
    // Trade-off: seeded output now depends on which thread runs which
    // class. TestNG's scheduler is deterministic given the same
    // parallel + thread-count config, so a stable environment DOES
    // produce a stable sequence per thread. Cross-config reruns
    // (e.g. thread-count=1 vs thread-count=4) will still differ --
    // that's inherent to any per-thread seeding strategy.
    //
    // A single-threaded run (parallel="none" / -DthreadCount=1) sees
    // one Faker and behaves identically to the old code.
    private static final ThreadLocal<Faker> FAKER_TL =
            ThreadLocal.withInitial(FakeData::buildFaker);
    private static final ObjectMapper JSON = new ObjectMapper();

    private FakeData() { }

    /** Route internal calls through here so they pick up the per-thread Faker. */
    private static Faker f() {
        return FAKER_TL.get();
    }

    /**
     * Return {@code raw} JSON-string-escaped so it can be dropped verbatim
     * between literal "..." in a JSON template. Handles backslashes, quotes,
     * newlines, tabs, and control chars via Jackson so the result is always
     * valid inside a JSON string context.
     */
    public static String jsonEscape(String raw) {
        if (raw == null) return "";
        try {
            String quoted = JSON.writeValueAsString(raw); // wraps in "..." and escapes
            return quoted.substring(1, quoted.length() - 1);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("JSON escape failed: " + e.getMessage(), e);
        }
    }

    // ---- lifecycle ----

    private static Faker buildFaker() {
        String seedStr = Config.get("fake.seed", null);
        if (seedStr != null && !seedStr.isBlank()) {
            long seed = Long.parseLong(seedStr.trim());
            // Fold the thread id into the seed so each thread's Faker
            // consumes its OWN Random in a stable sequence -- shared
            // Random across parallel threads was the reproducibility
            // bug this fix targets.
            long perThreadSeed = seed + Thread.currentThread().getId();
            return new Faker(Locale.ENGLISH, new Random(perThreadSeed));
        }
        return new Faker(Locale.ENGLISH);
    }

    /** Escape hatch -- reach for the raw (per-thread) Faker when the wrapper doesn't cover it. */
    public static Faker faker() {
        return f();
    }

    // ---- strings ----

    /** A well-formed random email like "jane_doe12@example.com". */
    public static String email() {
        return f().internet().emailAddress();
    }

    /** A username-safe token (lowercase alphanumeric + underscores).
     *
     *  Round-12 fix: previously delegated to {@code Datafaker.internet()
     *  .username()} which draws from a BOUNDED corpus (~500 first-names
     *  × ~500 last-names dictionary). Against a long-lived stg tenant
     *  (Hilton) that accumulates prior test users, the corpus overlap
     *  caused 88 x HTTP 409 CONFLICT on {@code MemberHHonorsEnroll} in
     *  a single 30-min run -- names like {@code quiana.schoen} kept
     *  repeating across tests. The SoapUI reference project's
     *  {@code DataGenInput} Groovy uses {@code new Random()} to build
     *  a 4-char lowercase string (26^4 = 457K) plus a tearDown JDBC
     *  cleanup between runs -- we don't have the cleanup so we need
     *  more entropy up front.
     *
     *  Switched to {@code regexify("[a-z]{6}")} for 26^6 ~= 309M
     *  combinations. That's ~700x SoapUI's per-name entropy and enough
     *  to make same-tenant collisions vanishingly rare across the
     *  test-run cadence we expect. Format stays lowercase-alpha so
     *  Hilton stg's username-shape validators keep accepting it.
     */
    public static String username() {
        return f().regexify("[a-z]{6}");
    }

    /** A firstName + lastName. */
    public static String fullName() {
        return f().name().fullName();
    }

    /** A sentence of approximately `wordCount` words. */
    public static String sentence(int wordCount) {
        return f().lorem().sentence(wordCount);
    }

    /** N paragraphs of lorem-style text, joined with blank-line separators. */
    public static String paragraphs(int count) {
        return String.join("\n\n", f().lorem().paragraphs(count));
    }

    /** Company / brand-like name. */
    public static String companyName() {
        return f().company().name();
    }

    /** RFC-4122 v4 UUID. */
    public static String uuid() {
        return UUID.randomUUID().toString();
    }

    // ---- numbers ----

    /** Uniform int in [min, max], both inclusive. */
    public static int intBetween(int min, int max) {
        return f().number().numberBetween(min, max + 1);
    }

    /** Uniform long in [min, max], both inclusive. */
    public static long longBetween(long min, long max) {
        return f().number().numberBetween(min, max + 1);
    }

    /** Uniform double in [min, max) with the given precision (fractional digits). */
    public static double doubleBetween(double min, double max, int fractionDigits) {
        return f().number().randomDouble(fractionDigits, (long) min, (long) max);
    }

    // ---- ready-to-use maps ----

    /**
     * Data map ready to feed RestUtilities.mapJsonValues(..., dataMap) against
     * templates/createPost.json (keys: title, body, userId, published).
     */
    public static Map<String, String> postDataMap() {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("title", sentence(6));
        row.put("body", paragraphs(2));
        row.put("userId", String.valueOf(intBetween(1, 10)));
        row.put("published", String.valueOf(f().bool().bool()));
        return row;
    }

    /**
     * Data map for a signup / user-creation payload (username, email, password).
     * Password is 12 chars mixing lower + upper + digits.
     */
    public static Map<String, String> signupDataMap() {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("username", username());
        row.put("email", email());
        row.put("password", f().internet().password(12, 12, true, false, true));
        return row;
    }
}
