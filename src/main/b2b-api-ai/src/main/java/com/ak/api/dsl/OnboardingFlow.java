package com.ak.api.dsl;

import com.ak.api.context.ScenarioContext;

/**
 * The onboarding chain as three stages, so the IDE offers only the phases
 * that are legal at the point you are typing.
 *
 * <pre>
 * MasterClass.onboarding(row)   // Start       -- only enrol / travel agency / salesforce
 *     .enrollOwner()            // Enrolled    -- guestId now published
 *     .createH4BAccount()       // AccountReady-- accountId + owner memberId now published
 *     .confirmOwner()
 *     .complete();
 * </pre>
 *
 * <h2>Why this exists</h2>
 *
 * {@link CustomerOnboarding} enforces order at RUNTIME: every id goes through
 * {@code required(...)}, which throws naming the rule ("An earlier phase must
 * publish it, e.g. enrollOwner() before createH4LAccount()"). That is a good
 * error, but you only see it once the test runs against a live environment.
 * Completion on a flat builder offers all 22 phases from the first keystroke,
 * so the wrong order is equally easy to type.
 *
 * <p>Here each phase returns the stage its response unlocks, so
 * {@code createH4BAccount()} is not offered until a guest exists and
 * {@code confirmOwner()} is not offered until an account does. Ordering
 * mistakes become compile errors instead of runtime ones.</p>
 *
 * <h2>Stages are cumulative</h2>
 *
 * {@code AccountReady extends Enrolled extends Start}, so nothing is ever
 * taken away -- enrolling a second guest after the account exists stays
 * legal, which the converted ReadyAPI cases do (HHonorsEnroll followed by
 * MemberHHonorsEnroll). The modifiers ({@code using}, {@code capture},
 * {@code expect*}) and {@code complete()} are on every stage, so
 * {@code .capture(...)} before the first phase still works.
 *
 * <h2>What publishes what</h2>
 *
 * <ul>
 *   <li>{@code enroll*} -> {@code guestId}</li>
 *   <li>{@code create*Account} -> {@code accountId} AND the owner's
 *       {@code memberId}: {@code POST /guests/{guestId}/businesses} returns
 *       both. That is why {@code confirmOwner()} is legal straight after
 *       account creation with no {@code addMember()} in between.</li>
 *   <li>{@code addMember*} -> that role's {@code memberId}</li>
 * </ul>
 *
 * <p>Implemented by {@link CustomerOnboarding}, which still declares every
 * method returning itself; these interfaces only narrow what each stage
 * hands back.</p>
 */
public final class OnboardingFlow {

    private OnboardingFlow() {
    }

    /** Nothing published yet: enrol a guest, or do the environment setup. */
    public interface Start {

        // -- modifiers: apply to the NEXT phase, never advance the stage ---

        Start using(Template template);

        Start capture(String jsonPath, String ctxKey);

        Start capture(String jsonPath, ScenarioContext.Field field);

        Start expectJson(String jsonPath, String expected);

        Start expectExists(String jsonPath);

        Start expectAbsent(String jsonPath);

        Start expectCount(String jsonPath, int count);

        Start expectHeader(String headerName);

        Start expectBodyContains(String expected);

        Start expectBodyContains(String jsonPath, String expected);

        Start expectSubstring(String jsonPath, String expected);

        Start expectJsonTree(String jsonPath, String expectedJson);

        Start expectCaptured(String ctxKey, String expected);

        // -- phases that need nothing published ---------------------------

        /** Needs {@code travelAgentId} from the CSV row, not from a phase. */
        Start createTravelAgency();

        /** Salesforce token + account + distribution. */
        Start prepareSalesforceAccount();

        // -- phases that publish guestId and open the next stage ----------

        Enrolled enrollGuest(CustomerOnboarding.Role role);

        Enrolled enrollOwner();

        Enrolled enrollTravelAdvisor();

        Enrolled enrollEmployee();

        /** Flush soft assertions. Call at the end of every chain. */
        void complete();
    }

    /** A guest exists: {@code guestId} is published, so an account can be created. */
    public interface Enrolled extends Start {

        @Override Enrolled using(Template template);

        @Override Enrolled capture(String jsonPath, String ctxKey);

        @Override Enrolled capture(String jsonPath, ScenarioContext.Field field);

        @Override Enrolled expectJson(String jsonPath, String expected);

        @Override Enrolled expectExists(String jsonPath);

        @Override Enrolled expectAbsent(String jsonPath);

        @Override Enrolled expectCount(String jsonPath, int count);

        @Override Enrolled expectHeader(String headerName);

        @Override Enrolled expectBodyContains(String expected);

        @Override Enrolled expectBodyContains(String jsonPath, String expected);

        @Override Enrolled expectSubstring(String jsonPath, String expected);

        @Override Enrolled expectJsonTree(String jsonPath, String expectedJson);

        @Override Enrolled expectCaptured(String ctxKey, String expected);

        @Override Enrolled createTravelAgency();

        @Override Enrolled prepareSalesforceAccount();

        // -- publishes accountId + the owner's memberId -------------------

        AccountReady createProgramAccount(CustomerOnboarding.Partner partner);

        AccountReady createH4LAccount();

        AccountReady createH4BAccount();

        AccountReady createLTAAccount();

        AccountReady createSmbAccount();
    }

    /**
     * An account exists: {@code accountId} and the owner's {@code memberId}
     * are published, so members, activation and the read-backs are legal.
     */
    public interface AccountReady extends Enrolled {

        @Override AccountReady using(Template template);

        @Override AccountReady capture(String jsonPath, String ctxKey);

        @Override AccountReady capture(String jsonPath, ScenarioContext.Field field);

        @Override AccountReady expectJson(String jsonPath, String expected);

        @Override AccountReady expectExists(String jsonPath);

        @Override AccountReady expectAbsent(String jsonPath);

        @Override AccountReady expectCount(String jsonPath, int count);

        @Override AccountReady expectHeader(String headerName);

        @Override AccountReady expectBodyContains(String expected);

        @Override AccountReady expectBodyContains(String jsonPath, String expected);

        @Override AccountReady expectSubstring(String jsonPath, String expected);

        @Override AccountReady expectJsonTree(String jsonPath, String expectedJson);

        @Override AccountReady expectCaptured(String ctxKey, String expected);

        @Override AccountReady createTravelAgency();

        @Override AccountReady prepareSalesforceAccount();

        // Re-enrolling once an account exists keeps the account: the
        // converted cases do exactly this (HHonorsEnroll, then
        // MemberHHonorsEnroll) before adding the member.
        @Override AccountReady enrollGuest(CustomerOnboarding.Role role);

        @Override AccountReady enrollOwner();

        @Override AccountReady enrollTravelAdvisor();

        @Override AccountReady enrollEmployee();

        // -- account reads and state changes ------------------------------

        AccountReady readProgramAccount();

        AccountReady activateProgramAccount();

        AccountReady activateThroughHws();

        AccountReady verifySynchronization();

        // -- members -------------------------------------------------------

        /** Publishes that role's {@code memberId}. */
        AccountReady addMember(CustomerOnboarding.Role role);

        AccountReady addTravelAdvisor();

        AccountReady addEmployee();

        AccountReady confirmMemberTotp(CustomerOnboarding.Role role);

        AccountReady confirmOwner();

        AccountReady confirmTravelAdvisor();

        AccountReady readAccountMember();
    }
}
