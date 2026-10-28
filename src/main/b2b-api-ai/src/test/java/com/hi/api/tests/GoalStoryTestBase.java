package com.hi.api.tests;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.testng.SkipException;

import com.hi.api.auth.JsonTokens;
import com.hi.api.config.Config;
import com.hi.api.data.ManualBody;
import com.hi.api.rest.ApiRoutes;
import com.hi.api.rest.utilities.RestUtilities;

import io.restassured.response.Response;

/**
 * The base class of a GOAL story test. GOAL is not B2B: there are no people
 * to create. A GOAL test FINDS a property and dates with availability (a
 * probe, never a fixed property or a frozen date), takes the server-made
 * {@code groupId} from the shop answer, and carries it through the group
 * chain -- as the converted GOAL suites do (AvailabilitySearch, the
 * group360create / roominglist chains). See
 * .cursor/skills/jira-to-restassured/GOAL_FLOWS.md.
 *
 * <p>Run with the GOAL project's configuration
 * ({@code -Dconfig.project=goal}: program_configuration.goal.json), whose
 * environment block has GOAL's {@code services.*}.</p>
 */
public abstract class GoalStoryTestBase extends StoryTestBase {

    private static final Logger LOG = LoggerFactory.getLogger(GoalStoryTestBase.class);

    /** GOAL's configuration (-Dconfig.project=goal); under B2B's, these tests skip. */
    @Override
    protected String testProject() {
        return "goal";
    }

    /** The service nearly every converted GOAL call goes to. */
    protected static final String GOAL_SERVICE = "hospitality_internal_all_v2";

    /** A property and dates the search proved have inventory, and the shop quote's ids. */
    protected static final class Availability {
        public final String propCode;
        public final String arrivalDate;
        public final String departureDate;
        public final String groupId;
        public final String roomTypeCode;
        public final String cacheExpiryTime;
        public final int inventory;

        Availability(String propCode, String arrivalDate, String departureDate, String groupId,
                     String roomTypeCode, String cacheExpiryTime, int inventory) {
            this.propCode = propCode;
            this.arrivalDate = arrivalDate;
            this.departureDate = departureDate;
            this.groupId = groupId;
            this.roomTypeCode = roomTypeCode;
            this.cacheExpiryTime = cacheExpiryTime;
            this.inventory = inventory;
        }

        /** The values a GOAL body's placeholders take: #propCode#, #groupId#, ... */
        public Map<String, String> asColumns() {
            Map<String, String> m = new LinkedHashMap<>();
            m.put("propCode", propCode);
            m.put("arrivalDate", arrivalDate);
            m.put("departureDate", departureDate);
            m.put("groupId", groupId);
            m.put("roomTypeCode", roomTypeCode);
            return m;
        }
    }

    /** The search's answer, kept for the run (as the converted search caches its winner). */
    private static final Map<String, Object[]> FOUND = new java.util.concurrent.ConcurrentHashMap<>();
    private static final long FOUND_TTL_MS = 30 * 60 * 1000L;

    private String goalTokenValue;
    private long goalTokenAt;
    private static final long TOKEN_REUSE_MS = 4 * 60 * 1000L;

    // ---- where and with what ------------------------------------------------------------

    /** GOAL's service base ({@code services.hospitality_internal_all_v2}, else the base URL). */
    protected String goalBase() {
        return Config.serviceBase(GOAL_SERVICE, "", baseUrl());
    }

    /**
     * The token every GOAL call carries: the converted tokenRequest (client
     * id / secret, the block's project username / password when set), sent
     * to the service the proven suites mint it at ({@code goal.token_service},
     * default {@code hospitality_partner_v2}). Asked again after four minutes; skipped, naming
     * the keys, when the client id / secret are not set.
     */
    protected String goalToken() {
        if (goalTokenValue != null && System.currentTimeMillis() - goalTokenAt < TOKEN_REUSE_MS) {
            return goalTokenValue;
        }
        String why = JsonTokens.missingConfig();
        if (!why.isEmpty()) {
            throw new SkipException(why);
        }
        // Where the proven suites mint it (PartialGoalRegression, Group360Create /
        // Shop: hospitality_partner_v2); goal.token_service names another.
        String service = Config.get("goal.token_service", "hospitality_partner_v2");
        String url = Config.serviceBase(service, "", goalBase()) + ApiRoutes.TOKEN;
        String user = Config.get("api_config.username", Config.get("username", ""));
        String pass = Config.get("api_config.password", Config.get("password", ""));
        if ((user.isBlank() || pass.isBlank()) && WARNED_GOAL_CREDENTIAL.compareAndSet(false, true)) {
            LOG.warn("GOAL token: the project credential (username / password of this environment) is not "
                    + "set -- the token is asked with the client credentials only; set them in "
                    + "program_configuration.goal.json if the token service needs them");
        }
        goalTokenValue = step("tokenRequest: GOAL service (as the converted suites)",
                () -> JsonTokens.at(url, user, pass)).accessToken;
        goalTokenAt = System.currentTimeMillis();
        return goalTokenValue;
    }

    private static final java.util.concurrent.atomic.AtomicBoolean WARNED_GOAL_CREDENTIAL =
            new java.util.concurrent.atomic.AtomicBoolean(false);

    // ---- availability ---------------------------------------------------------------------

    /**
     * Setup: a property and dates with availability, as the converted
     * AvailabilitySearch finds them -- each candidate property x each day
     * offset, {@code GET /props/{p}/groups?arrivalDate&departureDate&peakRooms},
     * the first where one of the first 6 {@code roomRates[i].inventory} is
     * above the threshold. Candidates: {@code goal.availability.props}
     * (comma list, from the environment; skipped naming the key when unset),
     * {@code goal.availability.offsets} (default 32,52,62,66 days from
     * today), {@code goal.availability.min_inventory} (default 9). The row's
     * {@code peak_rooms} (else goal.peak_rooms, else 10) always goes in the query. Null when none
     * answers (a SETUP failure naming what was tried).
     */
    protected Availability findAvailability(Map<String, String> row, String token, String tag) {
        List<String> props = list(Config.get("goal.availability.props", ""));
        if (props.isEmpty()) {
            throw new SkipException("goal.availability.props is not set for env '" + Config.env()
                    + "': list the candidate property codes (comma separated) in that block of "
                    + "the GOAL project's configuration file -- the converted suites' availability "
                    + "search shows the ones it uses");
        }
        List<Integer> offsets = new ArrayList<>();
        for (String o : list(Config.get("goal.availability.offsets", "32,52,62,66"))) {
            try {
                offsets.add(Integer.parseInt(o));
            } catch (NumberFormatException ignored) {
                LOG.warn("goal.availability.offsets: {} is not a number of days, left out", o);
            }
        }
        int min = intOr(Config.get("goal.availability.min_inventory", "9"), 9);
        String peak = peakRooms(row);
        String key = LocalDate.now() + "|" + props + "|" + offsets + "|" + min + "|" + peak;
        Object[] kept = FOUND.get(key);
        if (kept != null && System.currentTimeMillis() - (Long) kept[1] < FOUND_TTL_MS) {
            // The property and dates are reused; the quote is NOT: a groupId
            // an earlier test created a group with, an expiry that has run,
            // an inventory the earlier group took. Shop once more for those.
            Availability was = (Availability) kept[0];
            Response res = shopAt(was.propCode, was.arrivalDate, was.departureDate, peak, token);
            List<Object> inv = listAt(res, "roomRates.inventory");
            List<Object> codes = listAt(res, "roomRates.roomTypeCode");
            for (int i = 0; i < Math.min(6, inv.size()); i++) {
                int v = intOr(String.valueOf(inv.get(i)), -1);
                String code = i < codes.size() ? String.valueOf(codes.get(i)) : "";
                if (v > min && code.equals(was.roomTypeCode)) {
                    LOG.info("SETUP {} availability (property found earlier this run, quote fresh): {} {}",
                            tag, was.propCode, was.arrivalDate);
                    return new Availability(was.propCode, was.arrivalDate, was.departureDate,
                            RestUtilities.safeJsonExtract(res, "groupId"), code,
                            RestUtilities.safeJsonExtract(res, "cacheExpiryTime"), v);
                }
            }
            FOUND.remove(key);                              // no longer qualifies: search again
        }
        List<String> tried = new ArrayList<>();
        for (String p : props) {                           // property first, as the converted search
            for (int days : offsets) {
                String arrival = LocalDate.now().plusDays(days).toString();
                String departure = LocalDate.now().plusDays(days + 1L).toString();
                Response res;
                try {
                    res = shopAt(p, arrival, departure, peak, token);
                } catch (RuntimeException e) {
                    // One property or date that times out does not end the
                    // search -- the converted search skips it too.
                    tried.add(p + " " + arrival + " (" + e.getClass().getSimpleName() + ")");
                    continue;
                }
                List<Object> inv = listAt(res, "roomRates.inventory");
                List<Object> codes = listAt(res, "roomRates.roomTypeCode");
                int best = -1;
                for (int i = 0; i < Math.min(6, inv.size()); i++) {
                    int v = intOr(String.valueOf(inv.get(i)), -1);
                    best = Math.max(best, v);
                    if (v > min) {
                        String code = i < codes.size() ? String.valueOf(codes.get(i)) : "";
                        String groupId = RestUtilities.safeJsonExtract(res, "groupId");
                        String expiry = RestUtilities.safeJsonExtract(res, "cacheExpiryTime");
                        LOG.info("SETUP {} availability: {} {}..{} room {} inventory {} groupId {}",
                                tag, p, arrival, departure, code, v, groupId);
                        Availability av = new Availability(p, arrival, departure, groupId, code, expiry, v);
                        FOUND.put(key, new Object[] {av, System.currentTimeMillis()});
                        return av;
                    }
                }
                tried.add(p + " " + arrival + " (HTTP " + res.getStatusCode() + ", best " + best + ")");
            }
        }
        softAssert.fail(tag + "/SETUP: no candidate property had inventory above " + min + ": "
                + String.join("; ", tried));
        return null;
    }

    /** The shop quote of the availability found -- for a case whose act IS the shop read (unasserted). */
    protected Response shop(Availability av, String peakRooms, String token, String tag) {
        Response res = shopAt(av.propCode, av.arrivalDate, av.departureDate,
                peakRooms == null || peakRooms.isBlank() ? peakRooms(null) : peakRooms, token);
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        return res;
    }

    private Response shopAt(String propCode, String arrival, String departure, String peakRooms, String token) {
        String url = goalBase() + route(ApiRoutes.PROP_GROUPS, "propCode", propCode)
                + "?arrivalDate=" + enc(arrival) + "&departureDate=" + enc(departure)
                + (peakRooms == null || peakRooms.isBlank() ? "" : "&peakRooms=" + enc(peakRooms.trim()));
        return step("readPropsGroups: GET " + ApiRoutes.PROP_GROUPS,
                () -> RestUtilities.getResponseGet(url, apiHeaders(token)));
    }

    // ---- the group chain ----------------------------------------------------------------------

    /**
     * {@code POST /props/{p}/groups} -- the converted suites send it to the
     * block's {@code base_url} -- with {@code bodyTemplate} rendered from the
     * row plus the availability's {@code #propCode#}, {@code #arrivalDate#},
     * {@code #departureDate#}, {@code #groupId#}, {@code #roomTypeCode#}.
     * Unasserted: for a case whose act IS the create.
     */
    protected Response createGroupResponse(Availability av, Map<String, String> row, String bodyTemplate,
                                           String token, String tag) throws Exception {
        String body = render(bodyTemplate, row, av);
        RestUtilities.logRequestBody(tag, holder, body);
        String url = Config.get("base_url", baseUrl()) + route(ApiRoutes.PROP_GROUPS, "propCode", av.propCode);
        Response res = step("createPropsGroups: POST " + ApiRoutes.PROP_GROUPS,
                () -> RestUtilities.getResponsePost(body, url, apiHeaders(token)));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        return res;
    }

    /** Setup: the group created (asserted 200). */
    protected boolean createGroup(Availability av, Map<String, String> row, String bodyTemplate, String token,
                                  String tag) throws Exception {
        Response res = createGroupResponse(av, row, bodyTemplate, token, tag);
        String status = RestUtilities.checkActualResponse(res);
        softAssert.assertEquals(status, "200", tag + "/SETUP: POST create group status" + said(res));
        return "200".equals(status);
    }

    /** Setup: {@code POST /props/{p}/groups/{groupId}/payment} (asserted 200). */
    protected boolean payGroup(Availability av, Map<String, String> row, String bodyTemplate, String token,
                               String tag) throws Exception {
        String body = render(bodyTemplate, row, av);
        String url = goalBase() + route(ApiRoutes.PROP_GROUP_PAYMENT,
                "propCode", av.propCode, "groupId", av.groupId);
        Response res = step("createPropsGroupsPayment: POST " + ApiRoutes.PROP_GROUP_PAYMENT,
                () -> RestUtilities.getResponsePost(body, url, apiHeaders(token)));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        String status = RestUtilities.checkActualResponse(res);
        softAssert.assertEquals(status, "200", tag + "/SETUP: POST group payment status" + said(res));
        return "200".equals(status);
    }

    /**
     * {@code POST /props/{p}/groups/{groupId}/confirm}, with
     * {@code Hilton-Operator-Location} from {@code goal.operator_location}
     * when it is set. Unasserted -- the case says what the confirm must
     * answer (a confirm without payment answers 400).
     */
    protected Response confirmGroup(Availability av, Map<String, String> row, String bodyTemplate, String token,
                                    String tag) throws Exception {
        String body = render(bodyTemplate, row, av);
        String url = goalBase() + route(ApiRoutes.PROP_GROUP_CONFIRM,
                "propCode", av.propCode, "groupId", av.groupId);
        Map<String, String> headers = apiHeaders(token);
        String location = Config.get("goal.operator_location", "");
        if (location != null && !location.isBlank()) {
            headers.put("Hilton-Operator-Location", location.trim());
        }
        Response res = step("createPropsGroupsConfirm: POST " + ApiRoutes.PROP_GROUP_CONFIRM,
                () -> RestUtilities.getResponsePost(body, url, headers));
        RestUtilities.logResponseBody(tag, holder, RestUtilities.getResponseAsString(res));
        return res;
    }

    // ---- small things ---------------------------------------------------------------------------

    private String render(String template, Map<String, String> row, Availability av) throws Exception {
        Map<String, String> data = new LinkedHashMap<>();
        if (row != null) {
            data.putAll(row);
        }
        data.putAll(av.asColumns());
        if (data.get("inventoryCount") == null || data.get("inventoryCount").isBlank()) {
            // The row's, else the inventory the search found for that room (as
            // the converted search publishes it) -- never more than there is.
            data.put("inventoryCount", row != null && row.get("inventory_count") != null
                    && !row.get("inventory_count").isBlank() ? row.get("inventory_count").trim()
                    : String.valueOf(av.inventory));
        }
        if (data.get("peakRooms") == null || data.get("peakRooms").isBlank()) {
            data.put("peakRooms", peakRooms(row));          // what the search was shopped with
        }
        Map<String, String> known = new LinkedHashMap<>(ctx);  // the test's own values, the row and the search after
        known.putAll(data);
        return ManualBody.render(template, data, known);
    }

    /**
     * The peak rooms a shop is made for and a group asks for: the row's
     * {@code peak_rooms}, else {@code goal.peak_rooms}, else 10. Always sent:
     * every converted shop sends peakRooms, and a shop without it is
     * answered 400 -- measured on the converted probe, 460 of 500 searches
     * (support/AvailabilitySearch), which read as "no availability".
     */
    protected static String peakRooms(Map<String, String> row) {
        String v = row == null ? null : row.get("peak_rooms");
        if (v == null || v.isBlank()) {
            v = Config.get("goal.peak_rooms", "10");
        }
        return v == null || v.isBlank() ? "10" : v.trim();
    }

    private static List<String> list(String csv) {
        List<String> out = new ArrayList<>();
        for (String s : String.valueOf(csv == null ? "" : csv).split("[,;\\s]+")) {
            if (!s.isBlank()) {
                out.add(s.trim());
            }
        }
        return out;
    }

    private static int intOr(String s, int dflt) {
        try {
            return (int) Double.parseDouble(String.valueOf(s).trim());
        } catch (NumberFormatException e) {
            return dflt;
        }
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }
}
