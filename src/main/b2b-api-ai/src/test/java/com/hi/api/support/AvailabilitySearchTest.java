package com.hi.api.support;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import io.qameta.allure.Feature;
import io.qameta.allure.Story;
import io.restassured.builder.ResponseBuilder;
import io.restassured.http.ContentType;
import io.restassured.response.Response;

/**
 * The availability search picks a property/date pair that has rooms.
 *
 * <p>The converter used to emit only the FIRST candidate and warn that
 * the search had not run (165 warnings in one run). That is a different
 * test: if the first candidate has no availability, every downstream
 * call is built on a property and date with no rooms, and because the
 * requests are well-formed the failures read like assertion problems
 * rather than a missing search.</p>
 */
@Feature("AvailabilitySearch")
public class AvailabilitySearchTest {

    @BeforeMethod(alwaysRun = true)
    public void clear() {
        AvailabilitySearch.resetCacheForTest();
        System.clearProperty("rest.availabilitySearch.enabled");
        System.clearProperty("rest.availabilitySearch.perCase");
        System.clearProperty("rest.availabilitySearch.maxProbes");
    }

    private static Response json(int status, String body) {
        return new ResponseBuilder().setStatusCode(status)
                .setContentType(ContentType.JSON).setBody(body).build();
    }

    private static final String NO_ROOMS = "{\"roomRates\":[]}";

    private static String rooms(int inventory) {
        return "{\"roomRates\":[{\"roomTypeCode\":\"K1S\",\"inventory\":"
                + inventory + "}]}";
    }

    @Test(groups = {"unit"})
    @Story("The first combination with inventory above the threshold wins")
    public void itSkipsPastPropertiesWithNoRooms() {
        Map<String, String> ctx = new HashMap<>();
        List<String> tried = new ArrayList<>();
        boolean ok = AvailabilitySearch.run(ctx, "probe", "gen",
                List.of("AAA", "BBB", "CCC"), List.of("A1", "B1", "C1"),
                List.of(10), 9,
                (p, a, d) -> {
                    tried.add(p);
                    return json(200, "BBB".equals(p) ? rooms(25) : NO_ROOMS);
                });
        Assert.assertTrue(ok, "BBB has rooms, so the search succeeded");
        Assert.assertEquals(tried, List.of("AAA", "BBB"),
                "must stop at the winner, not probe the whole grid");
        Assert.assertEquals(ctx.get("gen.hcrs"), "BBB");
        Assert.assertEquals(ctx.get("gen.pcrs"), "B1",
                "the paired property travels with its hcrs, by index");
    }

    @Test(groups = {"unit"})
    @Story("Inventory must EXCEED the threshold, not merely equal it")
    public void thresholdIsExclusive() {
        Map<String, String> ctx = new HashMap<>();
        boolean ok = AvailabilitySearch.run(ctx, "probe", "gen",
                List.of("AAA"), List.of("A1"), List.of(10), 9,
                (p, a, d) -> json(200, rooms(9)));
        Assert.assertFalse(ok, "the Groovy is `inventory > 9`, so 9 is not enough");
    }

    @Test(groups = {"unit"})
    @Story("A departure date is the day after its arrival")
    public void departureIsTheDayAfterArrival() {
        Map<String, String> ctx = new HashMap<>();
        AvailabilitySearch.run(ctx, "probe", "gen", List.of("AAA"), List.of(),
                List.of(34), 0, (p, a, d) -> json(200, rooms(50)));
        java.time.LocalDate arr = java.time.LocalDate.now().plusDays(34);
        Assert.assertEquals(ctx.get("gen.arrivalDate"), arr.toString());
        Assert.assertEquals(ctx.get("gen.departureDate"),
                arr.plusDays(1).toString());
    }

    @Test(groups = {"unit"})
    @Story("Finding nothing still publishes a candidate, so nothing dangles")
    public void aFailedSearchStillPublishes() {
        Map<String, String> ctx = new HashMap<>();
        boolean ok = AvailabilitySearch.run(ctx, "probe", "gen",
                List.of("AAA", "BBB"), List.of(), List.of(10, 20), 9,
                (p, a, d) -> json(200, NO_ROOMS));
        Assert.assertFalse(ok);
        // Absent keys would turn every downstream `#gen_arrivalDate#` into
        // literal text -- a worse and far less legible failure than a date
        // that happens to have no rooms.
        Assert.assertEquals(ctx.get("gen.hcrs"), "AAA");
        Assert.assertNotNull(ctx.get("gen.arrivalDate"));
        Assert.assertNotNull(ctx.get("gen.departureDate"));
    }

    @Test(groups = {"unit"})
    @Story("A non-2xx probe is skipped, not read as 'no rooms'")
    public void aRejectedProbeDoesNotEndTheSearch() {
        Map<String, String> ctx = new HashMap<>();
        boolean ok = AvailabilitySearch.run(ctx, "probe", "gen",
                List.of("AAA", "BBB"), List.of(), List.of(10), 9,
                (p, a, d) -> "AAA".equals(p)
                        ? json(400, "{\"message\":\"Bad Request\"}")
                        : json(200, rooms(20)));
        Assert.assertTrue(ok);
        Assert.assertEquals(ctx.get("gen.hcrs"), "BBB");
    }

    @Test(groups = {"unit"})
    @Story("A probe that throws is skipped, not fatal")
    public void aThrowingProbeIsSkipped() {
        Map<String, String> ctx = new HashMap<>();
        boolean ok = AvailabilitySearch.run(ctx, "probe", "gen",
                List.of("AAA", "BBB"), List.of(), List.of(10), 9,
                (p, a, d) -> {
                    if ("AAA".equals(p)) {
                        throw new IllegalStateException("connection reset");
                    }
                    return json(200, rooms(20));
                });
        Assert.assertTrue(ok, "a lookup must not fail the test it feeds");
        Assert.assertEquals(ctx.get("gen.hcrs"), "BBB");
    }

    @Test(groups = {"unit"})
    @Story("The answer is resolved once per run, not once per case")
    public void theWinnerIsCachedForTheRun() {
        int[] calls = {0};
        AvailabilitySearch.Probe probe = (p, a, d) -> {
            calls[0]++;
            return json(200, "CCC".equals(p) ? rooms(30) : NO_ROOMS);
        };
        List<String> h = List.of("AAA", "BBB", "CCC");
        Map<String, String> first = new HashMap<>();
        AvailabilitySearch.run(first, "probe", "gen", h, List.of(),
                List.of(10), 9, probe);
        int afterFirst = calls[0];
        Assert.assertEquals(afterFirst, 3, "walked AAA, BBB, CCC");

        Map<String, String> second = new HashMap<>();
        AvailabilitySearch.run(second, "probe", "gen", h, List.of(),
                List.of(10), 9, probe);
        Assert.assertEquals(calls[0], afterFirst,
                "the second case must reuse the run's answer: 40 probes per "
                + "case across 730 tests would dominate the run");
        Assert.assertEquals(second.get("gen.hcrs"), "CCC",
                "and still publish it");
    }

    @Test(groups = {"unit"})
    @Story("perCase=true searches again for every case")
    public void perCaseOptsOutOfTheCache() {
        System.setProperty("rest.availabilitySearch.perCase", "true");
        int[] calls = {0};
        AvailabilitySearch.Probe probe = (p, a, d) -> {
            calls[0]++;
            return json(200, rooms(30));
        };
        AvailabilitySearch.run(new HashMap<>(), "probe", "gen", List.of("AAA"),
                List.of(), List.of(10), 9, probe);
        AvailabilitySearch.run(new HashMap<>(), "probe", "gen", List.of("AAA"),
                List.of(), List.of(10), 9, probe);
        Assert.assertEquals(calls[0], 2);
    }

    @Test(groups = {"unit"})
    @Story("enabled=false publishes the first candidate without probing")
    public void disabledDoesNotProbe() {
        System.setProperty("rest.availabilitySearch.enabled", "false");
        Map<String, String> ctx = new HashMap<>();
        int[] calls = {0};
        boolean ok = AvailabilitySearch.run(ctx, "probe", "gen",
                List.of("AAA", "BBB"), List.of(), List.of(10), 9,
                (p, a, d) -> {
                    calls[0]++;
                    return json(200, rooms(30));
                });
        Assert.assertFalse(ok);
        Assert.assertEquals(calls[0], 0, "no HTTP when disabled");
        Assert.assertEquals(ctx.get("gen.hcrs"), "AAA");
    }

    @Test(groups = {"unit"})
    @Story("maxProbes bounds a first run against an unknown environment")
    public void maxProbesBoundsTheGrid() {
        System.setProperty("rest.availabilitySearch.maxProbes", "2");
        int[] calls = {0};
        Map<String, String> ctx = new HashMap<>();
        boolean ok = AvailabilitySearch.run(ctx, "probe", "gen",
                List.of("AAA", "BBB", "CCC"), List.of(), List.of(10), 9,
                (p, a, d) -> {
                    calls[0]++;
                    return json(200, "CCC".equals(p) ? rooms(30) : NO_ROOMS);
                });
        Assert.assertEquals(calls[0], 2, "stopped at the cap");
        Assert.assertFalse(ok);
        Assert.assertNotNull(ctx.get("gen.arrivalDate"), "still publishes");
    }

    @Test(groups = {"unit"})
    @Story("maxInventory reads the largest room inventory, or -1")
    public void maxInventoryReadsTheBody() {
        Assert.assertEquals(AvailabilitySearch.maxInventory(
                json(200, "{\"roomRates\":[{\"inventory\":4},"
                        + "{\"inventory\":26},{\"inventory\":8}]}")), 26);
        Assert.assertEquals(AvailabilitySearch.maxInventory(
                json(200, NO_ROOMS)), -1);
        Assert.assertEquals(AvailabilitySearch.maxInventory(
                json(200, "{}")), -1);
        Assert.assertEquals(AvailabilitySearch.maxInventory(
                json(400, "not json at all")), -1,
                "an unparseable body is not a candidate, and must not throw");
    }

    @Test(groups = {"unit"})
    @Story("A null ctx or empty candidate list is a no-op, not a crash")
    public void degenerateInputsAreSafe() {
        Assert.assertFalse(AvailabilitySearch.run(null, "p", "gen",
                List.of("A"), List.of(), List.of(1), 0,
                (a, b, c) -> json(200, rooms(99))));
        Assert.assertFalse(AvailabilitySearch.run(new HashMap<>(), "p", "gen",
                List.of(), List.of(), List.of(1), 0,
                (a, b, c) -> json(200, rooms(99))));
    }
}
