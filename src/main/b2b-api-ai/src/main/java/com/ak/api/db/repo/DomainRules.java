package com.ak.api.db.repo;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ThreadLocalRandom;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.ak.api.config.Config;

/**
 * Live values for the {@code Properties.RandomDomain} / {@code RandomDomain2}
 * placeholders, sourced from {@code account_rules} instead of the frozen
 * snapshot baked into the generated CSVs.
 *
 * <p>WHY THIS EXISTS. In ReadyAPI these two properties were never static. A
 * JDBC {@code eachRow} step read {@code account_rules} for a reason
 * ({@code managed_domain} / {@code competitor_domain} / {@code freemail_domain})
 * and the following Groovy picked one at random:</p>
 *
 * <pre>randomValue = values ? values[new Random().nextInt(values.size())] : null
 * testRunner.testCase.setPropertyValue("RandomDomain", randomValue)</pre>
 *
 * <p>The converter could not translate that Groovy, so it froze whatever
 * domain the last ReadyAPI run happened to pick into the CSV. A domain that
 * was blacklisted at capture time is not necessarily blacklisted today, which
 * is why {@code B2B-6324_Verify_with_managed_domain_400} now returns 200: the
 * suite sends a domain the backend no longer refuses, and a test whose whole
 * purpose is to prove rejection passes the wrong way.</p>
 *
 * <p>NOTE the direction. These reasons are all {@code account_blacklist} rows
 * -- domains the backend REFUSES. They are the input to negative tests. They
 * are NOT the {@code ALLOWED_DOMAINS} allowlist that
 * {@link com.ak.api.support.CtxFields} uses to build valid identity; priming
 * that from these rows would make every happy-path test 400.</p>
 *
 * <p>OFF BY DEFAULT. Switching a test from a frozen domain to a live one
 * changes what the request sends, and any CSV-frozen expected value that
 * names the old domain would then mismatch. Turn it on deliberately and
 * measure:</p>
 *
 * <pre>domains.randomDomain.fromDb.enabled=true</pre>
 *
 * <p>FAIL-SOFT throughout. A disabled switch, an unresolvable reason, an
 * unreachable database or an empty result all fall back to the CSV value, so
 * this can never leave a row with no domain at all.</p>
 */
public final class DomainRules {

    private static final Logger LOG = LoggerFactory.getLogger(DomainRules.class);

    /** Master switch. OFF by default -- see the class javadoc. */
    public static final String ENABLED_KEY = "domains.randomDomain.fromDb.enabled";

    /**
     * Optional CSV column naming the reason outright. Takes precedence over
     * inference, so a case whose name does not advertise its intent can still
     * opt in without any code change.
     */
    public static final String REASON_COLUMN = "randomDomain.reason";

    /**
     * One fetch per reason per JVM. The ReadyAPI Groovy re-queried per test,
     * but the rows change far more slowly than a suite runs, and re-querying
     * per row would put thousands of round trips on the critical path.
     */
    private static final Map<String, List<String>> CACHE = new ConcurrentHashMap<>();

    private DomainRules() {
    }

    public static boolean isEnabled() {
        return Config.getBool(ENABLED_KEY, false);
    }

    /** Drops the per-reason cache. Test seam only. */
    public static void resetForTest() {
        CACHE.clear();
    }

    /**
     * The {@code account_rules.reason} this row wants, or null when it cannot
     * be determined -- in which case the caller keeps the CSV value.
     *
     * <p>An explicit {@link #REASON_COLUMN} wins. Otherwise the case id and
     * description are matched against the three reasons whose names the
     * corpus states unambiguously. Deliberately conservative: {@code blocklist}
     * and {@code freelist} appear in case names too, but they do not map onto
     * a single reason with confidence, so they fall through to the CSV rather
     * than guess.</p>
     */
    public static String reasonForRow(Map<String, String> row) {
        if (row == null) {
            return null;
        }
        String explicit = row.get(REASON_COLUMN);
        if (explicit != null && !explicit.trim().isEmpty()) {
            return explicit.trim();
        }
        String hay = (nullToEmpty(row.get("test_case_id")) + " "
                + nullToEmpty(row.get("description"))).toLowerCase(Locale.ROOT);
        if (hay.contains("managed_domain") || hay.contains("managed domain")
                || hay.contains("manageddomain")) {
            return "managed_domain";
        }
        if (hay.contains("competitor")) {
            return "competitor_domain";
        }
        if (hay.contains("freemail")) {
            return "freemail_domain";
        }
        return null;
    }

    /** Live domains for a reason, empty when unavailable. Cached per JVM. */
    public static List<String> domainsFor(String reason) {
        if (reason == null || reason.trim().isEmpty()) {
            return List.of();
        }
        return CACHE.computeIfAbsent(reason.trim(), r -> {
            List<String> live = AccountRulesRepository.domainsByReason(r);
            LOG.info("DomainRules: {} live domain(s) for reason={}", live.size(), r);
            return new ArrayList<>(live);
        });
    }

    /**
     * Live replacements for this row's two domain placeholders, falling back
     * to the CSV values whenever a live one is unavailable.
     *
     * <p>Both are resolved in ONE call so the pair can be kept distinct --
     * several cases add one domain and then verify against the other, and two
     * independent random picks would collide on a short list.</p>
     *
     * @return always a two-element array: {@code [RandomDomain, RandomDomain2]}
     */
    public static String[] overrideOrCsv(Map<String, String> row,
                                         String csv1, String csv2) {
        String[] out = {csv1, csv2};
        if (!isEnabled()) {
            return out;
        }
        String reason = reasonForRow(row);
        if (reason == null) {
            return out;
        }
        List<String> live = domainsFor(reason);
        if (live.isEmpty()) {
            return out;
        }
        int i = ThreadLocalRandom.current().nextInt(live.size());
        out[0] = live.get(i);
        if (live.size() > 1) {
            // Uniform over the remaining entries: pick in [0, n-1) and skip
            // over i, which is cheaper and less biased than retrying.
            int j = ThreadLocalRandom.current().nextInt(live.size() - 1);
            if (j >= i) {
                j++;
            }
            out[1] = live.get(j);
        } else {
            out[1] = live.get(i);
        }
        LOG.info("DomainRules: RandomDomain/RandomDomain2 sourced live for reason={} "
                + "({} candidate(s))", reason, live.size());
        return out;
    }

    private static String nullToEmpty(String s) {
        return s == null ? "" : s;
    }
}
