// =============================================================================
// SchemaValidator
// -----------------------------------------------------------------------------
// Thin wrapper around Rest Assured's built-in JSON schema matcher. Loads schema
// from the classpath (test-resources/schemas/) so callers just pass the file name.
//
// Uses the JsonSchemaFactory JSON-Schema 2019-09 configuration by default;
// swap to draft-07 if your schemas were written for it.
// =============================================================================

package com.ak.api.schema;

import static io.restassured.module.jsv.JsonSchemaValidator.matchesJsonSchemaInClasspath;

import io.restassured.response.Response;

public final class SchemaValidator {

    private static final String SCHEMA_DIR = "schemas/";

    private SchemaValidator() {
    }

    /**
     * Validate a Response body against a JSON schema on the classpath.
     * Throws AssertionError with a detailed diff if validation fails.
     */
    public static void validate(Response response, String schemaFileName) {
        response.then().assertThat().body(matchesJsonSchemaInClasspath(SCHEMA_DIR + schemaFileName));
    }

    /**
     * Non-throwing variant: returns true when the response matches the schema.
     * Useful when you want to record the outcome as a soft-assert.
     */
    public static boolean matches(Response response, String schemaFileName) {
        try {
            validate(response, schemaFileName);
            return true;
        } catch (AssertionError e) {
            return false;
        }
    }
}
