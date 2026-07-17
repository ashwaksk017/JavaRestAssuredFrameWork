// =============================================================================
// Headers -- fluent HTTP header builder
// -----------------------------------------------------------------------------
// Composes Map<String,String> header maps that plug straight into
// RestUtilities.getResponseXxx(url, headers) and RestAssured .headers(map).
//
// Common patterns:
//   Map<String,String> h = Headers.builder()
//           .bearer(token)
//           .acceptJson()
//           .contentTypeJson()
//           .correlationId()            // auto-generates UUID
//           .header("X-Tenant-Id", "acme")
//           .build();
//
//   Map<String,String> h = Headers.builder()
//           .from(AuthUtilities.authHeaderMap())   // stack on configured auth
//           .contentTypeJson()
//           .build();
//
//   Map<String,String> h = Headers.merge(a, b, c); // b/c win on conflict
//
// Design notes:
//   * Returns LinkedHashMap so header order is preserved (matters for some
//     signature schemes and for readable request logs).
//   * Null values are treated as "clear this header" -- convenient for
//     conditional adds without if/else at the call site.
//   * Auth methods delegate to AuthUtilities so we have ONE source of truth
//     for Bearer / Basic serialization.
// =============================================================================

package com.ak.api.rest.utilities;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;

import com.ak.api.auth.AuthUtilities;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

public final class Headers {

    // ---- Common header names (constants so tests reference them consistently) ----
    public static final String AUTHORIZATION     = "Authorization";
    public static final String CONTENT_TYPE      = "Content-Type";
    public static final String ACCEPT            = "Accept";
    public static final String USER_AGENT        = "User-Agent";
    public static final String X_CORRELATION_ID  = "X-Correlation-Id";
    public static final String X_REQUEST_ID      = "X-Request-Id";
    public static final String X_TRACE_ID        = "X-Trace-Id";
    public static final String X_IDEMPOTENCY_KEY = "X-Idempotency-Key";

    private Headers() { }

    public static Builder builder() {
        return new Builder();
    }

    /** Preset: Content-Type + Accept both application/json. */
    public static Map<String, String> json() {
        return builder().contentTypeJson().acceptJson().build();
    }

    /** Merge N maps into a new LinkedHashMap. Later maps win on conflict. Nulls skipped. */
    @SafeVarargs
    public static Map<String, String> merge(Map<String, String>... maps) {
        Map<String, String> out = new LinkedHashMap<>();
        for (Map<String, String> m : maps) {
            if (m != null) out.putAll(m);
        }
        return out;
    }

    // =========================================================================
    // File loaders -- JSON / Properties / Text (HTTP-style)
    // -------------------------------------------------------------------------
    // All three accept EITHER:
    //   * a classpath resource ("headers/qa.json") -- searched first, so tests
    //     can ship fixtures in src/test/resources without absolute paths
    //   * a filesystem path ("C:/creds/prod-headers.json") -- for real deploys
    //     where secrets live outside the artifact
    // =========================================================================

    private static final ObjectMapper JSON_MAPPER = new ObjectMapper();

    /**
     * Load headers from a JSON file. Accepts either a flat map
     * ({"X-Api-Key":"..."}) OR a wrapped object ({"headers":{...}}).
     * Numeric / boolean values are coerced to strings.
     */
    public static Map<String, String> fromJson(String pathOrResource) {
        try (InputStream in = openStream(pathOrResource)) {
            Map<String, Object> raw = JSON_MAPPER.readValue(in, new TypeReference<>() { });
            Object nested = raw.get("headers");
            @SuppressWarnings("unchecked")
            Map<String, Object> src = (nested instanceof Map) ? (Map<String, Object>) nested : raw;
            Map<String, String> out = new LinkedHashMap<>();
            for (Map.Entry<String, Object> e : src.entrySet()) {
                if (e.getValue() != null) {
                    out.put(e.getKey(), String.valueOf(e.getValue()));
                }
            }
            return out;
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read headers JSON: " + pathOrResource, e);
        }
    }

    /**
     * Load headers from a Java .properties file. Every key becomes a header
     * name; values are stripped of surrounding whitespace. Order is preserved
     * (uses insertion-order Properties reader).
     */
    public static Map<String, String> fromProperties(String pathOrResource) {
        Properties props = new Properties();
        try (InputStream in = openStream(pathOrResource)) {
            props.load(in);
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read headers properties: " + pathOrResource, e);
        }
        // Properties doesn't preserve order -- re-read as text to keep insertion order.
        Map<String, String> ordered = readTextKeyValue(pathOrResource, '=');
        if (!ordered.isEmpty()) return ordered;
        // Fallback: unordered Properties keys (rare edge case).
        Map<String, String> out = new LinkedHashMap<>();
        for (String key : props.stringPropertyNames()) {
            out.put(key, props.getProperty(key));
        }
        return out;
    }

    /**
     * Load headers from an HTTP-style text file, one header per line:
     *     X-Api-Key: qa-key-123
     *     X-Env: qa
     * Blank lines and lines starting with # are ignored. First ':' splits
     * the name from the value.
     */
    public static Map<String, String> fromText(String pathOrResource) {
        return readTextKeyValue(pathOrResource, ':');
    }

    /**
     * Auto-detect loader by file extension: .json -> fromJson, .properties ->
     * fromProperties, everything else -> fromText.
     */
    public static Map<String, String> fromFile(String pathOrResource) {
        String lower = pathOrResource.toLowerCase(Locale.ROOT);
        if (lower.endsWith(".json"))       return fromJson(pathOrResource);
        if (lower.endsWith(".properties")) return fromProperties(pathOrResource);
        return fromText(pathOrResource);
    }

    // ---- shared file-open + text-parse plumbing ----

    private static InputStream openStream(String pathOrResource) throws IOException {
        // 1. Try classpath first (test/main resources).
        InputStream cp = Headers.class.getClassLoader().getResourceAsStream(pathOrResource);
        if (cp != null) return cp;
        // 2. Fall back to filesystem.
        Path p = Paths.get(pathOrResource);
        if (Files.exists(p)) return Files.newInputStream(p);
        throw new IOException(
                "Header file not found on classpath or filesystem: " + pathOrResource
                        + "  (looked under src/test/resources/" + pathOrResource + ")");
    }

    /**
     * Line-based reader for both text (":" separator) and properties ("="
     * separator) formats. Preserves insertion order.
     */
    private static Map<String, String> readTextKeyValue(String pathOrResource, char separator) {
        Map<String, String> out = new LinkedHashMap<>();
        try (InputStream in = openStream(pathOrResource);
             java.io.BufferedReader br = new java.io.BufferedReader(
                     new java.io.InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) {
                String trimmed = line.trim();
                if (trimmed.isEmpty() || trimmed.startsWith("#") || trimmed.startsWith("!")) {
                    continue;
                }
                int idx = trimmed.indexOf(separator);
                if (idx <= 0) continue; // no separator or empty key -- skip
                String key = trimmed.substring(0, idx).trim();
                String value = trimmed.substring(idx + 1).trim();
                if (!key.isEmpty()) {
                    out.put(key, value);
                }
            }
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to read headers file: " + pathOrResource, e);
        }
        return out;
    }

    // =========================================================================
    // Builder
    // =========================================================================

    public static final class Builder {

        private final Map<String, String> h = new LinkedHashMap<>();

        // ---- Content negotiation ----

        public Builder acceptJson()               { return set(ACCEPT, "application/json"); }
        public Builder acceptXml()                { return set(ACCEPT, "application/xml"); }
        public Builder acceptAny()                { return set(ACCEPT, "*/*"); }
        public Builder accept(String mediaType)   { return set(ACCEPT, mediaType); }

        public Builder contentTypeJson()             { return set(CONTENT_TYPE, "application/json"); }
        public Builder contentTypeXml()              { return set(CONTENT_TYPE, "application/xml"); }
        public Builder contentTypeFormUrlEncoded()   { return set(CONTENT_TYPE, "application/x-www-form-urlencoded"); }
        public Builder contentType(String mediaType) { return set(CONTENT_TYPE, mediaType); }

        // ---- Auth (delegates to AuthUtilities) ----

        public Builder bearer(String token) {
            return set(AUTHORIZATION, AuthUtilities.bearer(token));
        }

        public Builder basic(String username, String password) {
            return set(AUTHORIZATION, AuthUtilities.basic(username, password));
        }

        /**
         * Attach the Authorization header that AuthUtilities would produce for
         * the CURRENT Config -- lets a test opt into whatever auth mode was
         * configured (bearer / basic / oauth2 / none) without hard-coding.
         */
        public Builder configuredAuth() {
            return set(AUTHORIZATION, AuthUtilities.authHeaderValue());
        }

        // ---- Tracing / correlation ----

        public Builder correlationId(String id)   { return set(X_CORRELATION_ID, id); }
        public Builder correlationId(UUID id)     { return set(X_CORRELATION_ID, id == null ? null : id.toString()); }
        /** Convenience: auto-generate a fresh UUID correlation id. */
        public Builder correlationId()            { return correlationId(UUID.randomUUID()); }

        public Builder requestId(String id)       { return set(X_REQUEST_ID, id); }
        public Builder traceId(String id)         { return set(X_TRACE_ID, id); }
        public Builder idempotencyKey(String key) { return set(X_IDEMPOTENCY_KEY, key); }
        public Builder userAgent(String ua)       { return set(USER_AGENT, ua); }

        // ---- Arbitrary ----

        /** Set any header. Null value clears the entry instead of storing "null". */
        public Builder header(String name, String value) { return set(name, value); }

        /** Bulk merge from another map. Later calls win on conflict. */
        public Builder from(Map<String, String> other) {
            if (other != null) other.forEach(this::set);
            return this;
        }

        /** Load + merge headers from a JSON file (classpath or filesystem). */
        public Builder fromJsonFile(String pathOrResource) {
            return from(Headers.fromJson(pathOrResource));
        }

        /** Load + merge headers from a .properties file (classpath or filesystem). */
        public Builder fromPropertiesFile(String pathOrResource) {
            return from(Headers.fromProperties(pathOrResource));
        }

        /** Load + merge headers from an HTTP-style text file (classpath or filesystem). */
        public Builder fromTextFile(String pathOrResource) {
            return from(Headers.fromText(pathOrResource));
        }

        /**
         * Auto-detect file type by extension and merge headers from it.
         * Precedence: any header set AFTER this call wins over the file.
         */
        public Builder fromFile(String pathOrResource) {
            return from(Headers.fromFile(pathOrResource));
        }

        public Map<String, String> build() {
            return new LinkedHashMap<>(h);
        }

        // ---- internals ----

        private Builder set(String name, String value) {
            if (value == null) {
                h.remove(name);
            } else {
                h.put(name, value);
            }
            return this;
        }
    }
}
