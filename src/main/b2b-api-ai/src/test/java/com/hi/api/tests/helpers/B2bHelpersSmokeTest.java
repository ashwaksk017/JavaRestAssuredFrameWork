package com.hi.api.tests.helpers;

import java.util.LinkedHashMap;
import java.util.Map;

import org.testng.annotations.Test;

import com.hi.api.tests.StoryTestBase;

/**
 * Every B2B story helper, once, against a real environment, in the order the
 * converted chain makes the calls (h4bbookonbehalf StaysTCStayTestPhases):
 * service token, enrol the owner, create the account, the owner's one-time
 * code from the database and its confirm, activate the account, its invite
 * key, enrol a member, add it, activate it, give it a role.
 *
 * <p>Why: the story helpers are shared code that only story tests run, and a
 * story only meets a broken helper after AI Agent has written it -- seen on
 * b2b-stg: the one-time code was read with the id bound as text, the column is
 * a bigint, and every story's owner confirm failed. The converted suites make
 * the same calls their own way and never showed it. Run this after a change to
 * StoryTestBase, Db, JsonTokens or the configuration, and on a new environment
 * -- {@code src/test/resources/testng-helpers-b2b.xml} on the Run tests tab.
 * The first step that fails is named, and nothing after it runs.</p>
 *
 * <p>It creates what a story does: two guests and a program account.</p>
 */
public class B2bHelpersSmokeTest extends StoryTestBase {

    private static final String TAG = "SMOKE";
    private static final String ENROL = "templates/manual/b2b_enroll_guest.json";
    private static final String ACCOUNT = "templates/manual/b2b_create_program_account.json";

    @Test(groups = {"helpers-smoke"}, description = "every B2B story helper, in the converted chain's order")
    public void everyHelperWorksAgainstThisEnvironment() throws Exception {
        // Before anything is made: no database, no half-made account.
        requireDb("read the owner's one-time code (segment.account_member_internal_security)");
        String token = serviceToken();
        Map<String, String> row = new LinkedHashMap<>();
        Actor owner = enrollActor(row, ENROL, TAG);
        if (owner == null) {
            return;
        }
        String[] account = createAccountFor(owner, ACCOUNT, token, TAG);
        if (account == null) {
            return;
        }
        String accountId = account[0];
        softAssert.assertEquals(owner.memberId, account[1], TAG + "/SETUP: the owner's memberId is kept on the owner");
        if (!confirmOwnerValidation(owner, accountId, account[1], token, TAG)) {
            return;
        }
        if (!activateAccount(accountId, token, TAG)) {
            return;
        }
        String invite = inviteKey(accountId, token, TAG);
        if (invite == null) {
            return;
        }
        Actor member = enrollActor(row, ENROL, TAG);
        if (member == null) {
            return;
        }
        if (addMember(member, accountId, "employee", invite, token, TAG) == null) {
            return;
        }
        if (!activateMember(accountId, member.memberId, token, TAG)) {
            return;
        }
        setMemberRole(owner, accountId, member, "travelCoordinator", token, TAG);
    }
}
