package com.hi.api.domain;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;

import com.hi.api.domain.account.ProgramAccountApi;
import com.hi.api.domain.guest.GuestApi;
import com.hi.api.domain.member.MemberApi;
import com.hi.api.support.ImportedRestClient;

/**
 * Binds guest / program-account / member facades over one generated
 * {@link ImportedRestClient}. {@link #client()} is a routing proxy: imported
 * Support that still writes {@code client.hHonorsEnroll(...)} goes through
 * {@link GuestApi} (and the other domains) without editing 600 Support files.
 */
public final class DomainApis {

    private static final Set<String> GUEST = Set.of(
            "cancelGuestReservationStay", "createGuestReservation", "deleteGuest",
            "getPackages", "hHonorsEnroll", "httpRequest200EnrollGuest",
            "httpRequest200EnrollNewguest", "httpRequest200VerifyPackagesDontExist",
            "partnerEnrollGuestBusiness", "readAllEmails", "readHhonors",
            "readMemberProgramAccounts", "shopSingleProp",
            "updateGuestReservationStay", "updateHhonors");

    private static final Set<String> ACCOUNT = Set.of(
            "activateProgramAccount", "attestProgramAccount", "compareProgramAccounts",
            "createEmailDomain", "createInviteLink", "createProgramAccount",
            "deleteEmailDomain", "deleteProgramAccount", "forceEnrollProgramAccount",
            "guestDeleteProgramAccount", "guestReadProgramAccount",
            "guestUpdateProgramAccount", "mergeProgramAccount", "patchProgramAccount",
            "readAccountBookerSpendMetrics", "readAccountRolePermissions",
            "readEmailDomains", "readProgramAccount", "rejectProgramAccount",
            "retrieveAccountBusinessProfile", "retrieveAccountMemberInvites",
            "retrieveClients", "searchAccountByInviteKey", "searchBookingPolicies",
            "searchProgramAccounts", "sendInvitationLink", "sendMemberInvites",
            "updateAccountCentralBillSummary", "updateAccountRolePermission",
            "updateBusinessProfile", "updateProgramAccount", "verifyProgramAccount");

    private static final Set<String> MEMBER = Set.of(
            "activateProgramAccountMember", "createPartnerAccount",
            "createProgramAccountMember", "deleteMemberPermission",
            "deletePartnerAccount", "deleteProgramAccountMember",
            "guestSearchProgramAccountMembers", "mergeProgramAccountMember",
            "mergeProgramAccountMemberGuestProfile", "patchProgramAccountMember",
            "readAccountMemberPreferences", "readPartnerAccounts",
            "readProgramAccountMemberSpendDetail", "readProgramAccountMember",
            "readStayFromConfirmationNumber", "removeProgramAccountMember",
            "requestMemberValidation", "retrieveAccountMemberBusinessProfile",
            "retrieveAccountMemberPermissions", "retrieveAccountMemberRolePermissions",
            "retrieveGrantedAccessToOtherMembers", "retrieveOtherMembersGrantedAccess",
            "searchProgramAccountMembers", "updateAccountMemberPermission",
            "updateAccountMemberPermissions", "updateAccountMemberRolePermission",
            "updateEmployeeProfile", "updateMemberCentralBillSummary",
            "updateMemberEmailAddress", "updateMemberPhone", "updateMemberRole",
            "updateProgramAccountMember", "validateMemberTOTP");

    private static final Map<String, String> RECEIVER_BY_METHOD = receiverMap();

    private final GuestApi guests;
    private final ProgramAccountApi accounts;
    private final MemberApi members;
    private final ImportedRestClient client;

    private DomainApis(GuestApi guests, ProgramAccountApi accounts,
                       MemberApi members, ImportedRestClient client) {
        this.guests = guests;
        this.accounts = accounts;
        this.members = members;
        this.client = client;
    }

    /**
     * Wrap {@code raw} so guest/account/member methods run through the
     * domain facades. Token, Salesforce, travel-agency, and partition
     * calls stay on the generated client.
     */
    /**
     * Generated {@code SetupHelper.flow_A} is typed to {@code *Client}.
     * Unwrap the routing proxy so invoke still receives the concrete client.
     */
    public static Object unwrapRaw(Object client) {
        if (client != null && Proxy.isProxyClass(client.getClass())) {
            try {
                Object h = Proxy.getInvocationHandler(client);
                if (h instanceof Router r) {
                    return r.raw;
                }
            } catch (IllegalArgumentException ignored) {
                return client;
            }
        }
        return client;
    }

    public static DomainApis bind(ImportedRestClient raw) {
        if (raw == null) {
            throw new IllegalArgumentException("client");
        }
        if (Proxy.isProxyClass(raw.getClass())
                && Proxy.getInvocationHandler(raw) instanceof Router) {
            Router existing = (Router) Proxy.getInvocationHandler(raw);
            return new DomainApis(existing.guests, existing.accounts,
                    existing.members, raw);
        }
        GuestApi guests = new GuestApi(raw);
        ProgramAccountApi accounts = new ProgramAccountApi(raw);
        MemberApi members = new MemberApi(raw);
        ImportedRestClient routed = (ImportedRestClient) Proxy.newProxyInstance(
                ImportedRestClient.class.getClassLoader(),
                new Class<?>[] {ImportedRestClient.class},
                new Router(raw, guests, accounts, members));
        return new DomainApis(guests, accounts, members, routed);
    }

    /** {@code guests}, {@code accounts}, or {@code members} for converter emit. */
    public static String receiverFor(String clientMethod) {
        if (clientMethod == null || clientMethod.isEmpty()) {
            return "client";
        }
        return RECEIVER_BY_METHOD.getOrDefault(clientMethod, "client");
    }

    public static boolean isDomainMethod(String clientMethod) {
        return RECEIVER_BY_METHOD.containsKey(clientMethod);
    }

    public GuestApi guests() {
        return guests;
    }

    public ProgramAccountApi accounts() {
        return accounts;
    }

    public MemberApi members() {
        return members;
    }

    /** Routing proxy; use this as {@code ScenarioSteps.client}. */
    public ImportedRestClient client() {
        return client;
    }

    private static Map<String, String> receiverMap() {
        Map<String, String> out = new HashMap<>();
        for (String n : GUEST) {
            out.put(n, "guests");
        }
        for (String n : ACCOUNT) {
            out.put(n, "accounts");
        }
        for (String n : MEMBER) {
            out.put(n, "members");
        }
        return Collections.unmodifiableMap(out);
    }

    private static final class Router implements InvocationHandler {
        private final ImportedRestClient raw;
        private final GuestApi guests;
        private final ProgramAccountApi accounts;
        private final MemberApi members;

        private Router(ImportedRestClient raw, GuestApi guests,
                       ProgramAccountApi accounts, MemberApi members) {
            this.raw = raw;
            this.guests = guests;
            this.accounts = accounts;
            this.members = members;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args)
                throws Throwable {
            if (method.getDeclaringClass() == Object.class) {
                return invokeObject(proxy, method, args);
            }
            Object target = targetFor(method.getName());
            if (target == null) {
                return method.invoke(raw, args);
            }
            try {
                Method dm = target.getClass().getMethod(
                        method.getName(), method.getParameterTypes());
                return dm.invoke(target, args);
            } catch (NoSuchMethodException e) {
                return method.invoke(raw, args);
            } catch (InvocationTargetException e) {
                throw e.getCause() != null ? e.getCause() : e;
            }
        }

        private Object targetFor(String name) {
            if (GUEST.contains(name)) {
                return guests;
            }
            if (ACCOUNT.contains(name)) {
                return accounts;
            }
            if (MEMBER.contains(name)) {
                return members;
            }
            return null;
        }

        private Object invokeObject(Object proxy, Method method, Object[] args) {
            String n = method.getName();
            if ("equals".equals(n)) {
                return proxy == args[0];
            }
            if ("hashCode".equals(n)) {
                return System.identityHashCode(proxy);
            }
            if ("toString".equals(n)) {
                return "DomainApis.client(" + raw.getClass().getSimpleName() + ")";
            }
            try {
                return method.invoke(raw, args);
            } catch (ReflectiveOperationException e) {
                throw new IllegalStateException(e);
            }
        }
    }
}
