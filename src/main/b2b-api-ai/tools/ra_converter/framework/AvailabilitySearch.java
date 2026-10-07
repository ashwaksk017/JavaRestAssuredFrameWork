package com.hi.api.support;

// ra_converter-framework-rev: 3
// Bumped whenever this bundled file changes. The converter SKIPS
// author-editable files that already exist, so without a revision it
// cannot tell an author's edit from a copy left by an older convert --
// and a run machine would keep executing the previous version while the
// commit claimed to have changed it.

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.hi.api.config.Config;

import io.restassured.response.Response;

/**
 * Find a property and date pair that actually has rooms, the way the
 * ReadyAPI Groovy step does.
 *
 * <p>The source script walks a list of property codes against a list of
 * date offsets, re-running the shop call each time, and keeps the first
 * combination where some room type has inventory above a threshold. It
 * then publishes that winner to a Properties step, and the rest of the
 * case reads it: `${generatedDatesAndProps#arrivalDate}` and friends
 * appear in workbook cells for inventory dates, propCode and more.</p>
 *
 * <p>The converter used to emit only the FIRST candidate and warn that
 * the search had not run -- 165 warnings in one run. That is not the
 * same test: the first candidate is whatever the script happened to
 * list first, and if it has no availability every downstream call is
 * built on a property/date with no rooms. The requests are
 * well-formed, so the failures look like assertion problems rather than
 * a missing search.</p>
 *
 * <h2>Why the winner is cached for the whole run</h2>
 *
 * <p>ReadyAPI re-runs the search per test case. Doing that here would
 * add up to {@code props x offsets} shop calls to EVERY test -- 40 per
 * case against a suite of 730 is tens of thousands of extra requests,
 * which would dominate the run and hammer a shared environment.</p>
 *
 * <p>What the search answers is a question about the ENVIRONMENT -- which
 * property has rooms around now -- not about the test's own data. That
 * answer does not change between two cases in the same run, so it is
 * resolved once per (threshold, candidate-list) and reused. The cost
 * becomes one search per run instead of one per case, and every case
 * still gets a combination that was verified to have inventory.</p>
 *
 * <p>Set {@code rest.availabilitySearch.perCase=true} to search for
 * every case instead, and {@code rest.availabilitySearch.enabled=false}
 * to go back to publishing the first candidate without probing.</p>
 */
public final class AvailabilitySearch {

    private static final Logger LOG =
            LoggerFactory.getLogger(AvailabilitySearch.class);

    /** The shop call, as the generated code can supply it. */
    @FunctionalInterface
    public interface Probe {
        Response shop(String propCode, String arrivalDate, String departureDate);
    }

    /**
     * One winning combination, and what the winning ROOM carried.
     *
     * <p>The Groovy does not merely choose a property and dates: on the
     * first qualifying room it also captures {@code groupId},
     * {@code cacheExpiryTime}, {@code roomTypeCode} and
     * {@code inventoryCount} and publishes them to a second Properties
     * step. Everything downstream reads those -- the POST body's
     * {@code "groupId"}, a Simple Contains on {@code roomTypeCode}, and
     * a 45-minute freshness assertion on {@code cacheExpiryTime}.
     * Publishing only the dates left all of them unresolved, which the
     * server answered with {@code 504 groupId Value not valid}.</p>
     */
    public static final class Found {
        public final String hcrs;
        public final String pcrs;
        public final String arrivalDate;
        public final String departureDate;
        public final boolean verified;
        /** From the winning response / room; empty when unverified. */
        public final String groupId;
        public final String cacheExpiryTime;
        public final String roomTypeCode;
        public final String inventoryCount;
        final long foundAtMillis = System.currentTimeMillis();

        Found(String hcrs, String pcrs, String arrivalDate,
              String departureDate, boolean verified) {
            this(hcrs, pcrs, arrivalDate, departureDate, verified,
                 "", "", "", "");
        }

        Found(String hcrs, String pcrs, String arrivalDate,
              String departureDate, boolean verified, String groupId,
              String cacheExpiryTime, String roomTypeCode,
              String inventoryCount) {
            this.hcrs = hcrs;
            this.pcrs = pcrs;
            this.arrivalDate = arrivalDate;
            this.departureDate = departureDate;
            this.verified = verified;
            this.groupId = groupId == null ? "" : groupId;
            this.cacheExpiryTime = cacheExpiryTime == null ? "" : cacheExpiryTime;
            this.roomTypeCode = roomTypeCode == null ? "" : roomTypeCode;
            this.inventoryCount = inventoryCount == null ? "" : inventoryCount;
        }
    }

    private static final Map<String, Found> CACHE = new ConcurrentHashMap<>();

    private AvailabilitySearch() {}

    /**
     * One probe query value, resolved the way the engine resolves it.
     *
     * <p>The emitted probe used to read `row.get("qry_<step>_<param>")`
     * for every non-literal parameter. That column only exists for a
     * parameter the XML gave no reference for. A parameter bound to
     * `${DataSource#peakRoom}` is emitted by the normal path as the
     * PLACEHOLDER `#DataSource_peakRoom#` and has no `qry_` column at
     * all, so the lookup returned null, the parameter was dropped, and
     * the probe went out without it. Measured: 460 of 500 probes came
     * back HTTP 400 and no search ever succeeded -- which looked like
     * an environment with no availability.</p>
     *
     * <p>Resolving against row+ctx merged is what the engine does, so
     * the probe now sends what the real call sends rather than what a
     * guessed column name happens to hold.</p>
     *
     * @return the resolved value, or "" when it does not resolve -- the
     *         caller drops empties, which is better than sending the
     *         literal placeholder text as a query value.
     */
    public static String resolve(String ref, Map<String, String> row,
                                 Map<String, String> ctx) {
        if (ref == null || ref.isEmpty()) {
            return "";
        }
        if (ref.indexOf('#') < 0) {
            return ref;                        // a recorded literal
        }
        String out;
        try {
            out = com.hi.api.rest.utilities.RestUtilities.mapJsonValues(
                    ref, ImportedScenario.mergedRow(row, ctx), false, false);
        } catch (Exception e) {
            return "";
        }
        if (out == null) {
            return "";
        }
        out = out.trim();
        // Unresolved comes back either as the literal token or, after the
        // non-strict fallback pass, as the string "null". Neither is a
        // query value; dropping it is what ReadyAPI does with an unset
        // parameter.
        if (out.isEmpty() || out.equals("null") || out.indexOf('#') >= 0) {
            return "";
        }
        return out;
    }

    /** Drop the per-run cache. For tests. */
    public static void resetCacheForTest() {
        CACHE.clear();
    }

    /**
     * Run the search (or reuse this run's answer) and publish the winner
     * under {@code <propsStep>.hcrs|pcrs|arrivalDate|departureDate}.
     *
     * <p>Always publishes something. A search that finds nothing still
     * publishes the first candidate, because leaving the keys absent
     * would turn every downstream reference into a literal placeholder
     * -- a worse failure, and a less legible one, than a date with no
     * rooms.</p>
     *
     * @return true when the published combination was VERIFIED to have
     *         inventory; false when it is the unverified first candidate.
     */
    /** Back-compat: no group step, scan every room. */
    public static boolean run(Map<String, String> ctx, String stepName,
                              String propsStep, List<String> hcrs,
                              List<String> pcrs, List<Integer> offsets,
                              int minInventory, Probe probe) {
        return run(ctx, stepName, propsStep, "", 0, hcrs, pcrs, offsets,
                   minInventory, probe);
    }

    /**
     * @param groupStep Properties step the winning ROOM's fields go to
     *                  ({@code groupId}, {@code cacheExpiryTime},
     *                  {@code roomTypeCode}, {@code inventoryCount}).
     *                  Empty to publish none.
     * @param scanRooms how many {@code roomRates} entries to consider.
     *                  The Groovy writes {@code for (t = 0; t < 6; t++)},
     *                  so a 7th room with inventory does NOT qualify
     *                  there and must not qualify here. 0 means all.
     */
    public static boolean run(Map<String, String> ctx, String stepName,
                              String propsStep, String groupStep,
                              int scanRooms, List<String> hcrs,
                              List<String> pcrs, List<Integer> offsets,
                              int minInventory, Probe probe) {
        if (ctx == null || hcrs == null || hcrs.isEmpty()
                || offsets == null || offsets.isEmpty()) {
            return false;
        }
        String props = propsStep == null || propsStep.isEmpty()
                ? "generatedDatesAndProps" : propsStep;
        String group = groupStep == null ? "" : groupStep.trim();
        Found first = candidate(hcrs, pcrs, offsets, 0, 0, false);

        if (!Config.getBool("rest.availabilitySearch.enabled", true)
                || probe == null) {
            publish(ctx, props, group, first);
            LOG.warn(" .. [availability] search DISABLED for `{}` -- publishing "
                     + "the first candidate {} on {} WITHOUT checking it has "
                     + "rooms. Downstream calls inherit whatever that is.",
                     stepName, first.hcrs, first.arrivalDate);
            return false;
        }

        String key = cacheKey(props, hcrs, pcrs, offsets, minInventory);
        boolean perCase = Config.getBool("rest.availabilitySearch.perCase", false);
        if (!perCase) {
            Found cached = CACHE.get(key);
            // cacheExpiryTime is asserted to be within 45 MINUTES of now, so
            // a winner reused for long enough starts failing an assertion
            // about freshness rather than about availability. Re-search past
            // the TTL instead of serving a stale capture.
            long ttlMin = Config.getInt("rest.availabilitySearch.cacheTtlMinutes", 30);
            if (cached != null && ttlMin > 0
                    && System.currentTimeMillis() - cached.foundAtMillis
                       > ttlMin * 60_000L) {
                LOG.info(" .. [availability] cached answer for `{}` is older than "
                         + "{} min -- searching again so cacheExpiryTime stays "
                         + "fresh", stepName, ttlMin);
                CACHE.remove(key);
                cached = null;
            }
            if (cached != null) {
                publish(ctx, props, group, cached);
                LOG.info(" .. [availability] reusing this run's answer for `{}`: "
                         + "{} on {} ({})", stepName, cached.hcrs,
                         cached.arrivalDate,
                         cached.verified ? "verified" : "unverified");
                return cached.verified;
            }
        }

        // Default to the full grid, which is what ReadyAPI walks. The knob
        // is there to bound a first run against an unfamiliar environment.
        int cap = Config.getInt("rest.availabilitySearch.maxProbes",
                                hcrs.size() * offsets.size());
        if (cap <= 0) {
            cap = hcrs.size() * offsets.size();
        }
        Found winner = search(stepName, hcrs, pcrs, offsets, minInventory,
                              probe, cap, scanRooms);
        if (winner == null) {
            winner = first;
            LOG.warn(" .. [availability] no combination for `{}` had inventory > "
                     + "{} after {} probe(s). Publishing the first candidate {} "
                     + "on {} unverified -- a downstream 'no rooms' answer here "
                     + "is the ENVIRONMENT, not the test data.",
                     stepName, minInventory, cap, first.hcrs, first.arrivalDate);
        } else {
            LOG.info(" .. [availability] `{}` -> {} on {}..{} "
                     + "(inventory {} > {}, roomType {}, groupId {})",
                     stepName, winner.hcrs, winner.arrivalDate,
                     winner.departureDate, winner.inventoryCount,
                     minInventory, winner.roomTypeCode,
                     winner.groupId.isEmpty() ? "(none)" : "captured");
        }
        if (!perCase) {
            CACHE.put(key, winner);
        }
        publish(ctx, props, group, winner);
        return winner.verified;
    }

    private static Found search(String stepName, List<String> hcrs,
                                List<String> pcrs, List<Integer> offsets,
                                int minInventory, Probe probe, int cap,
                                int scanRooms) {
        int probes = 0;
        // Property-major, offset-minor: the same order the Groovy loops in,
        // so the winner this picks is the winner ReadyAPI would pick.
        for (int j = 0; j < hcrs.size(); j++) {
            for (int i = 0; i < offsets.size(); i++) {
                if (probes >= cap) {
                    return null;
                }
                probes++;
                Found c = candidate(hcrs, pcrs, offsets, j, i, true);
                Response res;
                try {
                    res = probe.shop(c.hcrs, c.arrivalDate, c.departureDate);
                } catch (RuntimeException e) {
                    // A probe is a lookup, not a test. One bad combination
                    // must not end the search, let alone the test.
                    LOG.info(" .. [availability] probe {} on {} threw: {}",
                             c.hcrs, c.arrivalDate, e.toString());
                    continue;
                }
                if (res == null) {
                    continue;
                }
                int status = res.getStatusCode();
                if (status < 200 || status >= 300) {
                    LOG.info(" .. [availability] {} on {} -> HTTP {} (skipping)",
                             c.hcrs, c.arrivalDate, status);
                    continue;
                }
                Found hit = qualifyingRoom(res, c, minInventory, scanRooms);
                LOG.info(" .. [availability] {} on {} -> HTTP {}, best inventory "
                         + "{} in the first {} room(s)",
                         c.hcrs, c.arrivalDate, status,
                         maxInventory(res, scanRooms),
                         scanRooms > 0 ? scanRooms : "all");
                if (hit != null) {
                    return hit;
                }
            }
        }
        return null;
    }

    /**
     * The first room that clears the threshold, with the fields the
     * Groovy captures from it -- or null.
     *
     * <p>{@code scanRooms} is honoured because the Groovy writes
     * {@code for (t = 0; t < 6; t++)}: a 7th room with inventory does
     * not qualify there, so it must not qualify here either, or this
     * picks a combination ReadyAPI would have rejected.</p>
     */
    static Found qualifyingRoom(Response res, Found c, int minInventory,
                                int scanRooms) {
        List<Object> inv = roomField(res, "inventory");
        if (inv == null || inv.isEmpty()) {
            return null;
        }
        List<Object> codes = roomField(res, "roomTypeCode");
        int limit = scanRooms > 0 ? Math.min(scanRooms, inv.size()) : inv.size();
        for (int i = 0; i < limit; i++) {
            int v = asInt(inv.get(i));
            if (v <= minInventory) {
                continue;
            }
            String code = (codes != null && i < codes.size() && codes.get(i) != null)
                    ? String.valueOf(codes.get(i)) : "";
            return new Found(c.hcrs, c.pcrs, c.arrivalDate, c.departureDate,
                             true, scalar(res, "groupId"),
                             scalar(res, "cacheExpiryTime"), code,
                             Integer.toString(v));
        }
        return null;
    }

    /** Largest inventory within the scanned window, or -1. For the log. */
    static int maxInventory(Response res, int scanRooms) {
        List<Object> inv = roomField(res, "inventory");
        if (inv == null || inv.isEmpty()) {
            return -1;
        }
        int limit = scanRooms > 0 ? Math.min(scanRooms, inv.size()) : inv.size();
        int best = -1;
        for (int i = 0; i < limit; i++) {
            int v = asInt(inv.get(i));
            if (v > best) {
                best = v;
            }
        }
        return best;
    }

    private static List<Object> roomField(Response res, String leaf) {
        try {
            return res.jsonPath().getList("roomRates." + leaf);
        } catch (RuntimeException e) {
            return null;
        }
    }

    private static String scalar(Response res, String path) {
        try {
            Object o = res.jsonPath().get(path);
            return o == null ? "" : String.valueOf(o);
        } catch (RuntimeException e) {
            return "";
        }
    }

    private static int asInt(Object o) {
        if (o == null) {
            return -1;
        }
        if (o instanceof Number) {
            return ((Number) o).intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(o).trim());
        } catch (NumberFormatException e) {
            return -1;   // a non-numeric inventory is not a candidate
        }
    }

    private static Found candidate(List<String> hcrs, List<String> pcrs,
                                   List<Integer> offsets, int j, int i,
                                   boolean verified) {
        String h = hcrs.get(Math.min(j, hcrs.size() - 1));
        String p = (pcrs == null || pcrs.isEmpty())
                ? h : pcrs.get(Math.min(j, pcrs.size() - 1));
        int off = offsets.get(Math.min(i, offsets.size() - 1));
        java.time.LocalDate arr = java.time.LocalDate.now().plusDays(off);
        // The source scripts build departureDates as the day after each
        // arrival, every one of them.
        return new Found(h, p, arr.toString(), arr.plusDays(1).toString(),
                         verified);
    }

    private static void publish(Map<String, String> ctx, String propsStep,
                                String groupStep, Found f) {
        ImportedScenario.putExtracted(ctx, propsStep + ".hcrs", f.hcrs);
        ImportedScenario.putExtracted(ctx, propsStep + ".pcrs", f.pcrs);
        ImportedScenario.putExtracted(ctx, propsStep + ".arrivalDate",
                                      f.arrivalDate);
        ImportedScenario.putExtracted(ctx, propsStep + ".departureDate",
                                      f.departureDate);
        if (groupStep == null || groupStep.isEmpty()) {
            return;
        }
        // Only when the search actually won. Publishing empties here would
        // look like a resolved value and send `"groupId": ""`, which the
        // server rejects with a message about groupId rather than about
        // the search -- the unresolved placeholder at least names itself.
        if (!f.verified || f.groupId.isEmpty()) {
            return;
        }
        ImportedScenario.putExtracted(ctx, groupStep + ".groupId", f.groupId);
        ImportedScenario.putExtracted(ctx, groupStep + ".groupid", f.groupId);
        ImportedScenario.putExtracted(ctx, groupStep + ".cacheExpiryTime",
                                      f.cacheExpiryTime);
        ImportedScenario.putExtracted(ctx, groupStep + ".roomTypeCode",
                                      f.roomTypeCode);
        ImportedScenario.putExtracted(ctx, groupStep + ".inventoryCount",
                                      f.inventoryCount);
    }

    private static String cacheKey(String propsStep, List<String> hcrs,
                                   List<String> pcrs, List<Integer> offsets,
                                   int minInventory) {
        List<String> parts = new ArrayList<>();
        parts.add(propsStep);
        parts.add(String.join("|", hcrs));
        parts.add(pcrs == null ? "" : String.join("|", pcrs));
        Map<String, String> o = new LinkedHashMap<>();
        o.put("off", offsets.toString());
        parts.add(o.toString());
        parts.add(Integer.toString(minInventory));
        // The DATE matters: a winner found yesterday is about yesterday's
        // offsets, and a suite left running over midnight would otherwise
        // reuse dates that have shifted by a day.
        parts.add(java.time.LocalDate.now().toString());
        return String.join("##", parts);
    }
}
