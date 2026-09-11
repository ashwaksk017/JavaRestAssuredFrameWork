package com.ak.api.openapi.programaccounts;

/**
 * Minimal stand-in for the OpenAPI generator's {@code ApiClient}. Models call
 * {@link #valueToString(Object)} from unused {@code toUrlQueryString} helpers.
 * HTTP stays Rest Assured; this class is not a client.
 */
public final class ApiClient {

    private ApiClient() {}

    public static String valueToString(Object value) {
        if (value == null) {
            return "";
        }
        return String.valueOf(value);
    }
}
