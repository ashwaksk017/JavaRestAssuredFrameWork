package com.ak.api.support;

// ra_converter-framework-rev: 2
// Bumped whenever this bundled file changes. The converter
// SKIPS author-editable files that already exist, so without a
// revision it cannot tell an author's edit from a copy left by
// an older converter -- and an in-method change (no new symbol)
// would silently never reach existing trees.

import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import com.ak.api.config.Config;
import com.ak.api.db.Db;

/**
 * Harvests this-test-only identity from an imported test's {@code ctx}
 * so {@code SuiteCleanup.afterEachTest} can delete the rows that case
 * created, instead of wiping every account under {@code WEBSITE_DOMAIN}.
 *
 * <p>Account ids: values on keys whose last segment is {@code accountId}
 * / {@code accountID} / {@code account_id}. The DataGenInput faker writes
 * 9-digit placeholders under {@code Properties.accountId} <em>before</em>
 * any REST create; those are skipped so we do not DELETE a coincidental
 * real row. Extracted ids (typically 10+ digits on
 * {@code PropertiesDetails.accountID} / {@code PropertiesaccountID.accountID})
 * are kept.</p>
 *
 * <p>Website domains: {@code websiteDomain} keys whose value is unique to
 * this test. Shared fixtures ({@code Hardcodeddomain}, {@code WEBSITE_DOMAIN},
 * {@code ALLOWED_DOMAIN}) are skipped -- deleting those would be the same
 * blind sweep as {@code beforeEachTest} and would race sibling classes
 * under {@code parallel="classes"}.</p>
 */
public final class ImportedTestdataCleanup {

    private static final Pattern ACCOUNT_ID_FIELD =
            Pattern.compile("(?i)^account[_-]?id$");
    private static final Pattern WEBSITE_FIELD =
            Pattern.compile("(?i)^website[_-]?domain$");
    private static final Pattern NUMERIC_ID =
            Pattern.compile("\\d{8,20}");
    private static final Pattern HOSTNAME =
            Pattern.compile("(?i)^[a-z0-9][a-z0-9.-]*\\.[a-z]{2,}$");

    private ImportedTestdataCleanup() {}

    /**
     * Comma-separated numeric {@code account_id} literals for a SQL
     * {@code IN (...)} list, or {@code ""} when none were extracted.
     */
    public static String numericAccountIdInList(Map<String, String> ctx) {
        return Db.numericCsvInList(accountIdsFrom(ctx));
    }

    /**
     * Quoted CSV of website domains this test uniquely used, or {@code ""}.
     */
    public static String quotedUniqueWebsiteInList(Map<String, String> ctx) {
        Set<String> domains = uniqueWebsiteDomainsFrom(ctx);
        if (domains.isEmpty()) {
            return "";
        }
        return Db.quotedCsvInList(String.join(",", domains));
    }

    public static Set<String> accountIdsFrom(Map<String, String> ctx) {
        Set<String> ids = new LinkedHashSet<>();
        if (ctx == null) {
            return ids;
        }
        for (Map.Entry<String, String> e : ctx.entrySet()) {
            String key = e.getKey();
            String val = e.getValue();
            if (key == null || val == null) {
                continue;
            }
            String field = lastSegment(key);
            if (!ACCOUNT_ID_FIELD.matcher(field).matches()) {
                continue;
            }
            String trimmed = val.trim();
            if (!NUMERIC_ID.matcher(trimmed).matches()) {
                continue;
            }
            String ns = namespace(key);
            // Faker default: Properties.accountId = 9 digits. Real Hilton
            // program-account ids extracted from create responses are
            // longer and live on PropertiesDetails / PropertiesaccountID.
            if ("properties".equalsIgnoreCase(ns) && trimmed.length() == 9) {
                continue;
            }
            ids.add(trimmed);
        }
        return ids;
    }

    public static Set<String> uniqueWebsiteDomainsFrom(Map<String, String> ctx) {
        Set<String> domains = new LinkedHashSet<>();
        if (ctx == null) {
            return domains;
        }
        Set<String> shared = sharedDomains(ctx);
        for (Map.Entry<String, String> e : ctx.entrySet()) {
            String key = e.getKey();
            String val = e.getValue();
            if (key == null || val == null) {
                continue;
            }
            if (key.toLowerCase().contains("hardcoded")) {
                continue;
            }
            String field = lastSegment(key);
            if (!WEBSITE_FIELD.matcher(field).matches()) {
                continue;
            }
            String trimmed = val.trim();
            if (trimmed.isEmpty() || !HOSTNAME.matcher(trimmed).matches()) {
                continue;
            }
            if (shared.contains(trimmed.toLowerCase())) {
                continue;
            }
            domains.add(trimmed);
        }
        return domains;
    }

    private static Set<String> sharedDomains(Map<String, String> ctx) {
        Set<String> shared = new LinkedHashSet<>();
        addCsv(shared, Config.get("WEBSITE_DOMAIN", ""));
        addCsv(shared, Config.get("ALLOWED_DOMAIN", ""));
        addCsv(shared, Config.get("ALLOWED_DOMAINS", ""));
        if (ctx != null) {
            addCsv(shared, ctx.get("Properties.Hardcodeddomain"));
            addCsv(shared, ctx.get("Properties.hardcodeddomain"));
            addCsv(shared, ctx.get("Properties.HardcodedDomain"));
        }
        return shared;
    }

    private static void addCsv(Set<String> into, String csv) {
        if (csv == null || csv.isBlank()) {
            return;
        }
        for (String part : csv.split(",")) {
            String trimmed = part.trim();
            if (!trimmed.isEmpty()) {
                into.add(trimmed.toLowerCase());
            }
        }
    }

    private static String lastSegment(String key) {
        int dot = key.lastIndexOf('.');
        return dot < 0 ? key : key.substring(dot + 1);
    }

    private static String namespace(String key) {
        int dot = key.lastIndexOf('.');
        return dot < 0 ? "" : key.substring(0, dot);
    }
}
