package com.ak.api.dsl;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.testng.Assert;
import org.testng.annotations.Test;

import io.qameta.allure.Description;
import io.qameta.allure.Epic;
import io.qameta.allure.Feature;
import io.qameta.allure.Story;

/**
 * A stage never hands back a weaker stage.
 *
 * <p>{@link OnboardingFlow} narrows what the IDE offers by returning the
 * stage each phase unlocks. That only holds if every method reachable on a
 * stage returns THAT stage or deeper. Miss one covariant override and the
 * chain silently falls back a level: {@code .createH4BAccount().db(...)}
 * would return {@code Start}, and the next {@code .activateProgramAccount()}
 * stops compiling for a reason that points at the wrong line.</p>
 *
 * <p>Checked by reflection rather than by listing method names, so a phase
 * or modifier added later is covered without anyone remembering to add it
 * here. That is the actual failure mode: {@code db(String)} was added to
 * all three interfaces by hand, and nothing but this test would have caught
 * an omission in one of them.</p>
 */
@Epic("Framework")
@Feature("Onboarding DSL staging")
public class OnboardingStageContractTest {

    /** Narrowest first, so a stage's own name reads in failure messages. */
    private static final Class<?>[] STAGES = {
            OnboardingFlow.AccountReady.class,
            OnboardingFlow.Enrolled.class,
            OnboardingFlow.Start.class,
    };

    private static String sig(Method m) {
        StringBuilder sb = new StringBuilder(m.getName()).append('(');
        Class<?>[] ps = m.getParameterTypes();
        for (int i = 0; i < ps.length; i++) {
            sb.append(i == 0 ? "" : ", ").append(ps[i].getSimpleName());
        }
        return sb.append(')').toString();
    }

    /**
     * Most specific return type per signature. getMethods() reports BOTH
     * sides of a covariant override, so picking either arbitrarily would
     * make this test pass or fail on JVM method ordering.
     */
    private static Map<String, Class<?>> narrowestReturns(Class<?> stage) {
        Map<String, Class<?>> best = new LinkedHashMap<>();
        for (Method m : stage.getMethods()) {
            String key = sig(m);
            Class<?> prev = best.get(key);
            if (prev == null || prev.isAssignableFrom(m.getReturnType())) {
                best.put(key, m.getReturnType());
            }
        }
        return best;
    }

    @Test(groups = {"unit", "dsl"})
    @Story("every method on a stage returns that stage or deeper")
    @Description("""
            A covariant override missing from one interface silently drops
            the chain to an earlier stage.
            """)
    public void noMethodReturnsAWeakerStage() {
        List<String> problems = new ArrayList<>();
        for (Class<?> stage : STAGES) {
            for (Map.Entry<String, Class<?>> e : narrowestReturns(stage).entrySet()) {
                Class<?> ret = e.getValue();
                if (ret == void.class) {
                    continue;              // complete()
                }
                if (!ret.isAssignableFrom(stage) && !stage.isAssignableFrom(ret)) {
                    problems.add(stage.getSimpleName() + "." + e.getKey()
                            + " returns unrelated " + ret.getSimpleName());
                    continue;
                }
                // returning a SUPERtype is the bug: the chain loses a stage
                if (ret.isAssignableFrom(stage) && !ret.equals(stage)) {
                    problems.add(stage.getSimpleName() + "." + e.getKey()
                            + " returns " + ret.getSimpleName()
                            + " -- weaker than " + stage.getSimpleName()
                            + "; add a covariant @Override on "
                            + stage.getSimpleName());
                }
            }
        }
        Assert.assertTrue(problems.isEmpty(),
                "stage(s) hand back a weaker type:\n  " + String.join("\n  ", problems));
    }

    @Test(groups = {"unit", "dsl"})
    @Story("CustomerOnboarding implements the deepest stage")
    public void theImplementationSatisfiesEveryStage() {
        Assert.assertTrue(
                OnboardingFlow.AccountReady.class.isAssignableFrom(CustomerOnboarding.class),
                "CustomerOnboarding must implement AccountReady so `return this;` "
                + "satisfies every stage declaration");
    }

    @Test(groups = {"unit", "dsl"})
    @Story("the entry point hands out the first stage, not the class")
    @Description("""
            Returning CustomerOnboarding from onboarding()/start() would offer
            all 22 phases from the first keystroke and defeat the staging.
            """)
    public void entryPointsReturnStart() throws Exception {
        Assert.assertEquals(
                MasterClass.class.getMethod("onboarding", Map.class).getReturnType(),
                OnboardingFlow.Start.class, "MasterClass.onboarding must return Start");
        Assert.assertEquals(
                CustomerOnboarding.class.getMethod("start", Map.class).getReturnType(),
                OnboardingFlow.Start.class, "CustomerOnboarding.start must return Start");
    }

    @Test(groups = {"unit", "dsl"})
    @Story("a DB step is available at every stage")
    @Description("""
            Converted cases write to the database between requests -- account
            status before activate, seeded rows before enrol -- so db() must
            not be gated behind a stage.
            """)
    public void dbIsAvailableAtEveryStage() throws Exception {
        for (Class<?> stage : STAGES) {
            Method m = stage.getMethod("db", String.class);
            Assert.assertEquals(m.getReturnType(), stage,
                    stage.getSimpleName() + ".db(String) must return "
                    + stage.getSimpleName());
        }
    }

    @Test(groups = {"unit", "dsl"})
    @Story("stages are cumulative")
    public void stagesAreCumulative() {
        Assert.assertTrue(
                OnboardingFlow.Start.class.isAssignableFrom(OnboardingFlow.Enrolled.class),
                "Enrolled must extend Start");
        Assert.assertTrue(
                OnboardingFlow.Enrolled.class.isAssignableFrom(
                        OnboardingFlow.AccountReady.class),
                "AccountReady must extend Enrolled");
        // Everything Start offers is still reachable at the deepest stage.
        List<String> startSigs = new ArrayList<>(
                narrowestReturns(OnboardingFlow.Start.class).keySet());
        List<String> deepSigs = new ArrayList<>(
                narrowestReturns(OnboardingFlow.AccountReady.class).keySet());
        List<String> lost = new ArrayList<>(startSigs);
        lost.removeAll(deepSigs);
        Assert.assertTrue(lost.isEmpty(),
                "AccountReady lost method(s) Start had: " + Arrays.toString(lost.toArray()));
    }
}
