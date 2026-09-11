package com.ak.api.rest.utilities;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.ak.api.config.Config;

import io.restassured.RestAssured;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

/**
 * Salesforce JWT-bearer token. ReadyAPI {@code sf-token-Request} posts
 * {@code grant_type} + {@code assertion} as form-urlencoded to
 * {@code /services/oauth2/token}, then Groovy {@code sf-Token} copies
 * {@code access_token} onto {@code sftokenId.GeneratedTokenID}.
 *
 * <p>The JWT {@code aud} claim is the authorization server. ReadyAPI's
 * saved {@code originalUri} is the sandbox My Domain. Either host can
 * return {@code invalid_client_id} when the other is required, so
 * {@link #requestToken} tries aud, then {@code sf_config.token_end_point},
 * then {@code sf_config.api_end_point}. Credentials stay in
 * {@code program_configuration.json} — this class does not invent them.</p>
 */
public final class SalesforceAuth {

    private static final Logger LOG = LoggerFactory.getLogger(SalesforceAuth.class);
    private static final Pattern AUD = Pattern.compile("\"aud\"\\s*:\\s*\"([^\"]+)\"");
    public static final String JWT_BEARER =
            "urn:ietf:params:oauth:grant-type:jwt-bearer";

    private SalesforceAuth() {}

    /**
     * Fill unresolved {@code grant_type} / {@code assertion} from
     * {@code sf_config}, then POST, retrying ReadyAPI's instance host
     * when the aud host returns {@code invalid_client_id}.
     */
    public static Response requestToken(Map<String, String> queryParams) {
        Map<String, String> form = queryParams == null
                ? new LinkedHashMap<>()
                : queryParams;
        fillJwtBearerForm(form);
        String path = Config.get("sf_config.token_route", "/services/oauth2/token");
        if (path == null || path.isBlank()) {
            path = "/services/oauth2/token";
        }
        if (!path.startsWith("/")) {
            path = "/" + path;
        }
        Response last = null;
        List<String> hosts = tokenHosts(form);
        if (hosts.isEmpty()) {
            LOG.warn("Salesforce token host list is empty -- "
                    + "set sf_config.token_end_point or sf_config.api_end_point");
            hosts.add("https://test.salesforce.com");
        }
        for (int i = 0; i < hosts.size(); i++) {
            String host = hosts.get(i);
            LOG.info(" .. sf token POST {}{}", host, path);
            last = postForm(host + path, form);
            if (last != null && last.getStatusCode() == 200) {
                return last;
            }
            if (i + 1 < hosts.size() && isInvalidClientId(last)) {
                LOG.warn("Salesforce token HTTP {} invalid_client_id from {} "
                        + "-- retrying ReadyAPI instance host (do not invent credentials)",
                        last == null ? -1 : last.getStatusCode(), host);
                continue;
            }
            return last;
        }
        return last;
    }

    public static String tokenEndpointBase(Map<String, String> formParams) {
        fillJwtBearerForm(formParams);
        List<String> hosts = tokenHosts(formParams);
        return hosts.isEmpty() ? "" : hosts.get(0);
    }

    /**
     * ReadyAPI stores grant_type as a CSV literal and assertion as
     * {@code ${#Project#salesforce_assertion}}. Unresolved {@code #qry_…#}
     * / {@code #salesforce_assertion#} become the mapJsonValues fallback
     * {@code null} and Salesforce returns {@code invalid_client_id}.
     */
    public static void fillJwtBearerForm(Map<String, String> form) {
        if (form == null) {
            return;
        }
        if (isUnresolvedFormValue(form.get("grant_type"))) {
            form.put("grant_type", Config.get("sf_config.grant_type", JWT_BEARER));
        }
        if (isUnresolvedFormValue(form.get("assertion"))) {
            String configured = Config.get("salesforce_assertion",
                    Config.get("sf_config.assertion", ""));
            if (configured != null && !configured.isBlank()) {
                form.put("assertion", configured);
            } else {
                LOG.warn("Salesforce JWT assertion is empty or unresolved -- "
                        + "POST /oauth2/token will fail. Set salesforce_assertion "
                        + "or sf_config.assertion in program_configuration.json "
                        + "(do not invent credentials)");
            }
        }
    }

    public static boolean isUnresolvedFormValue(String v) {
        if (v == null) {
            return true;
        }
        String t = v.trim();
        if (t.isEmpty() || "null".equalsIgnoreCase(t) || "0".equals(t)) {
            return true;
        }
        // mapJsonValues / CSV leftovers: #salesforce_assertion#,
        // #qry_sf_token_Request_grant_type#, ${#Project#salesforce_assertion}.
        // A JWT assertion must not be overwritten from sf_config just
        // because the payload/signature happens to contain '#'.
        if (looksLikeJwt(t)) {
            return false;
        }
        return t.indexOf('#') >= 0;
    }

    static boolean looksLikeJwt(String t) {
        if (t == null || t.length() < 20 || !t.startsWith("eyJ")) {
            return false;
        }
        int first = t.indexOf('.');
        if (first <= 0) {
            return false;
        }
        int second = t.indexOf('.', first + 1);
        return second > first;
    }

    static boolean isInvalidClientId(Response res) {
        if (res == null) {
            return false;
        }
        String body = res.asString();
        return body != null && body.contains("invalid_client_id");
    }

    public static List<String> tokenHosts(Map<String, String> form) {
        List<String> out = new ArrayList<>();
        String assertion = form == null ? null : form.get("assertion");
        addHost(out, audienceBaseUrl(assertion));
        addHost(out, stripSlash(Config.get("sf_config.token_end_point", "")));
        addHost(out, stripSlash(Config.get("sf_config.api_end_point", "")));
        return out;
    }

    private static void addHost(List<String> out, String host) {
        if (host == null || host.isBlank()) {
            return;
        }
        String h = host.trim();
        if (!h.startsWith("http")) {
            h = "https://" + h;
        }
        h = stripSlash(h);
        for (String existing : out) {
            if (existing.equalsIgnoreCase(h)) {
                return;
            }
        }
        out.add(h);
    }

    private static String stripSlash(String s) {
        if (s == null) {
            return "";
        }
        return s.replaceAll("/+$", "");
    }

    private static Response postForm(String url, Map<String, String> form) {
        return RestAssured.given()
                .header("Content-Type", "application/x-www-form-urlencoded")
                .header("Accept", "application/json")
                .contentType(ContentType.URLENC)
                .formParams(form)
                .post(url);
    }

    static String audienceBaseUrl(String jwt) {
        if (jwt == null || jwt.isBlank() || jwt.indexOf('.') < 0) {
            return null;
        }
        String[] parts = jwt.split("\\.", 3);
        if (parts.length < 2 || parts[1].isEmpty()) {
            return null;
        }
        try {
            String payload = parts[1];
            int pad = (4 - (payload.length() % 4)) % 4;
            if (pad > 0) {
                payload = payload + "====".substring(0, pad);
            }
            String json = new String(Base64.getUrlDecoder().decode(payload), StandardCharsets.UTF_8);
            Matcher m = AUD.matcher(json);
            if (!m.find()) {
                return null;
            }
            String aud = m.group(1).trim()
                    .replaceFirst("^https://", "")
                    .replaceFirst("/.*$", "");
            if (aud.isEmpty()) {
                return null;
            }
            return "https://" + aud;
        } catch (RuntimeException e) {
            LOG.warn(" .. could not read Salesforce JWT aud: {}", e.toString());
            return null;
        }
    }

    /**
     * ReadyAPI {@code ${http_request_200_2#Response#$['alternateAccounts']['salesforceId']}}
     * when the GET that actually holds the id is named something else
     * ({@code get_program_account}). Walk ctx last-write-wins extracts.
     */
    public static String resolveAccountId() {
        try {
            com.ak.api.support.ImportedScenario.Session session =
                    com.ak.api.support.ImportedScenario.current();
            return resolveAccountId(session == null ? null : session.ctx);
        } catch (IllegalStateException e) {
            return "";
        }
    }

    public static String resolveAccountId(Map<String, String> ctx) {
        if (ctx == null || ctx.isEmpty()) {
            return "";
        }
        String[] preferred = {
                "http_request_200_2.alternateAccounts_salesforceId",
                "http_request_200_2_Response_alternateAccounts_salesforceId",
                "Properties.salesforceId",
        };
        for (String key : preferred) {
            String v = com.ak.api.support.ImportedScenario.ctxGet(ctx, key);
            if (v != null && !v.isEmpty()) {
                return v;
            }
        }
        String best = "";
        String bestKey = null;
        for (Map.Entry<String, String> e : ctx.entrySet()) {
            String k = e.getKey();
            String v = e.getValue();
            if (k == null || v == null || v.isEmpty()) {
                continue;
            }
            String field = k.substring(k.lastIndexOf('.') + 1);
            if (!"salesforceId".equalsIgnoreCase(field)
                    && !"alternateAccounts_salesforceId".equalsIgnoreCase(field)) {
                continue;
            }
            if (bestKey == null || k.compareTo(bestKey) < 0) {
                bestKey = k;
                best = v;
            }
        }
        return best == null ? "" : best;
    }
}
