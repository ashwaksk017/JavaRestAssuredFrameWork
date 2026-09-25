package com.hi.api.db.schema;

import org.jooq.Record;
import org.jooq.TableField;
import org.jooq.impl.DSL;
import org.jooq.impl.SQLDataType;
import org.jooq.impl.TableImpl;

/**
 * {@code segment.account_member_internal_security} -- TOTP / email OTP
 * used by imported ReadyAPI JDBC extracts.
 */
public final class AccountMemberInternalSecurity extends TableImpl<Record> {

    public static final AccountMemberInternalSecurity ACCOUNT_MEMBER_INTERNAL_SECURITY =
            new AccountMemberInternalSecurity();

    public final TableField<Record, String> ACCOUNT_MEMBER_ID =
            createField(DSL.name("account_member_id"), SQLDataType.VARCHAR(64));

    public final TableField<Record, Integer> EMAIL_OTP =
            createField(DSL.name("email_otp"), SQLDataType.INTEGER);

    private AccountMemberInternalSecurity() {
        super(DSL.name("account_member_internal_security"), Segment.SEGMENT);
    }
}
