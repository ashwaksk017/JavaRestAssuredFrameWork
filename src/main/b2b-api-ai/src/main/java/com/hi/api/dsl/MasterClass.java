package com.hi.api.dsl;

import java.util.Map;

import com.hi.api.domain.DomainApis;
import com.hi.api.domain.account.ProgramAccountApi;
import com.hi.api.domain.guest.GuestApi;
import com.hi.api.domain.member.MemberApi;
import com.hi.api.support.ImportedRestClient;
import com.hi.api.support.ImportedScenario;

/**
 * One entry point for hand-written tests.
 *
 * <pre>
 * MasterClass.onboarding(row)
 *     .enrollOwner()
 *     .createH4LAccount()
 *     .complete();
 *
 * // or drop to a single request, when no story phase fits:
 * Response res = MasterClass.members()
 *         .readProgramAccountMember(token, guestId, accountId, memberId);
 * </pre>
 *
 * <h2>What this is and is not</h2>
 *
 * This is a front door, not a new layer. Every call below forwards to
 * something that already existed: {@link CustomerOnboarding} for story
 * phases, and the {@link DomainApis} facades for the 75 individual
 * request types. It exists so an author needs one import and does not
 * have to know that the client is reached through the bound session.
 *
 * <p>Deliberately NOT bound to generated phase names. The converter's
 * {@code ScenarioSteps} has 512 methods whose numeric suffixes are
 * cluster-derived and move between runs -- {@code enrollGuest2} became
 * {@code enrollGuest52} within a single day of reconverts. Anything
 * reachable from here is either hand-written or a stable client method,
 * so a reconvert cannot silently change what a test calls.</p>
 *
 * <p>Requires {@code ImportedScenario.bind(...)} to have run, which
 * {@code BaseApiTest} subclasses do in {@code @BeforeMethod}.</p>
 */
public final class MasterClass {

    private MasterClass() {}

    /**
     * Start a story chain for one CSV row.
     *
     * <p>Returns the {@link OnboardingFlow.Start} stage, so completion
     * offers only the phases that are legal before anything is published --
     * {@code createH4BAccount()} appears once a guest is enrolled, and
     * {@code confirmOwner()} once an account exists. Ordering mistakes are
     * compile errors rather than a {@code required(...)} throw at run time.</p>
     *
     * @see CustomerOnboarding
     * @see OnboardingFlow
     */
    public static OnboardingFlow.Start onboarding(Map<String, String> row) {
        return CustomerOnboarding.start(row);
    }

    /** Guest / HHonors enrolment requests. */
    public static GuestApi guests() {
        return apis().guests();
    }

    /** Program-account requests. */
    public static ProgramAccountApi accounts() {
        return apis().accounts();
    }

    /** Account-member requests. */
    public static MemberApi members() {
        return apis().members();
    }

    /**
     * The raw generated client, for a request none of the facades name.
     *
     * <p>Returned as {@code ImportedRestClient} -- the suite-agnostic
     * interface -- never as a suite's {@code *Client}, so this file stays
     * compilable no matter which XML was converted.</p>
     */
    public static ImportedRestClient client() {
        return apis().client();
    }

    /** Facades bound over the client on this thread's session. */
    public static DomainApis apis() {
        Object client = ImportedScenario.current().client;
        if (!(client instanceof ImportedRestClient)) {
            throw new IllegalStateException(
                    "The bound client is "
                    + (client == null ? "null" : client.getClass().getName())
                    + ", which does not implement ImportedRestClient. "
                    + "Bind a generated suite client in @BeforeClass.");
        }
        return DomainApis.bind((ImportedRestClient) client);
    }
}
