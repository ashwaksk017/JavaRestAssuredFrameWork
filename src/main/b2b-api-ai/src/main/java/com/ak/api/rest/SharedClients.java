package com.ak.api.rest;

import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Function;

/**
 * One generated REST client instance per (type, baseUrl). Tests should
 * call {@link #programAccounts} instead of {@code new ProgramAccountsClient}
 * on every class.
 */
public final class SharedClients {

    private static final ConcurrentHashMap<String, Object> BY_KEY = new ConcurrentHashMap<>();

    private SharedClients() {}

    public static com.ak.api.rest.clients.ProgramAccountsClient programAccounts(String baseUrl) {
        return get("ProgramAccountsClient", baseUrl,
                com.ak.api.rest.clients.ProgramAccountsClient::new);
    }

    public static com.ak.api.rest.clients.ProgramAccountClient programAccount(String baseUrl) {
        return get("ProgramAccountClient", baseUrl,
                com.ak.api.rest.clients.ProgramAccountClient::new);
    }

    public static com.ak.api.rest.clients.SmbToHwsConsumerClient smbToHwsConsumer(String baseUrl) {
        return get("SmbToHwsConsumerClient", baseUrl,
                com.ak.api.rest.clients.SmbToHwsConsumerClient::new);
    }

    public static com.ak.api.rest.clients.Smbtohwsconsumerregressione2eClient
            smbtohwsconsumerregressione2e(String baseUrl) {
        return get("Smbtohwsconsumerregressione2eClient", baseUrl,
                com.ak.api.rest.clients.Smbtohwsconsumerregressione2eClient::new);
    }

    @SuppressWarnings("unchecked")
    public static <T> T get(String kind, String baseUrl, Function<String, T> factory) {
        String key = kind + "|" + (baseUrl == null ? "" : baseUrl);
        return (T) BY_KEY.computeIfAbsent(key, k -> factory.apply(baseUrl));
    }

    public static void clearForTest() {
        BY_KEY.clear();
    }
}
