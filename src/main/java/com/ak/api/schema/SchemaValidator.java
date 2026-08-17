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
     *
     * <p>NOTE: the underlying JsonSchemaValidator throws AssertionError for
     * BOTH cases -- (a) legitimate schema mismatch and (b) a broken /
     * unreadable schema file. Case (b) previously returned {@code false}
     * silently, and downstream soft-assert callers logged "schema mismatch"
     * with zero pointer to the actual root cause (the schema file itself
     * being broken). We log the exception message here so a broken schema
     * is diagnosable in surefire output without having to switch to the
     * throwing {@link #validate} variant.</p>
     */
    public static boolean matches(Response response, String schemaFileName) {
        try {
            validate(response, schemaFileName);
            return true;
        } catch (AssertionError e) {
            org.slf4j.LoggerFactory.getLogger(SchemaValidator.class)
                    .warn("SchemaValidator.matches: schema `{}` reported mismatch OR schema-load error: {}",
                          schemaFileName, e.getMessage());
            return false;
        } catch (RuntimeException e) {
            // Some versions of the underlying schema library wrap I/O
            // failures as RuntimeException instead of AssertionError.
            // Same treatment: log + return false so the caller's
            // soft-assert path is preserved but the root cause is at
            // least visible in the run output.
            org.slf4j.LoggerFactory.getLogger(SchemaValidator.class)
                    .warn("SchemaValidator.matches: schema `{}` threw {} -- probably a broken/missing schema file: {}",
                          schemaFileName, e.getClass().getSimpleName(), e.getMessage());
            return false;
        }
    }
}
