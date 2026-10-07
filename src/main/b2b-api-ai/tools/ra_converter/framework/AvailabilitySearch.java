package com.hi.api.support;

// ra_converter-framework-rev: 1
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

    /** One winning combination. */
    public static final class Found {
        public final String hcrs;
        public final String pcrs;
        public final String arrivalDate;
        public final String departureDate;
        public final boolean verified;

        Found(String hcrs, String pcrs, String arrivalDate,
              String departureDate, boolean verified) {
            this.hcrs = hcrs;
            this.pcrs = pcrs;
            this.arrivalDate = arrivalDate;
            this.departureDate = departureDate;
            this.verified = verified;
        }
    }

    private static final Map<String, Found> CACHE = new ConcurrentHashMap<>();

    private AvailabilitySearch() {}

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
    public static boolean run(Map<String, String> ctx, String stepName,
                              String propsStep, List<String> hcrs,
                              List<String> pcrs, List<Integer> offsets,
                              int minInventory, Probe probe) {
        if (ctx == null || hcrs == null || hcrs.isEmpty()
                || offsets == null || offsets.isEmpty()) {
            return false;
        }
        String props = propsStep == null || propsStep.isEmpty()
                ? "generatedDatesAndProps" : propsStep;
        Found first = candidate(hcrs, pcrs, offsets, 0, 0, false);

        if (!Config.getBool("rest.availabilitySearch.enabled", true)
                || probe == null) {
            publish(ctx, props, first);
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
            if (cached != null) {
                publish(ctx, props, cached);
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
                              probe, cap);
        if (winner == null) {
            winner = first;
            LOG.warn(" .. [availability] no combination for `{}` had inventory > "
                     + "{} after {} probe(s). Publishing the first candidate {} "
                     + "on {} unverified -- a downstream 'no rooms' answer here "
                     + "is the ENVIRONMENT, not the test data.",
                     stepName, minInventory, cap, first.hcrs, first.arrivalDate);
        } else {
            LOG.info(" .. [availability] `{}` -> {} on {}..{} (inventory > {})",
                     stepName, winner.hcrs, winner.arrivalDate,
                     winner.departureDate, minInventory);
        }
        if (!perCase) {
            CACHE.put(key, winner);
        }
        publish(ctx, props, winner);
        return winner.verified;
    }

    private static Found search(String stepName, List<String> hcrs,
                                List<String> pcrs, List<Integer> offsets,
                                int minInventory, Probe probe, int cap) {
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
                int best = maxInventory(res);
                LOG.info(" .. [availability] {} on {} -> HTTP {}, best inventory {}",
                         c.hcrs, c.arrivalDate, status, best);
                if (best > minInventory) {
                    return c;
                }
            }
        }
        return null;
    }

    /** Largest {@code roomRates[*].inventory} in the body, or -1. */
    static int maxInventory(Response res) {
        List<Object> raw;
        try {
            raw = res.jsonPath().getList("roomRates.inventory");
        } catch (RuntimeException e) {
            return -1;
        }
        if (raw == null || raw.isEmpty()) {
            return -1;
        }
        int best = -1;
        for (Object o : raw) {
            if (o == null) {
                continue;
            }
            try {
                int v = (o instanceof Number)
                        ? ((Number) o).intValue()
                        : Integer.parseInt(String.valueOf(o).trim());
                if (v > best) {
                    best = v;
                }
            } catch (NumberFormatException ignored) {
                // a non-numeric inventory is not a candidate
            }
        }
        return best;
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
                                Found f) {
        ImportedScenario.putExtracted(ctx, propsStep + ".hcrs", f.hcrs);
        ImportedScenario.putExtracted(ctx, propsStep + ".pcrs", f.pcrs);
        ImportedScenario.putExtracted(ctx, propsStep + ".arrivalDate",
                                      f.arrivalDate);
        ImportedScenario.putExtracted(ctx, propsStep + ".departureDate",
                                      f.departureDate);
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
