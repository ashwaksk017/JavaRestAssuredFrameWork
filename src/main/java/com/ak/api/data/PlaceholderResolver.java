package com.ak.api.data;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Two-pass placeholder resolver for CSV cells and request-body templates.
 *
 * <p><b>Pass 1: faker-style {@code <<X>>} tokens</b>. Each occurrence
 * produces a fresh random value on every call. Supports:
 * <pre>
 *   &lt;&lt;name&gt;&gt;              full personal name (space-separated first/last)
 *   &lt;&lt;firstName&gt;&gt;         first name only
 *   &lt;&lt;lastName&gt;&gt;          last name only
 *   &lt;&lt;username&gt;&gt;          random username (lowercase letters)
 *   &lt;&lt;username(N)&gt;&gt;       random username, exactly N chars
 *   &lt;&lt;email&gt;&gt;             random.local@example.com
 *   &lt;&lt;email(domain)&gt;&gt;     random.local@domain
 *   &lt;&lt;phone&gt;&gt;             10-digit US phone
 *   &lt;&lt;address&gt;&gt;           street address (no city/state)
 *   &lt;&lt;city&gt;&gt;              city name
 *   &lt;&lt;state&gt;&gt;             2-letter US state code
 *   &lt;&lt;zip&gt;&gt;               5-digit US ZIP
 *   &lt;&lt;country&gt;&gt;           2-letter ISO country code
 *   &lt;&lt;company&gt;&gt;           company name
 *   &lt;&lt;uuid&gt;&gt;              lowercase UUID
 *   &lt;&lt;unique&gt;&gt;            monotonic timestamp+random suffix
 *   &lt;&lt;int(min,max)&gt;&gt;      random int in [min,max] inclusive
 *   &lt;&lt;alphanum(N)&gt;&gt;       N random alphanumerics
 *   &lt;&lt;alpha(N)&gt;&gt;          N random letters
 *   &lt;&lt;digits(N)&gt;&gt;         N random digits
 * </pre>
 *
 * <p><b>Pass 2: property-bag {@code ${X}} refs</b>. On first read of
 * an unknown key, the resolver GENERATES a fresh value and stores it
 * in the ctx map so later reads (in the same row/method) see the same
 * value. This matches the reference framework's convention that all
 * occurrences of {@code ${phone}} inside one payload / CSV row are the
 * same phone number, but a rerun of the row yields a different one.
 * Auto-generated keys:
 * <pre>
 *   ${email}         random.local@${email_domain}
 *   ${email_domain}  example.com (or override via env / CSV column)
 *   ${domain}        example.com
 *   ${phone}         10-digit US phone
 *   ${username}      random lowercase username
 *   ${firstName} / ${lastName} / ${name}
 *   ${uuid}          lowercase UUID
 * </pre>
 * Any {@code ${X}} that isn't a well-known key AND isn't already in
 * ctx is left unchanged so the caller can decide whether that's an
 * error (via strict-mode mapJsonValues) or acceptable fallback.
 */
public final class PlaceholderResolver {

    private PlaceholderResolver() {}

    // <<X>> or <<X(args)>>
    private static final Pattern FAKER_TOKEN =
        Pattern.compile("<<([A-Za-z_][A-Za-z0-9_]*)(?:\\(([^)]*)\\))?>>");
    // ${X} -- key may include dots + underscores + digits + dashes + `#`.
    // The `#` accommodates SoapUI-style scoped refs (`${step#field}`,
    // `${Properties#Domain}`) that reach us verbatim from imported test
    // data. Without `#` in the char class the entire token was a literal
    // non-match, so cells like `expected_..._domain=${Properties#Domain}`
    // stayed as raw strings and failed later assertions with confusing
    // "expected `${Properties#Domain}` but was `<real value>`" diffs.
    private static final Pattern DOLLAR_REF =
        Pattern.compile("\\$\\{([A-Za-z_][A-Za-z0-9_.#-]*)\\}");

    /** Run pass 1, pass 2, then pass 3. Safe to call on already-resolved
     *  text (idempotent -- no faker tokens, ${}, #X#, or @X@ refs to match). */
    public static String resolveAll(String text, Map<String, String> ctx) {
        if (text == null || text.isEmpty()) return text;
        String phase1 = resolveFakerTokens(text);
        String phase2 = resolveDollarRefs(phase1, ctx);
        return resolveHashAtRefs(phase2, ctx);
    }

    /**
     * Pass 3: framework-native {@code #Key#} and {@code @Key@} refs.
     * mapJsonValues handles these for body templates at HTTP-call time,
     * but CSV cells that reach OTHER consumers (assertion emit reads
     * {@code row.getOrDefault(col, ...)} verbatim; URL path
     * substitution calls {@code TestSupport.ctxGet}) never went
     * through mapJsonValues -- literal {@code #Properties_Domain#}
     * in an assertion cell would compare against the actual response
     * value and mismatch every time. Bounded at 5 iterations. No-op
     * when text has no {@code #} or {@code @} chars.
     */
    public static String resolveHashAtRefs(String text, Map<String, String> ctx) {
        if (text == null || text.isEmpty()) return text;
        if (text.indexOf('#') < 0 && text.indexOf('@') < 0) return text;
        String cur = text;
        for (int i = 0; i < 5; i++) {
            String next = replaceIfKnown(cur, HASH_REF, ctx);
            next = replaceIfKnown(next, AT_REF, ctx);
            if (next.equals(cur)) return next;
            cur = next;
        }
        return cur;
    }

    /**
     * Iterate matches of {@code pattern} in {@code text}; substitute
     * only when ctx has a NON-EMPTY value for the key. Placeholders
     * whose key isn't in ctx yet are LEFT UNCHANGED so a later
     * consumer (mapJsonValues, ctxGet) can still resolve them once
     * ctx is populated (e.g. Groovy DataGenInput fires after resolveRow).
     * Eagerly replacing with "" wipes the placeholder and permanently
     * breaks later resolution.
     */
    private static String replaceIfKnown(String text, Pattern pattern,
                                         Map<String, String> ctx) {
        Matcher m = pattern.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {
            String rawKey = m.group(1);
            String v = null;
            if (ctx != null) {
                v = ctx.get(rawKey);
                if (v == null || v.isEmpty()) v = ctx.get(rawKey.replace('_', '.'));
                if (v == null || v.isEmpty()) v = ctx.get(rawKey.replace('.', '_'));
            }
            if (v == null || v.isEmpty()) {
                m.appendReplacement(out, Matcher.quoteReplacement(m.group()));
            } else {
                m.appendReplacement(out, Matcher.quoteReplacement(v));
            }
        }
        m.appendTail(out);
        return out.toString();
    }

    private static final Pattern HASH_REF =
            Pattern.compile("#([A-Za-z0-9_.-]+)#");
    private static final Pattern AT_REF =
            Pattern.compile("@([A-Za-z0-9_.-]+)@");

    /**
     * Expand every cell of a CSV row: {@code <<X>>} faker tokens become
     * fresh values, {@code ${X}} property refs consult / populate ctx so
     * the same ref used across multiple cells resolves to the same value
     * WITHIN one row. Returns a fresh LinkedHashMap so retrying a failed
     * row from the original DataProvider array still sees the unresolved
     * template.
     */
    public static java.util.Map<String, String> resolveRow(
            java.util.Map<String, String> row, java.util.Map<String, String> ctx) {
        if (row == null || row.isEmpty()) return row;
        java.util.LinkedHashMap<String, String> out = new java.util.LinkedHashMap<>(row.size());
        for (java.util.Map.Entry<String, String> e : row.entrySet()) {
            out.put(e.getKey(), resolveAll(e.getValue(), ctx));
        }
        return out;
    }

    /** Pass 1: faker-style {@code <<X>>} tokens. Fresh values each call. */
    public static String resolveFakerTokens(String text) {
        if (text == null || text.isEmpty() || text.indexOf('<') < 0) return text;
        Matcher m = FAKER_TOKEN.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {
            String key = m.group(1);
            String args = m.group(2);
            String value = fakerValue(key, args);
            // Null-guard: an unrecognized faker key OR an internal null
            // return would otherwise crash Matcher.quoteReplacement with
            // an NPE. Leave the literal <<X>> in place so the caller
            // (or a later stage) can see what wasn't resolved.
            if (value == null) {
                value = m.group();
            }
            m.appendReplacement(out, Matcher.quoteReplacement(value));
        }
        m.appendTail(out);
        return out.toString();
    }

    /** Pass 2: {@code ${X}} refs. Populates ctx on first use of a
     *  known-key so all occurrences within one row stay consistent. */
    public static String resolveDollarRefs(String text, Map<String, String> ctx) {
        if (text == null || text.isEmpty() || text.indexOf('$') < 0) return text;
        Matcher m = DOLLAR_REF.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {
            String rawKey = m.group(1);
            // SoapUI-style scoped refs (`Properties#Domain`, `step#field`)
            // come through with `#` separators. ctx uses `.` -- try both
            // forms so a value stored under `Properties.Domain` resolves
            // whether the template writes `${Properties#Domain}` or
            // `${Properties.Domain}`.
            String value = null;
            if (ctx != null) {
                value = ctx.get(rawKey);
                if ((value == null || value.isEmpty()) && rawKey.indexOf('#') >= 0) {
                    value = ctx.get(rawKey.replace('#', '.'));
                }
                if ((value == null || value.isEmpty()) && rawKey.indexOf('#') >= 0) {
                    value = ctx.get(rawKey.replace('#', '_'));
                }
            }
            if (value == null || value.isEmpty()) value = autoGenerate(rawKey, ctx);
            if (value == null || value.isEmpty()) {
                // Unresolved OR resolved-to-empty -- leave the literal
                // ${X} in place so it's visible as a marker in the
                // request log rather than silently emitting `""` into
                // the JSON body (which servers reject with "must match
                // regex" and no framework signal).
                m.appendReplacement(out, Matcher.quoteReplacement(m.group()));
            } else {
                m.appendReplacement(out, Matcher.quoteReplacement(value));
            }
        }
        m.appendTail(out);
        return out.toString();
    }

    // =====================================================================
    // Faker dispatch
    // =====================================================================

    private static String fakerValue(String key, String args) {
        String k = key.toLowerCase();
        switch (k) {
            case "name":       return FakeData.fullName();
            case "firstname":  return FakeData.faker().name().firstName();
            case "lastname":   return FakeData.faker().name().lastName();
            case "username":   return truncOrPad(FakeData.username(), parseIntArg(args, -1));
            case "email":      return args == null || args.isEmpty()
                                        ? FakeData.email()
                                        : (FakeData.username() + "@" + args);
            case "phone":      return randomDigits(10);
            case "address":    return FakeData.faker().address().streetAddress();
            case "city":       return FakeData.faker().address().city();
            case "state":      return FakeData.faker().address().stateAbbr();
            case "zip":        return FakeData.faker().address().zipCode().substring(0, 5);
            case "country":    return FakeData.faker().address().countryCode();
            case "company":    return FakeData.companyName();
            case "uuid":       return UUID.randomUUID().toString();
            case "unique":     return Long.toString(System.currentTimeMillis()) + randomAlnum(3);
            case "int":        return Integer.toString(parseIntRange(args));
            case "alphanum":   return randomAlnum(parseIntArg(args, 8));
            case "alpha":      return randomAlpha(parseIntArg(args, 8));
            case "digits":     return randomDigits(parseIntArg(args, 6));
            default:           return "<<" + key + (args == null ? ">>" : "(" + args + ")>>");
        }
    }

    // =====================================================================
    // ${X} dispatch (populates ctx so subsequent ${X} in same row see same value)
    // =====================================================================

    private static String autoGenerate(String key, Map<String, String> ctx) {
        String v;
        String k = key.toLowerCase();
        switch (k) {
            case "email_domain":
            case "domain":       v = "example.com"; break;
            case "email":        v = FakeData.username() + "@"
                                     + getOrDefault(ctx, "email_domain", "example.com"); break;
            case "phone":        v = randomDigits(10); break;
            case "username":     v = FakeData.username(); break;
            case "firstname":    v = FakeData.faker().name().firstName(); break;
            case "lastname":     v = FakeData.faker().name().lastName(); break;
            case "name":         v = FakeData.fullName(); break;
            case "uuid":         v = UUID.randomUUID().toString(); break;
            default:             return null;  // caller leaves the literal alone
        }
        if (ctx != null) ctx.put(key, v);
        return v;
    }

    // =====================================================================
    // Small utilities (kept local so the class has no non-FakeData deps)
    // =====================================================================

    private static String getOrDefault(Map<String, String> ctx, String key, String fallback) {
        if (ctx == null) return fallback;
        String v = ctx.get(key);
        return v == null || v.isEmpty() ? fallback : v;
    }

    private static int parseIntArg(String args, int fallback) {
        if (args == null || args.isEmpty()) return fallback;
        try { return Integer.parseInt(args.trim()); } catch (NumberFormatException e) { return fallback; }
    }

    private static int parseIntRange(String args) {
        if (args == null || !args.contains(",")) {
            return FakeData.intBetween(0, parseIntArg(args, 100));
        }
        String[] parts = args.split(",", 2);
        int lo = parseIntArg(parts[0].trim(), 0);
        int hi = parseIntArg(parts[1].trim(), lo + 100);
        return FakeData.intBetween(Math.min(lo, hi), Math.max(lo, hi));
    }

    private static String randomAlnum(int n) {
        return random(n, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789");
    }

    private static String randomAlpha(int n) {
        return random(n, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ");
    }

    private static String randomDigits(int n) {
        return random(n, "0123456789");
    }

    private static String random(int n, String alphabet) {
        if (n <= 0) n = 1;
        StringBuilder sb = new StringBuilder(n);
        for (int i = 0; i < n; i++) {
            sb.append(alphabet.charAt(ThreadLocalRandom.current().nextInt(alphabet.length())));
        }
        return sb.toString();
    }

    private static String truncOrPad(String s, int n) {
        if (n <= 0) return s;
        if (s.length() == n) return s;
        if (s.length() > n) return s.substring(0, n);
        return s + randomAlnum(n - s.length());
    }
}
