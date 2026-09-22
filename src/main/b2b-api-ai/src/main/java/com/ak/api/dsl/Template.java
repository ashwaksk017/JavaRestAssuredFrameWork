package com.ak.api.dsl;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import com.ak.api.config.Config;
import com.ak.api.support.ImportedScenario;

/**
 * Pick a request body by BUSINESS MEANING rather than by generated name.
 *
 * <h2>Why not just reference {@code Templates.<CONSTANT>}?</h2>
 *
 * Because that name is not a stable handle. The converter emits 472
 * constants for this suite and 392 of them (83%) live in multi-variant
 * groups like {@code BUSINESSES_CREATEACCOUNT_200_13}. That trailing
 * ordinal is handed out while walking template paths in ascending
 * CONTENT-HASH order, so editing any single template moves its hash,
 * moves its sort position, and renumbers its siblings. A hand-written
 * test pinned to {@code _13} would silently start sending a different
 * body after an unrelated template edit.
 *
 * <p>The ReadyAPI case name and step name are written by a human in the
 * XML, so they survive a reconvert. This class binds to those, through
 * the {@code templates/<suite>/_index.csv} the converter exports.</p>
 *
 * <h2>Usage</h2>
 *
 * <pre>
 * MasterClass.onboarding(row)
 *     .using(Template.singleMemberOnboarding)
 *     .createH4LAccount()
 *     .complete();
 * </pre>
 *
 * <p>Nothing here compiles against a generated type, so a converter run
 * -- {@code --clean} included -- cannot break this file. Resolution is by
 * classpath resource at runtime, and a miss fails loudly with near-miss
 * candidates rather than quietly sending a plausible wrong body.</p>
 */
public final class Template {

    /** Set {@code -Dmanual.suite=<name>} when the index cannot be inferred. */
    private static final String SUITE_PROPERTY = "manual.suite";

    // =====================================================================
    // Named templates
    //
    // Each name is a (ReadyAPI case, ReadyAPI step) pair -- the handle that
    // survives a reconvert. Add names as scenarios need them; this is a
    // curated list, not a mirror of all 472 constants. Most of those differ
    // only in throwaway data (one address says Houston, another says
    // "testmand"), so naming them all would be noise. A name that stops
    // resolving fails loudly on first use rather than sending a wrong body.
    // =====================================================================

    /**
     * Create the pending account member in the regular onboarding flow.
     *
     * <p>Chosen as the canonical member-creation body: its case covers the
     * optional-field member record, so it exercises the full shape.</p>
     */
    public static final Template singleMemberOnboarding = of(
            "single member onboarding",
            "B2B-4955_post_regular_flow_optional_field_member_record_200",
            "http_request_200_3-CreatePendingAccountmember");

    /**
     * Create a program account for the Amex invite-link scenario.
     *
     * <p>{@code CreateAccount_200} is the sharpest example of why this
     * class exists: 22 rows sit behind 14 DIFFERENT bodies, named
     * {@code BUSINESSES_CREATEACCOUNT_200}, {@code _3}, {@code _9},
     * {@code _10}, {@code _14} ... those ordinals move when any template's
     * content changes.</p>
     */
    public static final Template amexInviteLinkAccount = of(
            "amex invite-link account",
            "B2B-4643_verify_amex_invitelink_false",
            "CreateAccount_200");

    /** Reject an account whose email domain is already claimed. */
    public static final Template rejectedEmailDomainAccount = of(
            "rejected email-domain account",
            "B2B-3233_post_email_domain_rejected_account_403",
            "Reject_Account");

    /** Activate a freshly registered business (204). */
    public static final Template registerBusinessActivation = of(
            "register-business activation",
            "B2B-3216_post_register_business_204",
            "http_request_204_accountactivate");

    private static volatile Map<String, String> cachedIndex;
    private static volatile String cachedSuite;

    private final String label;
    private final String caseName;
    private final String stepName;

    private Template(String label, String caseName, String stepName) {
        this.label = label;
        this.caseName = caseName;
        this.stepName = stepName;
    }

    /**
     * Name a template by the ReadyAPI case + step that owns it.
     *
     * @param label    what this body MEANS, used in logs and failure text
     * @param caseName ReadyAPI test-case name, exactly as it appears in the XML
     * @param stepName ReadyAPI step name within that case
     */
    public static Template of(String label, String caseName, String stepName) {
        return new Template(label, caseName, stepName);
    }

    public String label() {
        return label;
    }

    public String caseName() {
        return caseName;
    }

    public String stepName() {
        return stepName;
    }

    @Override
    public String toString() {
        return label + " (" + caseName + " / " + stepName + ")";
    }

    /**
     * Classpath path of the body, ready for {@code RestStep.template(...)}.
     *
     * @throws IllegalStateException when the pair is absent from the index,
     *         listing near misses. A silent fallback here would send a
     *         plausible-looking wrong body, which is the failure mode this
     *         whole class exists to prevent.
     */
    public String resolve() {
        Map<String, String> index = index();
        String hit = index.get(key(caseName, stepName));
        if (hit != null) {
            return hit;
        }
        List<String> nearby = new ArrayList<>();
        String wantedStep = norm(stepName);
        for (Map.Entry<String, String> e : index.entrySet()) {
            if (e.getKey().endsWith(SEPARATOR + wantedStep)) {
                nearby.add(e.getKey());
            }
            if (nearby.size() >= 8) {
                break;
            }
        }
        throw new IllegalStateException(
                "Template " + this + " is not in templates/" + suite()
                + "/_index.csv. The converter rewrites that index on every "
                + "run; if the ReadyAPI case or step was renamed, update the "
                + "constant in com.ak.api.dsl.Template. Steps named '"
                + stepName + "' in other cases: "
                + (nearby.isEmpty() ? "<none>" : String.join(", ", nearby)));
    }

    /** Resolve, or {@code null} when absent -- for callers wanting a fallback. */
    public String resolveOrNull() {
        return index().get(key(caseName, stepName));
    }

    // =====================================================================
    // Index
    // =====================================================================

    private static final String SEPARATOR = " >> ";

    private static String key(String caseName, String stepName) {
        return norm(caseName) + SEPARATOR + norm(stepName);
    }

    private static String norm(String s) {
        return s == null ? "" : s.trim().toLowerCase(Locale.ROOT);
    }

    private static Map<String, String> index() {
        String suite = suite();
        Map<String, String> cached = cachedIndex;
        if (cached != null && suite.equals(cachedSuite)) {
            return cached;
        }
        Map<String, String> loaded = load(suite);
        cachedIndex = loaded;
        cachedSuite = suite;
        return loaded;
    }

    /**
     * Which converted suite owns the index.
     *
     * <p>The bound suite name is not enough on its own: a hand-written test
     * binds {@code ImportedScenario.bind(..., "manual")} and there is no
     * {@code templates/manual/} tree. So an explicit property wins, then the
     * bound name if it actually has an index, then the suite of the CLIENT
     * bound to this thread ({@code FooClient -> foo}), then the name derived
     * from {@code -Dmanual.client}.</p>
     *
     * <p>The bound client comes before the property because it is the client
     * actually sending the requests -- a manual test needs no flag to find its
     * own templates. Every branch is still gated on {@link #exists}, so an
     * unconverted client falls through rather than silently resolving against
     * whichever suite happens to be present.</p>
     */
    private static String suite() {
        String explicit = Config.get(SUITE_PROPERTY, "");
        if (explicit != null && !explicit.isEmpty()) {
            return explicit;
        }
        String bound;
        try {
            bound = ImportedScenario.current().suiteName;
        } catch (RuntimeException notBound) {
            // Resolution can legitimately happen outside a bound test --
            // a unit test of this registry, for instance. Fall through.
            bound = null;
        }
        if (bound != null && !bound.isEmpty() && exists(bound)) {
            return bound;
        }
        String fromBoundClient = SuiteName.ofBoundClient();
        if (fromBoundClient != null && exists(fromBoundClient)) {
            return fromBoundClient;
        }
        String configured = SuiteName.ofConfiguredClient();
        if (configured != null && exists(configured)) {
            return configured;
        }
        return bound == null ? "" : bound;
    }

    private static String resourceFor(String suite) {
        return "templates/" + suite + "/_index.csv";
    }

    private static boolean exists(String suite) {
        return Thread.currentThread().getContextClassLoader()
                .getResource(resourceFor(suite)) != null;
    }

    private static Map<String, String> load(String suite) {
        String resource = resourceFor(suite);
        Map<String, String> out = new LinkedHashMap<>();
        InputStream in = Thread.currentThread().getContextClassLoader()
                .getResourceAsStream(resource);
        if (in == null) {
            throw new IllegalStateException(
                    "No template index on the classpath at " + resource
                    + ". Run the converter (it writes one per suite), or set "
                    + "-D" + SUITE_PROPERTY + "=<convertedSuiteName>.");
        }
        try (BufferedReader br = new BufferedReader(
                new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String header = br.readLine();
            if (header == null) {
                throw new IllegalStateException("Empty template index: " + resource);
            }
            String line;
            while ((line = br.readLine()) != null) {
                if (line.isEmpty()) {
                    continue;
                }
                List<String> cells = splitCsv(line);
                if (cells.size() < 3) {
                    continue;
                }
                out.put(key(cells.get(0), cells.get(1)), cells.get(2));
            }
        } catch (java.io.IOException e) {
            throw new IllegalStateException("Cannot read " + resource, e);
        }
        return out;
    }

    /**
     * Minimal RFC4180 split.
     *
     * <p>ReadyAPI case names contain commas, so a naive split would shift
     * every later column and map the case to the wrong template path.</p>
     */
    static List<String> splitCsv(String line) {
        List<String> cells = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean inQuotes = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (inQuotes) {
                if (c == '"') {
                    if (i + 1 < line.length() && line.charAt(i + 1) == '"') {
                        cur.append('"');
                        i++;
                    } else {
                        inQuotes = false;
                    }
                } else {
                    cur.append(c);
                }
            } else if (c == '"') {
                inQuotes = true;
            } else if (c == ',') {
                cells.add(cur.toString());
                cur.setLength(0);
            } else {
                cur.append(c);
            }
        }
        cells.add(cur.toString());
        return cells;
    }
}
