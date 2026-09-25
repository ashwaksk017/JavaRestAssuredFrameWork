package com.hi.api.tests.framework;

import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import org.testng.Assert;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

import com.hi.api.rest.utilities.LastExchange;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * Contract for the per-thread response record every call path writes to.
 *
 * <p>No HTTP: {@code record} takes the Response it is handed, so a null
 * stands in for one. What is under test is the bookkeeping -- ordering,
 * isolation, the cap, the reset -- which is where a debugger-facing accessor
 * can quietly lie.</p>
 */
@Epic("Framework guards")
@Feature("LastExchange")
public class LastExchangeTest {

    @BeforeMethod(alwaysRun = true)
    public void clean() {
        LastExchange.clear();
    }

    @Test(groups = {"unit", "framework"})
    @Story("an empty record answers, rather than throwing")
    @Description("""
            Read before the first call -- which is exactly what a breakpoint
            on the first phase does -- must be a null, not an exception in
            the evaluate window.
            """)
    public void emptyRecordIsSafeToRead() {
        Assert.assertNull(LastExchange.response());
        Assert.assertNull(LastExchange.step());
        Assert.assertNull(LastExchange.last());
        Assert.assertEquals(LastExchange.status(), -1);
        Assert.assertEquals(LastExchange.body(), "");
        Assert.assertTrue(LastExchange.steps().isEmpty());
        Assert.assertNull(LastExchange.of("neverRan"));
    }

    @Test(groups = {"unit", "framework"})
    @Story("steps read back in call order")
    @Description("""
            steps() is meant to be eyeballed at a breakpoint as "what has
            happened so far", which is only true if it is in call order.
            """)
    public void stepsAreInCallOrder() {
        LastExchange.record("enrollGuest", "POST", "/guests", null);
        LastExchange.record("createAccount", "POST", "/accounts", null);
        LastExchange.record("readAccount", "GET", "/accounts/1", null);

        Assert.assertEquals(LastExchange.steps(),
                List.of("enrollGuest", "createAccount", "readAccount"));
        Assert.assertEquals(LastExchange.step(), "readAccount");
    }

    @Test(groups = {"unit", "framework"})
    @Story("a repeated step keeps its latest response, in the position it ran")
    @Description("""
            A chain that reads an account twice, or a step that retried, must
            report the call that counted -- and report it where it happened,
            not back at the first attempt's position.
            """)
    public void repeatedStepMovesToTheEndAndKeepsTheLatest() {
        LastExchange.record("readAccount", "GET", "/accounts/1", null);
        LastExchange.record("activate", "POST", "/accounts/1/activate", null);
        LastExchange.record("readAccount", "GET", "/accounts/1?v=2", null);

        Assert.assertEquals(LastExchange.steps(), List.of("activate", "readAccount"));
        Assert.assertEquals(LastExchange.last().uri(), "/accounts/1?v=2");
    }

    @Test(groups = {"unit", "framework"})
    @Story("the record is bounded")
    @Description("""
            A stuck or looping test must not hold response buffers without
            limit. The oldest entries go first, so the recent ones -- the
            only ones anyone reads at a breakpoint -- survive.
            """)
    public void oldestEntriesAreEvictedPastTheCap() {
        for (int i = 0; i < 80; i++) {
            LastExchange.record("step" + i, "GET", "/x/" + i, null);
        }
        List<String> steps = LastExchange.steps();
        Assert.assertEquals(steps.size(), 50);
        Assert.assertEquals(steps.get(steps.size() - 1), "step79");
        // Asserted through steps(), not of(): of() hands back the RESPONSE,
        // so "no such step" and "a step whose response was null" both read as
        // null -- and every response in this test is null.
        Assert.assertFalse(steps.contains("step0"), "oldest should be evicted");
        Assert.assertTrue(steps.contains("step79"));
    }

    @Test(groups = {"unit", "framework"})
    @Story("one test never reads another's responses")
    @Description("""
            TestNG pools threads, so without a per-thread record the next
            test would open on the previous test's calls. A stale 200 read at
            a breakpoint is worse than nothing: it looks like an answer.
            """)
    public void recordIsPerThread() throws Exception {
        LastExchange.record("mine", "GET", "/mine", null);

        AtomicReference<String> seen = new AtomicReference<>("unset");
        AtomicReference<Integer> count = new AtomicReference<>(-1);
        CountDownLatch done = new CountDownLatch(1);
        Thread other = new Thread(() -> {
            seen.set(String.valueOf(LastExchange.step()));
            count.set(LastExchange.steps().size());
            done.countDown();
        });
        other.start();
        Assert.assertTrue(done.await(5, TimeUnit.SECONDS), "helper thread hung");

        Assert.assertEquals(seen.get(), "null", "another thread saw our step");
        Assert.assertEquals(count.get().intValue(), 0);
        Assert.assertEquals(LastExchange.step(), "mine", "our own record survived");
    }

    @Test(groups = {"unit", "framework"})
    @Story("clear resets everything, including the borrowed name")
    @Description("""
            BaseApiTest's @BeforeMethod calls clear(). A leftover pending
            step name would misfile the NEXT test's first call under the
            previous test's step.
            """)
    public void clearResetsTheBorrowedName() {
        LastExchange.naming("enrollGuest");
        LastExchange.record("enrollGuest", "POST", "/guests", null);
        Assert.assertEquals(LastExchange.pendingStep(), "enrollGuest");

        LastExchange.clear();

        Assert.assertNull(LastExchange.pendingStep());
        Assert.assertNull(LastExchange.step());
        Assert.assertTrue(LastExchange.steps().isEmpty());
    }

    @Test(groups = {"unit", "framework"})
    @Story("two producers of one exchange make one entry")
    @Description("""
            The recording filter records a call as it leaves and RestStep
            re-records it once it has settled. Without the borrowed name they
            would file the same call twice under two keys, and steps() would
            read as double the calls that were made.
            """)
    public void filterAndRestStepShareOneEntry() {
        LastExchange.naming("createAccount");
        // as the filter sees it: borrowed name, first attempt
        LastExchange.record(LastExchange.pendingStep(), "POST", "/accounts", null);
        // as RestStep sees it: same name, settled response
        LastExchange.record("createAccount", "POST", "/accounts", null);
        LastExchange.doneNaming();

        Assert.assertEquals(LastExchange.steps(), List.of("createAccount"));
        Assert.assertNull(LastExchange.pendingStep());
    }

    @Test(groups = {"unit", "framework"})
    @Story("a call outside a step is still recorded")
    @Description("""
            A legacy-style test calls RestUtilities directly and never
            touches RestStep, so there is no step name to borrow. It must
            still be reachable -- that path is the reason the filter records
            at all.
            """)
    public void unnamedCallsAreStillReachable() {
        Assert.assertNull(LastExchange.pendingStep(), "no step is in flight");
        LastExchange.record(null, "POST", "/program-accounts", null);

        Assert.assertEquals(LastExchange.steps(), List.of("(unnamed)"));
        Assert.assertNotNull(LastExchange.last());
        Assert.assertEquals(LastExchange.last().method(), "POST");
    }
}
