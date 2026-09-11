package com.ak.api.domain.guest;

import java.util.Map;

import com.ak.api.support.ImportedRestClient;

import io.restassured.response.Response;

/** Guest-domain operations. Imported ScenarioSteps and Support call this instead of the fat client. */
public final class GuestApi {

    private final ImportedRestClient client;

    public GuestApi(ImportedRestClient client) {
        this.client = client;
    }

    public ImportedRestClient raw() {
        return client;
    }

    public Response cancelGuestReservationStay(String token, String guestId, String confNumber, String gnrNumber) {
        return client.cancelGuestReservationStay(token, guestId, confNumber, gnrNumber);
    }

    public Response createGuestReservation(String token, String guestId, String requestBody) {
        return client.createGuestReservation(token, guestId, requestBody);
    }

    public Response deleteGuest(String token, String guestId) {
        return client.deleteGuest(token, guestId);
    }

    public Response getPackages(String token, String guestId) {
        return client.getPackages(token, guestId);
    }

    public Response hHonorsEnroll(String token, String requestBody) {
        return client.hHonorsEnroll(token, requestBody);
    }

    public Response httpRequest200EnrollGuest(String token, Map<String, String> queryParams, String requestBody) {
        return client.httpRequest200EnrollGuest(token, queryParams, requestBody);
    }

    public Response httpRequest200EnrollNewguest(String token, Map<String, String> queryParams, String requestBody) {
        return client.httpRequest200EnrollNewguest(token, queryParams, requestBody);
    }

    public Response httpRequest200VerifyPackagesDontExist(String token, String guestId, Map<String, String> queryParams, String requestBody) {
        return client.httpRequest200VerifyPackagesDontExist(token, guestId, queryParams, requestBody);
    }

    public Response partnerEnrollGuestBusiness(String token, Map<String, String> extraHeaders, String requestBody) {
        return client.partnerEnrollGuestBusiness(token, extraHeaders, requestBody);
    }

    public Response readAllEmails(String token, String guestId) {
        return client.readAllEmails(token, guestId);
    }

    public Response readHhonors(String token, String guestId) {
        return client.readHhonors(token, guestId);
    }

    public Response readHhonors(String token, String guestId, Map<String, String> queryParams) {
        return client.readHhonors(token, guestId, queryParams);
    }

    public Response readMemberProgramAccounts(String token, String guestId, Map<String, String> queryParams) {
        return client.readMemberProgramAccounts(token, guestId, queryParams);
    }

    public Response shopSingleProp(String token, String propCode, Map<String, String> queryParams) {
        return client.shopSingleProp(token, propCode, queryParams);
    }

    public Response updateGuestReservationStay(String token, String guestId, String confNumber, String gnrNumber, String requestBody) {
        return client.updateGuestReservationStay(token, guestId, confNumber, gnrNumber, requestBody);
    }

    public Response updateHhonors(String token, String guestId, String requestBody) {
        return client.updateHhonors(token, guestId, requestBody);
    }

    public Response enroll(String token, Map<String, String> query, String body) {
        return httpRequest200EnrollNewguest(token, query, body);
    }

    public Response enrollHhonors(String token, String body) {
        return hHonorsEnroll(token, body);
    }

    public Response read(String token, String guestId, Map<String, String> query) {
        return readHhonors(token, guestId, query);
    }

    public Response delete(String token, String guestId) {
        return deleteGuest(token, guestId);
    }


    /**
     * Convenience overload mirroring {@link com.ak.api.support.ImportedRestClient}.
     * The facade previously exposed only the wider signature, so generated
     * scenario code calling this arity could not route through the domain
     * facade and failed to compile. Pure delegation -- no behaviour change.
     */
    public Response httpRequest200EnrollGuest(String token, String requestBody) {
        return client.httpRequest200EnrollGuest(token, requestBody);
    }

    /**
     * Convenience overload mirroring {@link com.ak.api.support.ImportedRestClient}.
     * The facade previously exposed only the wider signature, so generated
     * scenario code calling this arity could not route through the domain
     * facade and failed to compile. Pure delegation -- no behaviour change.
     */
    public Response partnerEnrollGuestBusiness(String token, String requestBody) {
        return client.partnerEnrollGuestBusiness(token, requestBody);
    }
}
