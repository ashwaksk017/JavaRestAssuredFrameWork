package com.ak.api.db.schema;

/**
 * Hand-maintained jOOQ tables for DB verification. Prefer these (or
 * {@code com.ak.api.db.generated} after codegen) over {@code DSL.table("users")}
 * so identifiers stay schema-qualified and dialect-safe.
 */
public final class Tables {

    public static final AccountMemberInternalSecurity ACCOUNT_MEMBER_INTERNAL_SECURITY =
            AccountMemberInternalSecurity.ACCOUNT_MEMBER_INTERNAL_SECURITY;

    private Tables() {}
}
