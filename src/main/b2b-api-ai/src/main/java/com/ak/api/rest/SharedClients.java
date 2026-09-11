package com.ak.api.rest;

import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Function;

/**
 * One generated REST client instance per (type, baseUrl), so every test class
 * does not build its own.
 *
 * <h2>Deliberately knows no client by name</h2>
 *
 * This class used to carry typed convenience methods --
 * {@code programAccounts(baseUrl)}, {@code smbToHwsConsumer(baseUrl)} and two
 * more -- each naming a client class that only exists after converting one
 * particular ReadyAPI XML.
 *
 * <p>That made the framework non-generic in the worst way: convert a
 * <em>different</em> XML and this committed file referenced four classes that
 * were never generated, so {@code mvn compile} failed on code the user never
 * touched. The failure also pointed at SharedClients rather than at the real
 * cause (a client that simply was not part of that conversion).</p>
 *
 * <p>Those methods had <b>zero</b> call sites -- generated tests always used
 * the generic {@link #get} below -- so removing them costs nothing and lets
 * any XML convert and compile on its own.</p>
 *
 * <p>Callers pass the client's own constructor:</p>
 * <pre>
 * MemberValidationClient client = SharedClients.get(
 *         "MemberValidationClient", baseUrl, MemberValidationClient::new);
 * </pre>
 *
 * <p>Keep it that way. A convenience method here must never name a generated
 * client type.</p>
 */
public final class SharedClients {

    private static final ConcurrentHashMap<String, Object> BY_KEY = new ConcurrentHashMap<>();

    private SharedClients() {}

    /**
     * The instance for {@code kind} at {@code baseUrl}, creating it once.
     *
     * @param kind    stable key for the client type, usually its simple name
     * @param baseUrl service base URL; part of the key so two environments
     *                in one JVM do not share an instance
     * @param factory the client's constructor, e.g. {@code FooClient::new}
     */
    @SuppressWarnings("unchecked")
    public static <T> T get(String kind, String baseUrl, Function<String, T> factory) {
        String key = kind + "|" + (baseUrl == null ? "" : baseUrl);
        return (T) BY_KEY.computeIfAbsent(key, k -> factory.apply(baseUrl));
    }

    public static void clearForTest() {
        BY_KEY.clear();
    }
}
