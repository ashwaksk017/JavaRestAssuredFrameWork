package com.hi.api.db.repo;

import java.util.Map;

import com.hi.api.db.Db;
import com.hi.api.support.ImportedScenario;

/**
 * OTP reads for imported ReadyAPI JDBC steps. Prefer this over inlined
 * SQL in Support classes.
 */
public final class OtpRepository {

    private OtpRepository() {}

    public static String pollEmailOtp(String accountMemberId) {
        return Db.pollEmailOtp(accountMemberId);
    }

    /**
     * Poll OTP and publish to ctx as {@code totpKey} (ReadyAPI last-write
     * via {@link ImportedScenario#putExtracted}).
     */
    public static String pollAndStore(Map<String, String> ctx,
                                      String accountMemberId,
                                      String totpKey) {
        String otp = pollEmailOtp(accountMemberId);
        if (totpKey != null && !totpKey.isEmpty()) {
            ImportedScenario.putExtracted(ctx, totpKey, otp);
        }
        return otp;
    }
}
