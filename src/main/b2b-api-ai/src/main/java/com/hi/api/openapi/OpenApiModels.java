package com.hi.api.openapi;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

import io.restassured.module.jsv.JsonSchemaValidator;
import io.restassured.response.Response;

/**
 * Bind Rest Assured responses to OpenAPI-generated models and (optionally)
 * validate bodies against a named Swagger {@code definitions} entry.
 *
 * <p>Imported ReadyAPI tests stay on JsonPath + {@code Map&lt;String,String&gt;}
 * ctx. This helper is additive for hand-written / domain-API callers.</p>
 */
public final class OpenApiModels {

    /**
     * Classpath resource of the Program Accounts spec.
     *
     * <p>The file name carries the API version, so it changes whenever the
     * vendor publishes one. Resolved from the {@code openapi.spec} key --
     * the SAME key {@code pom.xml} feeds to the generator's
     * {@code <inputSpec>} -- so {@code -Dopenapi.spec=abc.yaml} moves the
     * build and this lookup together. Moving only one of them gives you
     * models generated from one spec and validated against another, which
     * fails far from its cause.</p>
     *
     * <p>Also settable in {@code program_configuration.json} or as
     * {@code OPENAPI_SPEC}, by the usual Config precedence.</p>
     */
    public static final String PROGRAM_ACCOUNTS_SPEC =
            "openapi/" + com.hi.api.config.Config.get(
                    "openapi.spec", "ProgramAccounts-1.0.71.yaml");
    public static final String PROGRAM_ACCOUNTS_MODEL_PACKAGE =
            "com.hi.api.openapi.programaccounts.model";

    private static final Logger LOG = LoggerFactory.getLogger(OpenApiModels.class);

    private static final ObjectMapper JSON = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);

    private static final ObjectMapper YAML = new ObjectMapper(new YAMLFactory());

    private static final Map<String, Class<?>> MODEL_CACHE = new ConcurrentHashMap<>();

    private static volatile JsonNode definitions;
    private static volatile JsonNode schemaDefinitions;

    private OpenApiModels() {}

    public static <T> T as(Response response, Class<T> type) {
        if (response == null) {
            throw new IllegalArgumentException("response is required");
        }
        return asJson(response.body() == null ? "" : response.body().asString(), type);
    }

    public static <T> T asJson(String json, Class<T> type) {
        if (type == null) {
            throw new IllegalArgumentException("type is required");
        }
        try {
            return JSON.readValue(json == null ? "null" : json, type);
        } catch (IOException e) {
            throw new IllegalStateException(
                    "Failed to deserialize JSON into " + type.getName() + ": " + e.getMessage(), e);
        }
    }

    public static Object asNamed(Response response, String definitionName) {
        return as(response, modelClass(definitionName));
    }

    @SuppressWarnings("unchecked")
    public static <T> Class<T> modelClass(String definitionName) {
        if (definitionName == null || definitionName.isBlank()) {
            throw new IllegalArgumentException("definitionName is required");
        }
        Class<?> cached = MODEL_CACHE.computeIfAbsent(definitionName, name -> {
            String fqcn = PROGRAM_ACCOUNTS_MODEL_PACKAGE + "." + name;
            try {
                return Class.forName(fqcn);
            } catch (ClassNotFoundException e) {
                throw new IllegalArgumentException(
                        "No generated model " + fqcn + " — run Maven generate-sources", e);
            }
        });
        return (Class<T>) cached;
    }

    public static String toJson(Object model) {
        try {
            return JSON.writeValueAsString(model);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to serialize model to JSON: " + e.getMessage(), e);
        }
    }

    public static boolean matchesDefinition(Response response, String definitionName) {
        try {
            assertMatchesDefinition(response, definitionName);
            return true;
        } catch (AssertionError | RuntimeException e) {
            LOG.warn("OpenAPI definition `{}` mismatch: {}", definitionName, e.getMessage());
            return false;
        }
    }

    public static void assertMatchesDefinition(Response response, String definitionName) {
        if (response == null) {
            throw new IllegalArgumentException("response is required");
        }
        response.then().assertThat().body(
                JsonSchemaValidator.matchesJsonSchema(schemaDocument(definitionName)));
    }

    static String schemaDocument(String definitionName) {
        JsonNode defs = schemaDefinitions();
        if (defs == null || !defs.has(definitionName)) {
            throw new IllegalArgumentException(
                    "Unknown OpenAPI definition '" + definitionName + "' in " + PROGRAM_ACCOUNTS_SPEC);
        }
        ObjectNode root = JSON.createObjectNode();
        root.put("$schema", "http://json-schema.org/draft-04/schema#");
        root.put("$ref", "#/definitions/" + definitionName);
        root.set("definitions", defs);
        try {
            return JSON.writeValueAsString(root);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to build JSON Schema for " + definitionName, e);
        }
    }

    private static JsonNode schemaDefinitions() {
        JsonNode cached = schemaDefinitions;
        if (cached != null) {
            return cached;
        }
        synchronized (OpenApiModels.class) {
            if (schemaDefinitions == null) {
                schemaDefinitions = sanitizeForJsonSchema(definitions().deepCopy());
            }
            return schemaDefinitions;
        }
    }

    private static JsonNode definitions() {
        JsonNode cached = definitions;
        if (cached != null) {
            return cached;
        }
        synchronized (OpenApiModels.class) {
            if (definitions == null) {
                try (InputStream in = OpenApiModels.class.getClassLoader()
                        .getResourceAsStream(PROGRAM_ACCOUNTS_SPEC)) {
                    if (in == null) {
                        throw new IllegalStateException("Missing classpath resource " + PROGRAM_ACCOUNTS_SPEC);
                    }
                    JsonNode root = YAML.readTree(in);
                    JsonNode defs = root.get("definitions");
                    if (defs == null || !defs.isObject()) {
                        throw new IllegalStateException(
                                PROGRAM_ACCOUNTS_SPEC + " has no Swagger definitions object");
                    }
                    definitions = defs;
                } catch (IOException e) {
                    throw new IllegalStateException("Failed to load " + PROGRAM_ACCOUNTS_SPEC, e);
                }
            }
            return definitions;
        }
    }

    /**
     * Draft-04 / ECMA 262 cannot compile lookbehind or {@code \p{..}} classes
     * used in the Hilton Swagger patterns. Drop those patterns and vendor
     * extensions so json-schema-validator can still check types and required.
     */
    private static JsonNode sanitizeForJsonSchema(JsonNode node) {
        if (node == null || node.isMissingNode()) {
            return node;
        }
        if (node.isObject()) {
            ObjectNode obj = (ObjectNode) node;
            List<String> remove = new ArrayList<>();
            Iterator<Map.Entry<String, JsonNode>> it = obj.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                String key = e.getKey();
                JsonNode val = e.getValue();
                if (key.startsWith("x-")
                        || "readOnly".equals(key)
                        || "xml".equals(key)
                        || "example".equals(key)
                        || "externalDocs".equals(key)) {
                    remove.add(key);
                    continue;
                }
                if ("pattern".equals(key) && val != null && val.isTextual()
                        && !ecma262Safe(val.asText())) {
                    remove.add(key);
                    continue;
                }
                sanitizeForJsonSchema(val);
            }
            for (String key : remove) {
                obj.remove(key);
            }
        } else if (node.isArray()) {
            for (JsonNode child : node) {
                sanitizeForJsonSchema(child);
            }
        }
        return node;
    }

    private static boolean ecma262Safe(String pattern) {
        return pattern.indexOf("(?<") < 0 && pattern.indexOf("\\p{") < 0;
    }
}
