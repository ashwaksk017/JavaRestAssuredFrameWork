package com.ak.api.db.repo;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.ak.api.db.Db;

/**
 * {@code account_rules} writes used by LTA travel-agent whitelist Groovy.
 */
public final class AccountRulesRepository {

    private static final Logger LOG = LoggerFactory.getLogger(AccountRulesRepository.class);

    private AccountRulesRepository() {}

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
