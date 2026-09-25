package com.hi.api.db.schema;

import org.jooq.Catalog;
import org.jooq.impl.SchemaImpl;

/**
 * Hilton {@code segment} schema. Hand-maintained so OTP / member
 * verification can use type-safe jOOQ tables without running codegen
 * against a live database. Full catalog:
 * {@code mvn generate-sources -Djooq.codegen.skip=false} (see pom).
 */
public final class Segment extends SchemaImpl {

    public static final Segment SEGMENT = new Segment();

    private Segment() {
        super("segment", null);
    }

    @Override
    public Catalog getCatalog() {
        return null;
    }
}
