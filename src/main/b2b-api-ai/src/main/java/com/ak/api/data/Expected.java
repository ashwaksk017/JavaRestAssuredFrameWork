// =============================================================================
// Expected -- typed wrapper around the "expected" column of a datasheet row
// -----------------------------------------------------------------------------
// Convention: every datasheet-driven test row can carry a column named
// "expected" whose value packs multiple key/value pairs into ONE string:
//
//   key1:value1;key2:value2;key3:value3
//
// Semicolon separates pairs; the FIRST colon in each pair separates the key
// from the value. That means values may contain colons (e.g. URLs, times)
// safely, but MUST NOT contain semicolons -- the format is deliberately
// stricter on ';' so URLs like "https://..." work without escaping.
//
// Base-class helper `BaseApiTest.expected(row)` returns one of these; tests
// then read expectations with typed getters:
//
//   Expected exp = expected(row);
//   softAssert.assertEquals(res.statusCode(),  exp.getInt("statusCode"), "status");
//   softAssert.assertEquals(created.getTitle(), exp.get("title"),        "title");
//   if (exp.has("errorMessage")) { ... }
//
// Empty / missing "expected" column -> an empty Expected (safe to call, all
// getters return null / false / 0 unless a fallback is provided).
// =============================================================================

package com.ak.api.data;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

public final class Expected {

    private final Map<String, String> map;

    private Expected(Map<String, String> map) {
        this.map = map;
    }

    /** Empty expectations (safe null-object). */
    public static Expected empty() {
        return new Expected(new LinkedHashMap<>());
    }

    /**
     * Parse a "key1:value1;key2:value2" string into an Expected.
     * Null / blank input yields an empty Expected. Whitespace around keys
     * and values is trimmed. Pairs without a colon are silently skipped.
     */
    public static Expected from(String raw) {
        Map<String, String> m = new LinkedHashMap<>();
        if (raw == null || raw.isBlank()) return new Expected(m);
        for (String pair : raw.split(";")) {
            String trimmed = pair.trim();
            if (trimmed.isEmpty()) continue;
            int idx = trimmed.indexOf(':');
            if (idx <= 0) continue; // no ':' or empty key -> skip malformed pair
            String key = trimmed.substring(0, idx).trim();
            String value = trimmed.substring(idx + 1).trim();
            if (!key.isEmpty()) m.put(key, value);
        }
        return new Expected(m);
    }

    // ---- accessors ----

    public boolean has(String key)                   { return map.containsKey(key); }
    public String  get(String key)                   { return map.get(key); }
    public String  get(String key, String fallback)  { return map.getOrDefault(key, fallback); }
    public int     size()                            { return map.size(); }
    public Set<String> keys()                        { return map.keySet(); }
    public Map<String, String> asMap()               { return new LinkedHashMap<>(map); }

    // ---- typed getters (throw on missing or unparseable -- fail fast) ----

    public int getInt(String key) {
        String v = require(key);
        try { return Integer.parseInt(v); }
        catch (NumberFormatException e) {
            throw new IllegalArgumentException(
                    "expected column '" + key + "' is not an int: '" + v + "'", e);
        }
    }

    public int getInt(String key, int fallback) {
        String v = map.get(key);
        if (v == null || v.isBlank()) return fallback;
        try { return Integer.parseInt(v); }
        catch (NumberFormatException e) {
            throw new IllegalArgumentException(
                    "expected column '" + key + "' is not an int: '" + v + "'", e);
        }
    }

    public long getLong(String key) {
        try { return Long.parseLong(require(key)); }
        catch (NumberFormatException e) {
            throw new IllegalArgumentException(
                    "expected column '" + key + "' is not a long: '" + get(key) + "'", e);
        }
    }

    /** Boolean.parseBoolean semantics -- only "true" (case-insensitive) is true. */
    public boolean getBool(String key) { return Boolean.parseBoolean(require(key)); }

    // ---- helpers ----

    private String require(String key) {
        String v = map.get(key);
        if (v == null) {
            throw new IllegalArgumentException(
                    "expected column has no key '" + key + "' (keys present: " + map.keySet() + ")");
        }
        return v;
    }

    @Override
    public String toString() {
        return "Expected" + map;
    }
}
