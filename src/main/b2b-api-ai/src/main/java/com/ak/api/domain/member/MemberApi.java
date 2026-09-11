package com.ak.api.domain.member;

import java.util.Map;

import com.ak.api.support.ImportedRestClient;

import io.restassured.response.Response;

/** Member operations. Imported flows call this instead of the fat client. */
public final class MemberApi {

    private final ImportedRestClient client;

    public MemberApi(ImportedRestClient client) {
        this.client = client;
    }

    public ImportedRestClient raw() {
        return client;
    }

    public Response activateProgramAccountMember(String token, String accountId, String memberId, String requestBody) {
        return client.activateProgramAccountMember(token, accountId, memberId, requestBody);
    }

    public Response createPartnerAccount(String token, String guestId, String accountId, String memberId, String partneraccount, String requestBody) {
        return client.createPartnerAccount(token, guestId, accountId, memberId, partneraccount, requestBody);
    }

    public Response createProgramAccountMember(String token, String guestId, String accountId, Map<String, String> queryParams, String requestBody) {
        return client.createProgramAccountMember(token, guestId, accountId, queryParams, requestBody);
    }

    public Response createProgramAccountMember(String token, String guestId, String accountId, String requestBody) {
        return client.createProgramAccountMember(token, guestId, accountId, requestBody);
    }

    public Response deleteMemberPermission(String token, String guestId, String accountId, String memberId, String memberPermissionId) {
        return client.deleteMemberPermission(token, guestId, accountId, memberId, memberPermissionId);
    }

    public Response deletePartnerAccount(String token, String guestId, String accountId, String memberId, String partneraccount) {
        return client.deletePartnerAccount(token, guestId, accountId, memberId, partneraccount);
    }

    public Response deleteProgramAccountMember(String token, String guestId, String accountId, String memberId) {
        return client.deleteProgramAccountMember(token, guestId, accountId, memberId);
    }

    public Response guestSearchProgramAccountMembers(String token, String guestId, String accountId, Map<String, String> queryParams) {
        return client.guestSearchProgramAccountMembers(token, guestId, accountId, queryParams);
    }

    public Response mergeProgramAccountMemberGuestProfile(String token, String accountId, String memberId, String requestBody) {
        return client.mergeProgramAccountMemberGuestProfile(token, accountId, memberId, requestBody);
    }

    public Response mergeProgramAccountMember(String token, String accountId, String requestBody) {
        return client.mergeProgramAccountMember(token, accountId, requestBody);
    }

    public Response patchProgramAccountMember(String token, String accountId, String memberId, Map<String, String> queryParams, String requestBody) {
        return client.patchProgramAccountMember(token, accountId, memberId, queryParams, requestBody);
    }

    public Response readAccountMemberPreferences(String token, String guestId, String accountId, String memberId) {
        return client.readAccountMemberPreferences(token, guestId, accountId, memberId);
    }

    public Response readPartnerAccounts(String token, String guestId, String accountId, String memberId, String partneraccount) {
        return client.readPartnerAccounts(token, guestId, accountId, memberId, partneraccount);
    }

    public Response readProgramAccountMemberSpendDetail(String token, String guestId, String accountId, String memberId, Map<String, String> queryParams) {
        return client.readProgramAccountMemberSpendDetail(token, guestId, accountId, memberId, queryParams);
    }

    public Response readProgramAccountMember(String token, String guestId, String accountId, String memberId) {
        return client.readProgramAccountMember(token, guestId, accountId, memberId);
    }

    public Response readProgramAccountMember(String token, String guestId, String accountId, String memberId, Map<String, String> queryParams) {
        return client.readProgramAccountMember(token, guestId, accountId, memberId, queryParams);
    }

    public Response readStayFromConfirmationNumber(String token, String guestId, String accountId, String memberId, Map<String, String> queryParams) {
        return client.readStayFromConfirmationNumber(token, guestId, accountId, memberId, queryParams);
    }

    public Response removeProgramAccountMember(String token, String accountId, String memberId) {
        return client.removeProgramAccountMember(token, accountId, memberId);
    }

    public Response removeProgramAccountMember(String token, String accountId, String memberId, String requestBody) {
        return client.removeProgramAccountMember(token, accountId, memberId, requestBody);
    }

    public Response requestMemberValidation(String token, String guestId, String accountId, String memberId, String requestBody) {
        return client.requestMemberValidation(token, guestId, accountId, memberId, requestBody);
    }

    public Response retrieveAccountMemberBusinessProfile(String token, String guestId, String accountId, String memberId) {
        return client.retrieveAccountMemberBusinessProfile(token, guestId, accountId, memberId);
    }

    public Response retrieveAccountMemberPermissions(String token, String guestId, String accountId, String memberId, Map<String, String> queryParams) {
        return client.retrieveAccountMemberPermissions(token, guestId, accountId, memberId, queryParams);
    }

    public Response retrieveAccountMemberRolePermissions(String token, String guestId, String accountId, String memberId) {
        return client.retrieveAccountMemberRolePermissions(token, guestId, accountId, memberId);
    }

    public Response retrieveGrantedAccessToOtherMembers(String token, String guestId, String accountId, String memberId, Map<String, String> queryParams) {
        return client.retrieveGrantedAccessToOtherMembers(token, guestId, accountId, memberId, queryParams);
    }

    public Response retrieveOtherMembersGrantedAccess(String token, String guestId, String accountId, String memberId, Map<String, String> queryParams) {
        return client.retrieveOtherMembersGrantedAccess(token, guestId, accountId, memberId, queryParams);
    }

    public Response searchProgramAccountMembers(String token, String accountId, Map<String, String> queryParams) {
        return client.searchProgramAccountMembers(token, accountId, queryParams);
    }

    public Response updateAccountMemberPermissions(String token, String guestId, String accountId, String memberId, String requestBody) {
        return client.updateAccountMemberPermissions(token, guestId, accountId, memberId, requestBody);
    }

    public Response updateAccountMemberPermission(String token, String guestId, String accountId, String memberId, String memberPermissionId, String requestBody) {
        return client.updateAccountMemberPermission(token, guestId, accountId, memberId, memberPermissionId, requestBody);
    }

    public Response updateAccountMemberRolePermission(String token, String guestId, String accountId, String memberId, String requestBody) {
        return client.updateAccountMemberRolePermission(token, guestId, accountId, memberId, requestBody);
    }

    public Response updateEmployeeProfile(String token, String guestId, String accountId, String memberId, String requestBody) {
        return client.updateEmployeeProfile(token, guestId, accountId, memberId, requestBody);
    }

    public Response updateMemberCentralBillSummary(String token, String accountId, String memberId, String requestBody) {
        return client.updateMemberCentralBillSummary(token, accountId, memberId, requestBody);
    }

    public Response updateMemberEmailAddress(String token, String guestId, String accountId, String memberId, String requestBody) {
        return client.updateMemberEmailAddress(token, guestId, accountId, memberId, requestBody);
    }

    public Response updateMemberPhone(String token, String guestId, String accountId, String memberId, String requestBody) {
        return client.updateMemberPhone(token, guestId, accountId, memberId, requestBody);
    }

    public Response updateMemberRole(String token, String guestId, String accountId, String memberId, String requestBody) {
        return client.updateMemberRole(token, guestId, accountId, memberId, requestBody);
    }

    public Response updateProgramAccountMember(String token, String guestId, String accountId, String memberId, Map<String, String> queryParams, String requestBody) {
        return client.updateProgramAccountMember(token, guestId, accountId, memberId, queryParams, requestBody);
    }

    public Response updateProgramAccountMember(String token, String guestId, String accountId, String memberId, String requestBody) {
        return client.updateProgramAccountMember(token, guestId, accountId, memberId, requestBody);
    }

    public Response validateMemberTOTP(String token, String guestId, String accountId, String memberId, String requestBody) {
        return client.validateMemberTOTP(token, guestId, accountId, memberId, requestBody);
    }

    public Response create(String token, String guestId, String accountId, String body) {
        return createProgramAccountMember(token, guestId, accountId, body);
    }

    public Response read(String token, String guestId, String accountId, String memberId) {
        return readProgramAccountMember(token, guestId, accountId, memberId);
    }

    public Response activate(String token, String accountId, String memberId, String body) {
        return activateProgramAccountMember(token, accountId, memberId, body);
    }

    public Response confirmTotp(String token, String guestId, String accountId, String memberId, String body) {
        return validateMemberTOTP(token, guestId, accountId, memberId, body);
    }

    public Response remove(String token, String accountId, String memberId, String body) {
        return removeProgramAccountMember(token, accountId, memberId, body);
    }


    /**
     * Convenience overload mirroring {@link com.ak.api.support.ImportedRestClient}.
     * The facade exposed only the other arity, so generated scenario code
     * routing this call through the domain facade failed to compile.
     * Pure delegation -- no behaviour change.
     */
    public Response deletePartnerAccount(String token, String guestId, String accountId, String memberId, String partneraccount, Map<String, String> queryParams) {
        return client.deletePartnerAccount(token, guestId, accountId, memberId, partneraccount, queryParams);
    }

    /**
     * Convenience overload mirroring {@link com.ak.api.support.ImportedRestClient}.
     * The facade exposed only the other arity, so generated scenario code
     * routing this call through the domain facade failed to compile.
     * Pure delegation -- no behaviour change.
     */
    public Response patchProgramAccountMember(String token, String accountId, String memberId, String requestBody) {
        return client.patchProgramAccountMember(token, accountId, memberId, requestBody);
    }
}
