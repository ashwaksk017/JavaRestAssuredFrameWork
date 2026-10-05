// =============================================================================
// XrayClient -- Xray result import, for Cloud AND Server / Data Center
// -----------------------------------------------------------------------------
// TWO FLAVOURS, because the two products authenticate differently and this
// client only ever spoke Cloud:
//
//   CLOUD          POST /api/v2/authenticate {client_id, client_secret}
//                    -> JWT, sent as `Authorization: Bearer <jwt>`
//                  POST /api/v2/import/execution
//
//   SERVER / DC    no auth exchange at all. A Jira personal access token
//                  goes straight on as `Authorization: Bearer <pat>`
//                  POST <jira-base>/rest/raven/1.0/import/execution
//
// Both take the same `{testExecutionKey, info, tests}` body, so only the
// credential and the path differ.
//
// Which flavour is picked (first match wins, so an explicit setting always
// beats a guess):
//   1. `xray.flavour` = cloud | server
//   2. baseUrl host contains `getxray.app`            -> cloud
//   3. `xray.token` is set                            -> server
//   4. `xray.clientId` + `xray.clientSecret` are set  -> cloud
//
// Rest Assured is still the transport by default, so the framework
// dogfoods its own HTTP stack -- but it is injectable, because a client
// whose only test is "point it at a real Jira" gets no tests at all.
//
// The Cloud calls:
//
//   1. POST /api/v2/authenticate  {"client_id":"...", "client_secret":"..."}
//        -> returns a JWT string (Rest Assured extracts as a body string,
//           trimmed of surrounding quotes since Xray responds with a bare
//           quoted JSON string, not an object)
//
//   2. POST /api/v2/import/execution
//        Authorization: Bearer <jwt>
//        {
//          "testExecutionKey": "PROJ-123",   // optional; new execution if absent
//          "info": { "summary": "...", "startDate": "...", "finishDate": "..." },
//          "tests": [ { "testKey": "PROJ-456", "status": "PASSED", "comment": "..." } ]
//        }
//
// Configuration (all read via Config, so -D / env-var / properties layering
// applies uniformly):
//     xray.enabled           = false           # master kill switch (default)
//     xray.flavour           = (optional: cloud | server; inferred otherwise)
//     xray.baseUrl           = https://xray.cloud.getxray.app   # CLOUD
//                            = https://jira.yourorg.com         # SERVER/DC
//     xray.clientId          = (CLOUD, required)
//     xray.clientSecret      = (CLOUD, required)
//     xray.token             = (SERVER/DC, required -- a Jira PAT)
//     xray.importPath        = (optional; defaults per flavour. Falls back to
//                               `xray_api_config.route`, which is where this
//                               repo's Server/DC route was already configured)
//     xray.projectKey        = (SERVER/DC, only when no testExecutionKey --
//                               Server needs a project to create one in)
//     xray.testExecutionKey  = (optional -- Xray creates a new execution if absent)
//
// Failure semantics: XrayClient NEVER throws. Any HTTP / auth failure is
// logged to stderr and swallowed -- Xray outages must not fail the local
// automation suite.
// =============================================================================

package com.hi.api.xray;

import static io.restassured.RestAssured.given;

import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.hi.api.config.Config;

import io.restassured.http.ContentType;
import io.restassured.response.Response;

public final class XrayClient {

    private static final String DEFAULT_BASE = "https://xray.cloud.getxray.app";
    private static final String AUTH_PATH    = "/api/v2/authenticate";
    private static final String IMPORT_PATH  = "/api/v2/import/execution";
    /** Xray Server / Data Center's import route. */
    private static final String SERVER_IMPORT_PATH =
            "/rest/raven/1.0/import/execution";

    /** Which product we are talking to. They differ only in auth and path. */
    public enum Flavour { CLOUD, SERVER }

    /** One HTTP reply, so a test can assert on what was sent and returned. */
    public record HttpReply(int status, String body) {}

    /**
     * The POST seam.
     *
     * <p>Exists so the two flavours can be tested without a Jira. The
     * default implementation is Rest Assured, which keeps the framework
     * dogfooding its own HTTP stack; a test passes a stub and inspects the
     * URL, the headers and the body that would have gone out.</p>
     */
    @FunctionalInterface
    public interface Transport {
        HttpReply post(String url, Map<String, String> headers, Object jsonBody);
    }

    private final String baseUrl;
    private final String clientId;
    private final String clientSecret;
    private final String token;
    private final String testExecutionKey;
    private final String projectKey;
    private final String importPath;
    private final Flavour flavour;
    private final boolean enabled;
    private final Transport transport;

    public XrayClient() {
        this(restAssuredTransport());
    }

    public XrayClient(Transport transport) {
        this.transport        = transport == null ? restAssuredTransport() : transport;
        this.enabled          = "true".equalsIgnoreCase(Config.get("xray.enabled", "false"));
        this.baseUrl          = Config.get("xray.baseUrl", DEFAULT_BASE);
        this.clientId         = Config.get("xray.clientId", null);
        this.clientSecret     = Config.get("xray.clientSecret", null);
        this.token            = Config.get("xray.token", null);
        this.testExecutionKey = Config.get("xray.testExecutionKey", null);
        this.projectKey       = Config.get("xray.projectKey", null);
        this.flavour          = detectFlavour(
                Config.get("xray.flavour", null), this.baseUrl,
                this.token, this.clientId, this.clientSecret);
        // `xray_api_config.route` is honoured ONLY for Server/DC. It holds a
        // `/rest/raven/...` route -- that is where this repo's Server/DC
        // route was already configured -- and letting it reach a CLOUD
        // publish sent results to
        // `xray.cloud.getxray.app/rest/raven/1.0/import/execution`, which
        // is not a Cloud endpoint. Caught by the test that pins the Cloud
        // path; an explicit `xray.importPath` still wins for either.
        String explicitPath = Config.get("xray.importPath", null);
        String legacyRoute = this.flavour == Flavour.SERVER
                ? Config.get("xray_api_config.route", null)
                : null;
        String configured = isSet(explicitPath) ? explicitPath : legacyRoute;
        this.importPath = isSet(configured)
                ? configured.trim()
                : (this.flavour == Flavour.SERVER ? SERVER_IMPORT_PATH : IMPORT_PATH);
    }

    /**
     * Which product, by the rule in the header comment.
     *
     * <p>Static and parameterised so the decision is testable on its own.
     * An explicit {@code xray.flavour} always wins: inference is a
     * convenience, never an override of something someone stated.</p>
     */
    public static Flavour detectFlavour(String explicit, String baseUrl, String token,
                                        String clientId, String clientSecret) {
        if (explicit != null && !explicit.isBlank()) {
            return "server".equalsIgnoreCase(explicit.trim())
                    || "dc".equalsIgnoreCase(explicit.trim())
                    || "datacenter".equalsIgnoreCase(explicit.trim())
                    ? Flavour.SERVER : Flavour.CLOUD;
        }
        // The host is the strongest signal: only Cloud lives on getxray.app,
        // and it is checked before the credentials so a tree that has both
        // kinds of key lying around still resolves the same way every run.
        if (baseUrl != null && baseUrl.toLowerCase().contains("getxray.app")) {
            return Flavour.CLOUD;
        }
        if (isSet(token)) {
            return Flavour.SERVER;
        }
        if (isSet(clientId) && isSet(clientSecret)) {
            return Flavour.CLOUD;
        }
        // A baseUrl pointed somewhere other than Cloud, with no credential
        // either way, is still a statement about which product this is.
        // Reporting CLOUD there produced a skip message telling the reader
        // to go and get Cloud API keys for a Server/DC instance.
        if (isSet(baseUrl)) {
            return Flavour.SERVER;
        }
        return Flavour.CLOUD;      // nothing said at all: historical default
    }

    private static boolean isSet(String s) {
        return s != null && !s.isBlank();
    }

    /** Which product this run would publish to. */
    public Flavour flavour() {
        return flavour;
    }

    /**
     * True when the master switch is on AND this flavour's credential is
     * present: a PAT for Server/DC, a client id + secret for Cloud.
     */
    public boolean isEnabled() {
        if (!enabled) {
            return false;
        }
        return flavour == Flavour.SERVER
                ? isSet(token)
                : isSet(clientId) && isSet(clientSecret);
    }

    /** Why a run would not publish, or null when it would. */
    String disabledReason() {
        if (!enabled) {
            return "xray.enabled=false";
        }
        if (flavour == Flavour.SERVER && !isSet(token)) {
            return "flavour=SERVER (baseUrl is not an Xray Cloud host) but "
                    + "xray.token is empty. Server / Data Center authenticates "
                    + "with a Jira personal access token -- there are no Cloud "
                    + "client keys to issue on it. Put the PAT in "
                    + "xray_api_config.token or pass -Dxray.token=...";
        }
        if (flavour == Flavour.CLOUD && !(isSet(clientId) && isSet(clientSecret))) {
            return "flavour=CLOUD but xray.clientId / xray.clientSecret is "
                    + "empty. Cloud needs an API key PAIR (Xray > Settings > "
                    + "API Keys), not a PAT. If your Jira is self-hosted, set "
                    + "xray.baseUrl to it -- the flavour follows the host";
        }
        return null;
    }

    /**
     * Import a batch of results in a single POST. Silently no-ops when
     * disabled or misconfigured. Returns true if the import returned 2xx.
     */
    public boolean importResults(List<XrayResult> results, Instant startedAt, Instant finishedAt) {
        if (!isEnabled()) {
            log("skipped -- " + disabledReason());
            return false;
        }
        if (results == null || results.isEmpty()) {
            log("skipped -- no results with a jira_xray_id to import");
            return false;
        }

        // Cloud exchanges keys for a JWT; Server / DC sends the PAT as-is.
        // The bearer differs, nothing else does.
        String bearer;
        if (flavour == Flavour.SERVER) {
            bearer = token.trim();
        } else {
            try {
                bearer = authenticate();
            } catch (RuntimeException authErr) {
                log("auth failed: " + authErr.getMessage());
                return false;
            }
            if (bearer == null || bearer.isBlank()) {
                log("auth failed: empty JWT");
                return false;
            }
        }

        Map<String, Object> body = buildImportBody(results, startedAt, finishedAt);
        String url = baseUrl + importPath;
        try {
            Map<String, String> headers = new LinkedHashMap<>();
            headers.put("Authorization", "Bearer " + bearer);
            HttpReply res = transport.post(url, headers, body);
            int code = res.status();
            log("imported " + results.size() + " result(s) to " + flavour
                    + " " + url + " -> HTTP " + code
                    + (code >= 200 && code < 300 ? " OK" : " body=" + res.body()));
            return code >= 200 && code < 300;
        } catch (RuntimeException importErr) {
            log("import call failed: " + importErr.getMessage());
            return false;
        }
    }

    /** Rest Assured, so the framework keeps using its own HTTP stack. */
    private static Transport restAssuredTransport() {
        return (url, headers, jsonBody) -> {
            io.restassured.specification.RequestSpecification spec = given()
                    .relaxedHTTPSValidation()
                    .contentType(ContentType.JSON);
            if (headers != null) {
                for (Map.Entry<String, String> h : headers.entrySet()) {
                    spec = spec.header(h.getKey(), h.getValue());
                }
            }
            Response r = spec.body(jsonBody).when().post(url);
            return new HttpReply(r.statusCode(), r.body() == null
                    ? "" : r.body().asString());
        };
    }

    // ---- internals ----

    private String authenticate() {
        Map<String, String> credBody = new LinkedHashMap<>();
        credBody.put("client_id", clientId);
        credBody.put("client_secret", clientSecret);

        HttpReply res = transport.post(baseUrl + AUTH_PATH,
                new LinkedHashMap<>(), credBody);

        int code = res.status();
        if (code < 200 || code >= 300) {
            throw new RuntimeException("HTTP " + code + " on " + AUTH_PATH
                    + " body=" + res.body());
        }
        // Xray returns the JWT as a bare quoted JSON string: "eyJ..."
        return trimSurroundingQuotes(res.body());
    }

    private static String trimSurroundingQuotes(String s) {
        if (s == null) return null;
        String t = s.trim();
        if (t.length() >= 2 && t.startsWith("\"") && t.endsWith("\"")) {
            return t.substring(1, t.length() - 1);
        }
        return t;
    }

    private Map<String, Object> buildImportBody(List<XrayResult> results,
                                                Instant startedAt, Instant finishedAt) {
        Map<String, Object> body = new LinkedHashMap<>();
        if (testExecutionKey != null && !testExecutionKey.isBlank()) {
            body.put("testExecutionKey", testExecutionKey.trim());
        }

        Map<String, Object> info = new LinkedHashMap<>();
        // Server / DC needs a project to create an execution IN when no
        // testExecutionKey was given; Cloud infers it from the test keys.
        // Only sent when both apply, so a Cloud body is unchanged.
        if (flavour == Flavour.SERVER
                && !isSet(testExecutionKey) && isSet(projectKey)) {
            info.put("project", projectKey.trim());
        }
        info.put("summary", "api-automation-restassured -- automated run");
        info.put("startDate",  DateTimeFormatter.ISO_INSTANT.format(startedAt));
        info.put("finishDate", DateTimeFormatter.ISO_INSTANT.format(finishedAt));
        body.put("info", info);

        List<Map<String, Object>> tests = new ArrayList<>(results.size());
        for (XrayResult r : results) {
            Map<String, Object> t = new LinkedHashMap<>();
            t.put("testKey", r.testKey());
            t.put("status", r.status().name());
            if (r.comment() != null && !r.comment().isBlank()) {
                t.put("comment", truncate(r.comment(), 2000));
            }
            tests.add(t);
        }
        body.put("tests", tests);
        return body;
    }

    private static String truncate(String s, int max) {
        if (s == null || s.length() <= max) return s;
        return s.substring(0, max) + "...(truncated)";
    }

    private static void log(String msg) {
        System.out.println("[XrayClient] " + msg);
    }
}
