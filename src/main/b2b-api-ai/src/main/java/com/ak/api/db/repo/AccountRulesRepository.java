package com.ak.api.db.repo;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.ak.api.config.Config;
import com.ak.api.db.Db;

/**
 * {@code account_rules} access.
 *
 * <p>Writes: the LTA travel-agent whitelist used by translated Groovy.
 * Reads: the managed-domain allowlist, which used to be a static
 * {@code ALLOWED_DOMAINS} string in config and can grow over time.</p>
 */
public final class AccountRulesRepository {

    private static final Logger LOG = LoggerFactory.getLogger(AccountRulesRepository.class);

    private AccountRulesRepository() {}

    /** Column holding the domain value; {@code segment.account_rules} uses the same name. */
    private static final String VALUE_COLUMN = "value";

    /**
     * Domains the backend currently treats as managed:
     * {@code SELECT value FROM account_rules WHERE reason = ?}.
     *
     * <p>Replaces the static {@code ALLOWED_DOMAINS} config string, which had
     * to be edited by hand whenever the backend list grew. The suite already
     * runs this exact query from translated Groovy
     * ({@code OnboardingFlowAGuestidmember9}), so the table and column are
     * proven against the same credentials.</p>
     *
     * <p>FAIL-SOFT BY DESIGN. Returns an empty list rather than throwing when
     * the DB is off, the credentials are placeholders, the query is refused by
     * the SQL guard, or the driver errors. The caller then keeps whatever
     * {@code ALLOWED_DOMAINS} was configured, so a database problem can never
     * empty the domain allowlist -- doing so would poison generated identity
     * for the whole suite, which is far worse than a stale list.</p>
     *
     * @param reason the {@code reason} column value, e.g. {@code managed_domain}
     * @return distinct, lower-cased, non-blank domains; empty when unavailable
     */
    public static List<String> domainsByReason(String reason) {
        if (reason == null || reason.isBlank()) {
            LOG.warn("AccountRulesRepository.domainsByReason skipped: blank reason");
            return List.of();
        }
        if (!isDbUsable()) {
            return List.of();
        }
        String table = Config.get("domains.fromDb.table", "account_rules");
        String sql = "SELECT " + VALUE_COLUMN + " FROM " + table + " WHERE reason = ?";
        // Same guard the emitted JDBC steps apply before Db.queryAll.
        String refused = Db.unsafeSqlReasonForQuery(sql);
        if (refused != null) {
            LOG.warn("AccountRulesRepository.domainsByReason refused ({}): {}", refused, sql);
            return List.of();
        }
        try {
            List<Map<String, Object>> rows = Db.queryAll(sql, reason);
            Set<String> out = new LinkedHashSet<>();
            for (Map<String, Object> row : rows) {
                Object v = columnValue(row);
                if (v == null) {
                    continue;
                }
                String d = String.valueOf(v).trim().toLowerCase(Locale.ROOT);
                if (d.startsWith("www.")) {
                    d = d.substring(4);
                }
                if (!d.isEmpty()) {
                    out.add(d);
                }
            }
            LOG.info("AccountRulesRepository: {} domain(s) for reason={}", out.size(), reason);
            return new ArrayList<>(out);
        } catch (RuntimeException e) {
            // Db.queryAll wraps SQLException in RuntimeException. A DB blip
            // must not fail the suite -- the caller falls back to config.
            LOG.warn("AccountRulesRepository.domainsByReason failed for reason={}: {}",
                    reason, e.getMessage());
            return List.of();
        }
    }

    /**
     * Whether a DB call is worth attempting.
     *
     * <p>{@code Db.isConfigured()} only checks that {@code db.url} is
     * non-blank, and the shipped {@code program_configuration.json} builds a
     * URL out of {@code __SET_ME__} placeholders -- non-blank, but guaranteed
     * to fail at connect time. {@code Config.isUnset} already knows that
     * vocabulary, so reuse it rather than inventing a second rule.</p>
     */
    /**
     * The {@code value} column, whatever case the driver reports.
     *
     * <p>{@code Db.readAll} keys each row by {@code getColumnLabel}, and
     * drivers disagree on folding: PostgreSQL lower-cases an unquoted
     * identifier, H2 upper-cases it. A case-SENSITIVE {@code row.get("value")}
     * therefore matched Postgres and silently missed H2 -- every row dropped,
     * an empty list returned, and no exception raised. Combined with the
     * fail-soft contract that empty means "keep the configured list", that
     * would have looked exactly like "no rows configured" rather than a bug.
     *
     * <p>Falls back to the sole column when the row has exactly one, since
     * the query selects a single column and an aliased result would still
     * be unambiguous.</p>
     */
    private static Object columnValue(Map<String, Object> row) {
        if (row == null || row.isEmpty()) {
            return null;
        }
        Object direct = row.get(VALUE_COLUMN);
        if (direct != null) {
            return direct;
        }
        for (Map.Entry<String, Object> e : row.entrySet()) {
            if (e.getKey() != null && e.getKey().equalsIgnoreCase(VALUE_COLUMN)) {
                return e.getValue();
            }
        }
        return row.size() == 1 ? row.values().iterator().next() : null;
    }

    private static boolean isDbUsable() {
        if (!Db.isConfigured()) {
            LOG.debug("AccountRulesRepository: DB is not configured");
            return false;
        }
        String url = Config.get("db.url", "");
        if (Config.isUnset(url)) {
            LOG.warn("AccountRulesRepository: db.url is still a placeholder -- skipping DB read");
            return false;
        }
        return true;
    }


    /**
     * {@code INSERT INTO account_rules (rule_type, value, reason)
     * VALUES ('account_whitelist', ?, ?)}.
     *
     * @return rows updated, or 0 when DB is off / SQL gated
     */
    public static int whitelistIata(String value, String reason) {
        if (!Db.isConfigured()) {
            LOG.warn("AccountRulesRepository.whitelistIata skipped: DB is not configured");
            return 0;
        }
        if (value == null || value.isBlank()) {
            LOG.warn("AccountRulesRepository.whitelistIata skipped: empty value");
            return 0;
        }
        String why = reason == null ? "" : reason;
        try {
            return Db.execute(
                    "INSERT INTO account_rules (rule_type, value, reason) VALUES ('account_whitelist', ?, ?)",
                    value, why);
        } catch (RuntimeException e) {
            LOG.warn("AccountRulesRepository.whitelistIata failed: {}", e.getMessage());
            return 0;
        }
    }
}
