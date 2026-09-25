package com.hi.api.domain.account;

import java.util.Map;

import com.hi.api.openapi.OpenApiModels;
import com.hi.api.support.ImportedRestClient;

import io.restassured.response.Response;

/** Program-account (business) operations. Imported flows call this instead of the fat client. */
public final class ProgramAccountApi {

    private final ImportedRestClient client;

    public ProgramAccountApi(ImportedRestClient client) {
        this.client = client;
    }

    public ImportedRestClient raw() {
        return client;
    }

    public Response activateProgramAccount(String token, String accountId, Map<String, String> queryParams, String requestBody) {
        return client.activateProgramAccount(token, accountId, queryParams, requestBody);
    }

    public Response activateProgramAccount(String token, String accountId, String requestBody) {
        return client.activateProgramAccount(token, accountId, requestBody);
    }

    public Response attestProgramAccount(String token, Map<String, String> queryParams) {
        return client.attestProgramAccount(token, queryParams);
    }

    public Response compareProgramAccounts(String token, Map<String, String> queryParams) {
        return client.compareProgramAccounts(token, queryParams);
    }

    public Response compareProgramAccounts(String token, Map<String, String> queryParams, String requestBody) {
        return compareProgramAccounts(token, queryParams);
    }

    public Response createEmailDomain(String token, String guestId, String accountId, String requestBody) {
        return client.createEmailDomain(token, guestId, accountId, requestBody);
    }

    public Response createInviteLink(String token, String accountId, String requestBody) {
        return client.createInviteLink(token, accountId, requestBody);
    }

    public Response createProgramAccount(String token, String guestId, Map<String, String> queryParams, String requestBody) {
        return client.createProgramAccount(token, guestId, queryParams, requestBody);
    }

    /** With request-level headers (ReadyAPI sends content-language: zh-CN for transliteration cases). */
    public Response createProgramAccount(String token, String guestId, Map<String, String> queryParams,
                                         Map<String, String> extraHeaders, String requestBody) {
        return client.createProgramAccount(token, guestId, queryParams, extraHeaders, requestBody);
    }

    public Response deleteEmailDomain(String token, String guestId, String accountId, String emailDomain) {
        return client.deleteEmailDomain(token, guestId, accountId, emailDomain);
    }

    public Response deleteProgramAccount(String token, String accountId) {
        return client.deleteProgramAccount(token, accountId);
    }

    public Response forceEnrollProgramAccount(String token, String requestBody) {
        return client.forceEnrollProgramAccount(token, requestBody);
    }

    public Response guestDeleteProgramAccount(String token, String guestId, String accountId) {
        return client.guestDeleteProgramAccount(token, guestId, accountId);
    }

    public Response guestReadProgramAccount(String token, String guestId, String accountId) {
        return client.guestReadProgramAccount(token, guestId, accountId);
    }

    public Response guestUpdateProgramAccount(String token, String guestId, String accountId, String requestBody) {
        return client.guestUpdateProgramAccount(token, guestId, accountId, requestBody);
    }

    public Response mergeProgramAccount(String token, String guestId, String accountId, String requestBody) {
        return client.mergeProgramAccount(token, guestId, accountId, requestBody);
    }

    public Response patchProgramAccount(String token, String accountId, Map<String, String> queryParams, String requestBody) {
        return client.patchProgramAccount(token, accountId, queryParams, requestBody);
    }

    public Response readAccountBookerSpendMetrics(String token, String guestId, String accountId) {
        return client.readAccountBookerSpendMetrics(token, guestId, accountId);
    }

    public Response readAccountRolePermissions(String token, String guestId, String accountId) {
        return client.readAccountRolePermissions(token, guestId, accountId);
    }

    public Response readEmailDomains(String token, String guestId, String accountId) {
        return client.readEmailDomains(token, guestId, accountId);
    }

    public Response readProgramAccount(String token, String accountId) {
        return client.readProgramAccount(token, accountId);
    }

    public Response readProgramAccount(String token, String accountId, Map<String, String> queryParams) {
        return client.readProgramAccount(token, accountId, queryParams);
    }

    /** With request-level headers (content-language / X-PrettyPrint on the recorded GETs). */
    public Response readProgramAccount(String token, String accountId, Map<String, String> queryParams,
                                       Map<String, String> extraHeaders) {
        return client.readProgramAccount(token, accountId, queryParams, extraHeaders);
    }

    public Response rejectProgramAccount(String token, String accountId, String requestBody) {
        return client.rejectProgramAccount(token, accountId, requestBody);
    }

    public Response retrieveAccountBusinessProfile(String token, String guestId, String accountId) {
        return client.retrieveAccountBusinessProfile(token, guestId, accountId);
    }

    public Response retrieveAccountMemberInvites(String token, String guestId, String accountId, Map<String, String> queryParams) {
        return client.retrieveAccountMemberInvites(token, guestId, accountId, queryParams);
    }

    public Response retrieveClients(String token, String programAccountId) {
        return client.retrieveClients(token, programAccountId);
    }

    public Response searchAccountByInviteKey(String token, String guestId, String inviteKey) {
        return client.searchAccountByInviteKey(token, guestId, inviteKey);
    }

    public Response searchBookingPolicies(String token, String guestId, String accountId, String memberId, Map<String, String> queryParams) {
        return client.searchBookingPolicies(token, guestId, accountId, memberId, queryParams);
    }

    public Response searchProgramAccounts(String token, Map<String, String> queryParams) {
        return client.searchProgramAccounts(token, queryParams);
    }

    public Response sendInvitationLink(String token, String guestId, String accountId) {
        return sendInvitationLink(token, guestId, accountId, "");
    }

    public Response sendInvitationLink(String token, String guestId, String accountId, String requestBody) {
        return client.sendInvitationLink(token, guestId, accountId, requestBody);
    }

    public Response sendInvitationLink(String token, String guestId, String accountId, Map<String, String> queryParams, String requestBody) {
        return client.sendInvitationLink(token, guestId, accountId, requestBody);
    }

    public Response sendMemberInvites(String token, String guestId, String accountId, String requestBody) {
        return client.sendMemberInvites(token, guestId, accountId, requestBody);
    }

    public Response updateAccountCentralBillSummary(String token, String accountId, String requestBody) {
        return client.updateAccountCentralBillSummary(token, accountId, requestBody);
    }

    public Response updateAccountRolePermission(String token, String guestId, String accountId, String rolePermissionId, String requestBody) {
        return client.updateAccountRolePermission(token, guestId, accountId, rolePermissionId, requestBody);
    }

    public Response updateBusinessProfile(String token, String guestId, String accountId, String requestBody) {
        return client.updateBusinessProfile(token, guestId, accountId, requestBody);
    }

    public Response updateProgramAccount(String token, String accountId, String requestBody) {
        return client.updateProgramAccount(token, accountId, requestBody);
    }

    public Response verifyProgramAccount(String token, String guestId, Map<String, String> queryParams) {
        return client.verifyProgramAccount(token, guestId, queryParams);
    }

    public Response create(String token, String guestId, Map<String, String> query, String body) {
        return createProgramAccount(token, guestId, query, body);
    }

    public Response read(String token, String accountId, Map<String, String> query) {
        return readProgramAccount(token, accountId, query);
    }

    public <T> T readAs(String token, String accountId, Class<T> type) {
        return OpenApiModels.as(readProgramAccount(token, accountId), type);
    }

    public <T> T readAs(String token, String accountId, Map<String, String> query, Class<T> type) {
        return OpenApiModels.as(readProgramAccount(token, accountId, query), type);
    }

    public Response activate(String token, String accountId, Map<String, String> query, String body) {
        return activateProgramAccount(token, accountId, query, body);
    }

    public Response reject(String token, String accountId, String body) {
        return rejectProgramAccount(token, accountId, body);
    }

    public Response delete(String token, String accountId) {
        return deleteProgramAccount(token, accountId);
    }

    public Response search(String token, Map<String, String> query) {
        return searchProgramAccounts(token, query);
    }


    /**
     * Convenience overload mirroring {@link com.hi.api.support.ImportedRestClient}.
     * The facade previously exposed only the wider signature, so generated
     * scenario code calling this arity could not route through the domain
     * facade and failed to compile. Pure delegation -- no behaviour change.
     */
    public Response createProgramAccount(String token, String guestId, String requestBody) {
        return client.createProgramAccount(token, guestId, requestBody);
    }
}
