package com.ak.api.support;

// ra_converter-framework-rev: 1
// Bumped whenever this bundled file changes. The converter
// SKIPS author-editable files that already exist, so without a
// revision it cannot tell an author's edit from a copy left by
// an older converter.

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * The project-shaped names the identity regeneration reasons about.
 *
 * <p>Everything ReadyAPI's DataGen conventions dictate -- which
 * {@code Properties.*} fields hold the generated identity, which key is the
 * frozen domain, what the member-enroll steps are called, which saved
 * values are fixtures that must never be rewritten -- lives here instead of
 * being spread over {@code CtxFields} and {@code ImportedScenario} as
 * literals. The converter writes the project's values to
 * {@code converter_identity.json} on the classpath (from
 * {@code tools/ra_converter/converter.config.json}); the built-in defaults
 * below are what that file says for the suite this framework was first
 * built on, so a tree without the resource behaves exactly as before.</p>
 *
 * <p>Read-only after first use; every accessor returns an unmodifiable view.</p>
 */
public final class IdentityVocabulary {

    public static final String RESOURCE = "converter_identity.json";

    private IdentityVocabulary() {
    }

    // ------------------------------------------------------------ defaults
    private static final List<String> D_STANDARD_FIELDS = List.of(
            "Username", "usernamemember", "usernameM",
            "Email", "EmailMember", "guestMemberEmail",
            "Phone", "phoneNumber", "hhonorsNumber",
            "Domain", "websiteDomain",
            "generatedemailAddress", "generatedEmail",
            "guestId", "guestID", "memberGuestID",
            "accountId", "accountID",
            "memberId", "memberID",
            "partnerAccountId", "partnerAccountID");
    private static final List<String> D_FREEMAIL_DOMAINS = List.of(
            "yahoo.com", "gmail.com", "hotmail.com", "aol.com", "outlook.com",
            "live.com", "msn.com", "icloud.com", "mail.com", "ymail.com",
            "protonmail.com", "gmx.com", "zoho.com", "me.com", "mac.com");
    private static final List<String> D_BINDABLE_EMAILS = List.of(
            "Email", "EmailAddress", "GeneratedEmail", "generatedemailAddress",
            "generatedemailAddress1", "generatedemailAddress2", "generatedemailAddress3",
            "generatedemailAddress_1", "generatedemailAddress_2", "generatedemailAddress_3",
            "Email1", "Email2", "Email3", "Email_1", "Email_2", "Email_3",
            "EmailMember", "guestMemberEmail");
    private static final List<String> D_BINDABLE_DOMAINS = List.of(
            "Hardcodeddomain", "Domain2", "Domain1", "Domain3", "Domain4",
            "Domain5", "Domain6", "Domain7", "Domain8", "Domain9",
            "Domain_1", "Domain_2", "Domain_3", "Domain_4", "Domain_5",
            "Domain_6", "Domain_7", "Domain_8", "Domain_9");
    private static final List<String> D_NAMED_IDENTITY_KEYS = List.of(
            "email", "emailaddress", "generatedemail", "generatedemailaddress",
            "generatedemailaddress1", "generatedemailaddress2", "generatedemailaddress3",
            "emailmember", "guestmemberemail", "email1", "email2", "email3",
            "email_1", "email_2", "email_3", "hardcodedemail", "updatedemail",
            "updatedmailaddress", "phone", "phonenumber", "hhonorsnumber");
    private static final List<String> D_MEMBER_ENROLL_PATTERNS = List.of(
            "memberhhonorsenroll", "hhonorsenrollmember", "member+hhonorsenroll");
    private static final Map<String, String> D_FIXTURE_LITERALS = Map.of(
            "memberGuestID", "\\d{6,}");
    private static final String D_FROZEN_DOMAIN_KEY = "Hardcodeddomain";
    private static final String D_ALLOWED_DOMAINS_KEY = "ALLOWED_DOMAINS";
    private static final String D_SALESFORCE_ID_FIELD = "(?i)^(.*(sfdc|salesforce).*)id$";
    private static final String D_SALESFORCE_ID_SHAPE = "[A-Za-z0-9]{15,18}";
    private static final String D_SALESFORCE_SESSION_KEY = "sftokenid";

    // -------------------------------------------------------------- loaded
    private static volatile JsonNode loaded;
    private static volatile boolean tried;

    private static JsonNode root() {
        if (!tried) {
            synchronized (IdentityVocabulary.class) {
                if (!tried) {
                    JsonNode n = null;
                    try (InputStream in = IdentityVocabulary.class.getClassLoader()
                            .getResourceAsStream(RESOURCE)) {
                        if (in != null) {
                            n = new ObjectMapper().readTree(in);
                        }
                    } catch (IOException ignored) {
                        n = null;
                    }
                    loaded = n;
                    tried = true;
                }
            }
        }
        return loaded;
    }

    /** Test hook: forget the classpath resource so the next call re-reads it. */
    static synchronized void reset() {
        loaded = null;
        tried = false;
    }

    private static JsonNode node(String... path) {
        JsonNode n = root();
        for (String p : path) {
            if (n == null) {
                return null;
            }
            n = n.get(p);
        }
        return n;
    }

    private static List<String> strings(List<String> dflt, String... path) {
        JsonNode n = node(path);
        if (n == null || !n.isArray()) {
            return dflt;
        }
        List<String> out = new ArrayList<>();
        for (JsonNode e : n) {
            String s = e.asText("");
            if (!s.isEmpty()) {
                out.add(s);
            }
        }
        return Collections.unmodifiableList(out);
    }

    private static String string(String dflt, String... path) {
        JsonNode n = node(path);
        return n == null || !n.isTextual() || n.asText().isEmpty() ? dflt : n.asText();
    }

    // ------------------------------------------------------------ accessors
    /** The ctx namespace the identity pack lives under ({@code Properties}). */
    public static String namespace() {
        return string("Properties", "identity", "namespace");
    }

    /** Fields a generator pack always writes, in emission order. */
    public static String[] standardFields() {
        return strings(D_STANDARD_FIELDS, "identity", "standard_fields").toArray(new String[0]);
    }

    public static Set<String> freemailDomains() {
        return Collections.unmodifiableSet(
                new LinkedHashSet<>(strings(D_FREEMAIL_DOMAINS, "identity", "freemail_domains")));
    }

    public static String[] bindableEmails() {
        return strings(D_BINDABLE_EMAILS, "identity", "bindable_emails").toArray(new String[0]);
    }

    public static String[] bindableDomains() {
        return strings(D_BINDABLE_DOMAINS, "identity", "bindable_domains").toArray(new String[0]);
    }

    /** Lower-cased keys the named regeneration decides; generic passes skip them. */
    public static Set<String> namedIdentityKeys() {
        Set<String> out = new LinkedHashSet<>();
        for (String s : strings(D_NAMED_IDENTITY_KEYS, "identity", "named_identity_keys")) {
            out.add(s.toLowerCase(Locale.ROOT));
        }
        return Collections.unmodifiableSet(out);
    }

    /** The Properties key ReadyAPI keeps fixed across runs (its emails stay on it). */
    public static String frozenDomainKey() {
        return string(D_FROZEN_DOMAIN_KEY, "identity", "frozen_domain_key");
    }

    /** Config key holding the comma-separated allowed identity domains. */
    public static String allowedDomainsKey() {
        return string(D_ALLOWED_DOMAINS_KEY, "identity", "allowed_domains_config_key");
    }

    /**
     * Is this step the member's HHonors enroll? Patterns are matched on the
     * step name lower-cased with non-alphanumerics removed; {@code a+b}
     * means "contains both".
     */
    public static boolean isMemberEnrollStep(String stepName) {
        if (stepName == null || stepName.isEmpty()) {
            return false;
        }
        String n = stepName.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]", "");
        for (String pat : strings(D_MEMBER_ENROLL_PATTERNS, "identity", "member_enroll_step_patterns")) {
            String p = pat.toLowerCase(Locale.ROOT);
            if (p.contains("+")) {
                boolean all = true;
                for (String part : p.split("\\+")) {
                    if (!part.isEmpty() && !n.contains(part)) {
                        all = false;
                        break;
                    }
                }
                if (all) {
                    return true;
                }
            } else if (!p.isEmpty() && n.contains(p)) {
                return true;
            }
        }
        return false;
    }

    /**
     * A saved value the row must keep verbatim (a pre-existing fixture the
     * author refers to, never generated): returns the regex the value must
     * match to count, or null when the field is not a fixture.
     */
    public static String fixtureLiteralShape(String field) {
        if (field == null) {
            return null;
        }
        JsonNode n = node("identity", "fixture_literal_fields");
        if (n != null && n.isObject()) {
            java.util.Iterator<Map.Entry<String, JsonNode>> it = n.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                if (e.getKey().equalsIgnoreCase(field)) {
                    return e.getValue().asText(".*");
                }
            }
            return null;
        }
        for (Map.Entry<String, String> e : D_FIXTURE_LITERALS.entrySet()) {
            if (e.getKey().equalsIgnoreCase(field)) {
                return e.getValue();
            }
        }
        return null;
    }

    // ---------------------------------------------------------- heuristics
    public static boolean salesforceEnabled() {
        JsonNode n = node("heuristics", "salesforce", "enabled");
        return n == null || n.asBoolean(true);
    }

    /** Field names that carry a Salesforce record id. */
    public static Pattern salesforceIdField() {
        return Pattern.compile(string(D_SALESFORCE_ID_FIELD, "heuristics", "salesforce", "id_field_regex"));
    }

    /** What a plausible Salesforce id looks like; anything else saved is the author's literal. */
    public static Pattern salesforceIdShape() {
        return Pattern.compile(string(D_SALESFORCE_ID_SHAPE, "heuristics", "salesforce", "id_shape"));
    }

    /** Fragment naming the captured Salesforce session token key. */
    public static String salesforceSessionKeyFragment() {
        return string(D_SALESFORCE_SESSION_KEY, "heuristics", "salesforce", "session_key_fragment")
                .toLowerCase(Locale.ROOT);
    }
}
