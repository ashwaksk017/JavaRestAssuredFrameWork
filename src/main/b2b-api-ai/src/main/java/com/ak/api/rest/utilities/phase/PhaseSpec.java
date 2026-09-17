package com.ak.api.rest.utilities.phase;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import io.restassured.response.Response;

/**
 * Everything one REST phase of one test case is built from -- as DATA.
 *
 * <p>Measured over 18 ReadyAPI projects: 11,555 REST steps are 103
 * distinct calls. The converter still emitted 1,304 methods because each
 * one baked the step name, the id sources, the expected status, the
 * extracts and the assertions into Java text. Those are the fields of
 * this class. The call itself lives once, as an engine method that takes
 * a spec; a case supplies its spec.</p>
 *
 * <p>Immutable once built. The builder is what the converter emits per
 * case, and what a hand-written test can use directly.</p>
 */
public final class PhaseSpec {

    /** One value copied from the response (or the resolved request) into ctx. */
    public static final class Extract {
        public enum Kind { JSON, WHOLE, RAW_REQUEST, RAW_REQUEST_PATH }

        public final String key;
        public final Kind kind;
        public final String path;

        Extract(String key, Kind kind, String path) {
            this.key = key;
            this.kind = kind;
            this.path = path == null ? "" : path;
        }
    }

    /** One CSV-driven check against the response -- the closed set {@code ResponseAsserts} offers. */
    public static final class Check {
        public enum Kind { EQUALS, EXISTS, ABSENT, COUNT, TREE_EQUALS, VALUE_IN_RESPONSE }

        public final Kind kind;
        public final String jsonPath;
        public final String expected;   // SoapUI literal; the CSV column wins at runtime

        Check(Kind kind, String jsonPath, String expected) {
            this.kind = kind;
            this.jsonPath = jsonPath;
            this.expected = expected == null ? "" : expected;
        }
    }

    /** Translated Groovy / transfers / script assertions that follow the call. Generated per case. */
    @FunctionalInterface
    public interface Hook {
        void after(Response res, PhaseContext c) throws Exception;
    }

    public final String step;
    /** The typed client operation this call goes through; the generated Calls class dispatches on it. */
    public final String engine;
    public final String verb;
    public final String path;
    public final List<Ref> pathArgs;
    public final Ref token;
    public final String template;          // classpath resource, or null for no body
    public final boolean regenIdentity;
    public final int expectedStatus;       // -1 = no expectation
    public final String pollJsonPath;      // null = no poll
    public final String pollConfigKey;
    public final long pollDefaultMs;
    public final Map<String, String> query;
    public final Map<String, String> headers;
    public final List<Extract> extracts;
    public final List<Check> checks;
    public final Hook hook;

    private PhaseSpec(Builder b) {
        this.step = b.step;
        this.engine = b.engine;
        this.verb = b.verb;
        this.path = b.path;
        this.pathArgs = Collections.unmodifiableList(new ArrayList<>(b.pathArgs));
        this.token = b.token;
        this.template = b.template;
        this.regenIdentity = b.regenIdentity;
        this.expectedStatus = b.expectedStatus;
        this.pollJsonPath = b.pollJsonPath;
        this.pollConfigKey = b.pollConfigKey;
        this.pollDefaultMs = b.pollDefaultMs;
        this.query = Collections.unmodifiableMap(new LinkedHashMap<>(b.query));
        this.headers = Collections.unmodifiableMap(new LinkedHashMap<>(b.headers));
        this.extracts = Collections.unmodifiableList(new ArrayList<>(b.extracts));
        this.checks = Collections.unmodifiableList(new ArrayList<>(b.checks));
        this.hook = b.hook;
    }

    /** {@code phase("https_Get_verify_200").get("/guests/{guestId}/businesses/verify")...} */
    public static Builder phase(String step) {
        return new Builder(step);
    }

    /** The i-th path parameter, in template order. */
    public Ref arg(int i) {
        if (i < 0 || i >= pathArgs.size()) {
            throw new IllegalStateException("phase `" + step + "` declares " + pathArgs.size()
                    + " path argument(s); asked for #" + i + " -- the spec and the engine disagree");
        }
        return pathArgs.get(i);
    }

    @Override
    public String toString() {
        return verb + " " + path + " as `" + step + "` expect " + expectedStatus
                + " args=" + pathArgs + " query=" + query.keySet()
                + " extracts=" + extracts.size() + " checks=" + checks.size()
                + (hook != null ? " +hook" : "");
    }

    public static final class Builder {
        private final String step;
        private String engine = "";
        private String verb = "GET";
        private String path = "/";
        private final List<Ref> pathArgs = new ArrayList<>();
        private Ref token = Ref.ctx("tokenId.GeneratedTokenID");
        private String template;
        private boolean regenIdentity;
        private int expectedStatus = -1;
        private String pollJsonPath;
        private String pollConfigKey;
        private long pollDefaultMs;
        private final Map<String, String> query = new LinkedHashMap<>();
        private final Map<String, String> headers = new LinkedHashMap<>();
        private final List<Extract> extracts = new ArrayList<>();
        private final List<Check> checks = new ArrayList<>();
        private Hook hook;

        Builder(String step) {
            if (step == null || step.isEmpty()) {
                throw new IllegalArgumentException("a phase needs its ReadyAPI step name: "
                        + "it is the CSV column prefix and the log label");
            }
            this.step = step;
        }

        public Builder get(String path) { return call("GET", path); }
        public Builder post(String path) { return call("POST", path); }
        public Builder put(String path) { return call("PUT", path); }
        public Builder patch(String path) { return call("PATCH", path); }
        public Builder delete(String path) { return call("DELETE", path); }

        public Builder call(String verb, String path) {
            this.verb = verb.toUpperCase();
            this.path = path;
            return this;
        }

        /** The typed client operation ({@code readProgramAccount}) the generated Calls class binds. */
        public Builder engine(String clientMethod) {
            this.engine = clientMethod == null ? "" : clientMethod;
            return this;
        }

        /** Path parameters in the order they appear in the template. */
        public Builder args(Ref... refs) {
            pathArgs.clear();
            Collections.addAll(pathArgs, refs);
            return this;
        }

        public Builder token(Ref ref) { this.token = ref; return this; }
        public Builder noAuth() { this.token = Ref.literal(""); return this; }
        public Builder template(String classpathResource) { this.template = classpathResource; return this; }
        public Builder regenIdentity() { this.regenIdentity = true; return this; }
        public Builder expect(int status) { this.expectedStatus = status; return this; }

        public Builder pollUntilPresent(String jsonPath, String configKey, long defaultMs) {
            this.pollJsonPath = jsonPath;
            this.pollConfigKey = configKey;
            this.pollDefaultMs = defaultMs;
            return this;
        }

        /** Query value as the converter writes it: a {@code #placeholder#} or a literal. */
        public Builder query(String name, String placeholderOrValue) {
            query.put(name, placeholderOrValue == null ? "" : placeholderOrValue);
            return this;
        }

        public Builder header(String name, String placeholderOrValue) {
            headers.put(name, placeholderOrValue == null ? "" : placeholderOrValue);
            return this;
        }

        public Builder extract(String ctxKey, String jsonPath) {
            extracts.add(new Extract(ctxKey, Extract.Kind.JSON, jsonPath));
            return this;
        }

        public Builder extractWhole(String ctxKey) {
            extracts.add(new Extract(ctxKey, Extract.Kind.WHOLE, ""));
            return this;
        }

        public Builder extractRawRequest(String ctxKey) {
            extracts.add(new Extract(ctxKey, Extract.Kind.RAW_REQUEST, ""));
            return this;
        }

        public Builder extractRawRequestPath(String ctxKey, String jsonPath) {
            extracts.add(new Extract(ctxKey, Extract.Kind.RAW_REQUEST_PATH, jsonPath));
            return this;
        }

        public Builder equals(String jsonPath, String expected) {
            checks.add(new Check(Check.Kind.EQUALS, jsonPath, expected));
            return this;
        }

        public Builder exists(String... jsonPaths) {
            for (String p : jsonPaths) {
                checks.add(new Check(Check.Kind.EXISTS, p, ""));
            }
            return this;
        }

        public Builder absent(String... jsonPaths) {
            for (String p : jsonPaths) {
                checks.add(new Check(Check.Kind.ABSENT, p, ""));
            }
            return this;
        }

        public Builder count(String jsonPath, int expected) {
            checks.add(new Check(Check.Kind.COUNT, jsonPath, String.valueOf(expected)));
            return this;
        }

        public Builder treeEquals(String jsonPath, String expected) {
            checks.add(new Check(Check.Kind.TREE_EQUALS, jsonPath, expected));
            return this;
        }

        public Builder valueInResponse(String expected, String jsonPath) {
            checks.add(new Check(Check.Kind.VALUE_IN_RESPONSE, jsonPath, expected));
            return this;
        }

        public Builder after(Hook hook) { this.hook = hook; return this; }

        public PhaseSpec build() {
            long params = path.chars().filter(ch -> ch == '{').count();
            if (params != pathArgs.size()) {
                throw new IllegalStateException("phase `" + step + "`: path " + path + " has "
                        + params + " parameter(s) but " + pathArgs.size() + " Ref(s) were given");
            }
            return new PhaseSpec(this);
        }
    }
}
