package com.hi.api.rest;

import java.io.InputStream;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Single place for REST path templates. Generated clients and
 * {@link com.hi.api.rest.utilities.RestStep} go through {@link #fill}
 * so an endpoint change can be remapped in {@code routes.json} without
 * editing every client method.
 *
 * <p>Optional classpath overlay {@code routes.json}: object of
 * {@code "exact-template": "replacement-template"}. Loaded once at
 * class init (suite startup).</p>
 */
public final class ApiRoutes {

    private static final Logger LOG = LoggerFactory.getLogger(ApiRoutes.class);
    private static final Map<String, String> OVERRIDES = loadOverrides();
    private static final ConcurrentHashMap<String, String> SEEN = new ConcurrentHashMap<>();

    public static final String TOKEN = "/realms/applications/token";
    public static final String GUEST_ENROLL = "/realms/guests/enroll";
    public static final String GUEST = "/guests/{guestId}";
    public static final String GUEST_BUSINESSES = "/guests/{guestId}/businesses";
    public static final String GUEST_BUSINESS = "/guests/{guestId}/businesses/{accountId}";
    public static final String BUSINESSES = "/businesses";
    public static final String BUSINESS = "/businesses/{accountId}";
    public static final String BUSINESS_ACTIVATE = "/businesses/{accountId}/activate";
    public static final String BUSINESS_REJECT = "/businesses/{accountId}/reject";
    public static final String MEMBER_CONFIRM_VALIDATION =
            "/guests/{guestId}/businesses/{accountId}/members/{memberId}/confirmValidation";
    public static final String MEMBER_REMOVE =
            "/businesses/{accountId}/members/{memberId}/remove";

    private ApiRoutes() {}

    /**
     * Apply optional {@code routes.json} override, intern the template,
     * then substitute {@code {name}} slots from alternating name/value
     * pairs. Null values become empty (same as generated {@code replace}).
     */
    public static String fill(String template, String... nameValues) {
        String t = template == null ? "" : template;
        String mapped = OVERRIDES.getOrDefault(t, t);
        SEEN.putIfAbsent(mapped, mapped);
        if (nameValues == null || nameValues.length == 0) {
            return mapped;
        }
        String out = mapped;
        for (int i = 0; i + 1 < nameValues.length; i += 2) {
            String name = nameValues[i];
            String value = nameValues[i + 1] == null ? "" : nameValues[i + 1];
            if (name != null && !name.isEmpty()) {
                out = out.replace("{" + name + "}", value);
            }
        }
        return out;
    }

    /** Templates observed this JVM (for drift checks / diagnostics). */
    public static Map<String, String> seenTemplates() {
        return Collections.unmodifiableMap(new LinkedHashMap<>(SEEN));
    }

    public static boolean isTokenPath(String path) {
        if (path == null) {
            return false;
        }
        String p = path.toLowerCase();
        return p.contains("/realms/applications/token")
                || p.contains("/protocol/openid-connect/token");
    }

    private static Map<String, String> loadOverrides() {
        Map<String, String> out = new LinkedHashMap<>();
        try (InputStream in = ApiRoutes.class.getClassLoader()
                .getResourceAsStream("routes.json")) {
            if (in == null) {
                return out;
            }
            JsonNode root = new ObjectMapper().readTree(in);
            if (root == null || !root.isObject()) {
                return out;
            }
            root.fields().forEachRemaining(e -> {
                if (e.getKey() == null || e.getKey().startsWith("_")) {
                    return;
                }
                if (e.getValue() != null && e.getValue().isTextual()) {
                    out.put(e.getKey(), e.getValue().asText());
                }
            });
            LOG.info("ApiRoutes: loaded {} path override(s) from routes.json", out.size());
        } catch (Exception e) {
            LOG.warn("ApiRoutes: failed to load routes.json: {}", e.getMessage());
        }
        return out;
    }
}
