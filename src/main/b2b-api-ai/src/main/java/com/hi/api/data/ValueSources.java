package com.hi.api.data;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import com.hi.api.config.Config;

/**
 * Where a story test's value comes from when its data sheet does not have
 * it -- the sources the converted tests already use, for the hand-written
 * path (ManualBody, StoryTestBase):
 *
 * <ul>
 *   <li>{@link #fromConfig}: program_configuration.json (what the Settings
 *       tab writes) under the placeholder's own name, its dotted forms
 *       ({@code enroll_source_code} -> {@code enroll.source_code}) and
 *       {@code test_data.<name>} -- the alias forms the converted mergedRow
 *       and testData use;</li>
 *   <li>{@link #convertedEnrollCodes}: the enrolment source and property
 *       codes the converted suites' enrol bodies send. They exist nowhere
 *       else: those bodies carry them as literals;</li>
 *   <li>{@link #frozenDomain}: an e-mail domain the converted suites' data
 *       already uses (test_data_defaults), the target's allowlist knowing
 *       it.</li>
 * </ul>
 *
 * <p>Read from THIS project at run time -- nothing company-specific is
 * written in the code, which is public. Cached once a JVM.</p>
 */
public final class ValueSources {

    private ValueSources() {
    }

    /**
     * The configuration's value for a placeholder name, and the key it was
     * found under: {@code {value, key}}, or null.
     */
    public static String[] fromConfig(String name) {
        if (name == null || name.isBlank()) {
            return null;
        }
        for (String key : configKeysFor(name.trim())) {
            String v = Config.get(key, "");
            if (v != null && !v.isBlank()) {
                return new String[] {v.trim(), key};
            }
        }
        return null;
    }

    /** The keys a name is looked up under, in order. */
    static List<String> configKeysFor(String name) {
        Set<String> keys = new LinkedHashSet<>();
        keys.add(name);
        for (int i = name.indexOf('_'); i > 0; i = name.indexOf('_', i + 1)) {
            keys.add(name.substring(0, i) + "." + name.substring(i + 1));   // enroll_source_code -> enroll.source_code
        }
        keys.add(name.replace('_', '.'));
        keys.add("test_data." + name);
        return new ArrayList<>(keys);
    }

    private static final Pattern SOURCE = Pattern.compile("\"enrollSourceCode\"\\s*:\\s*\"([A-Z0-9]{3,10})\"");
    private static final Pattern PROP = Pattern.compile("\"propCode\"\\s*:\\s*\"([A-Z0-9]{3,8})\"");
    private static volatile String[] enrollCodes;

    /**
     * {@code {source code, property code, where}} that the most converted
     * enrol bodies send (src/main/resources/templates/<suite>/...), or null
     * when this project has none. Only real codes count -- a body carrying
     * a name instead ({@code "enrollSourceCode": "enrollSourceCode"}) does not.
     */
    public static String[] convertedEnrollCodes() {
        String[] got = enrollCodes;
        if (got == null) {
            got = scanEnrollCodes(templatesDir());
            enrollCodes = got;
        }
        return got.length == 0 ? null : got;
    }

    static String[] scanEnrollCodes(Path dir) {
        Map<String, Integer> pairs = new HashMap<>();
        Map<String, String> example = new HashMap<>();
        if (dir != null && Files.isDirectory(dir)) {
            try (Stream<Path> files = Files.walk(dir)) {
                for (Path p : (Iterable<Path>) files.filter(f -> f.toString().endsWith(".json"))::iterator) {
                    String text = read(p);
                    Matcher s = SOURCE.matcher(text);
                    Matcher q = PROP.matcher(text);
                    if (s.find() && q.find()) {
                        String pair = s.group(1) + "/" + q.group(1);
                        pairs.merge(pair, 1, Integer::sum);
                        example.putIfAbsent(pair, dir.relativize(p).toString().replace('\\', '/'));
                    }
                }
            } catch (IOException | RuntimeException ignored) {
                // no codes from here
            }
        }
        String best = null;
        for (Map.Entry<String, Integer> e : pairs.entrySet()) {
            if (best == null || e.getValue() > pairs.get(best)
                    || (e.getValue().equals(pairs.get(best)) && e.getKey().compareTo(best) < 0)) {
                best = e.getKey();
            }
        }
        if (best == null) {
            return new String[0];
        }
        String[] sp = best.split("/", 2);
        return new String[] {sp[0], sp[1], pairs.get(best) + " converted enrol bodies, e.g. templates/"
                + example.get(best)};
    }

    /**
     * Never taken from another suite's body: a person's identity or a
     * credential must be this test's own (generated, or from its data sheet).
     */
    private static final Pattern PRIVATE_FIELD = Pattern.compile(
            "(?i).*(password|username|user_name|secret|token|assertion|email|guestid|memberid|accountid"
                    + "|hhonorsnumber|confirmation|otp|totp|ssn|taxid|salesforceid).*");
    private static final Map<String, String[]> LITERALS = new java.util.concurrent.ConcurrentHashMap<>();

    /**
     * {@code {value, where}}: the value the converted suites' bodies send
     * most for this JSON field ({@code "ratePlanName": "..."} in
     * src/main/resources/templates/**), or null -- none, a placeholder
     * only, an empty string only, or a field that is a person's identity
     * or a credential. The last source for a template's #name#, after the
     * data sheet, the test's own values and program_configuration.json: a
     * field the converted tests always send with a working value is not
     * left out or invented here.
     */
    public static String[] convertedLiteral(String field) {
        if (field == null || field.isBlank() || PRIVATE_FIELD.matcher(field).matches()) {
            return null;
        }
        String[] got = LITERALS.computeIfAbsent(field, f -> scanLiteral(templatesDir(), f));
        return got.length == 0 ? null : got;
    }

    static String[] scanLiteral(Path dir, String field) {
        if (PRIVATE_FIELD.matcher(field).matches()) {
            return new String[0];
        }
        Pattern p = Pattern.compile("\"" + Pattern.quote(field) + "\"\\s*:\\s*\"([^\"\\\\]*)\"");
        Map<String, Integer> seen = new HashMap<>();
        Map<String, String> example = new HashMap<>();
        if (dir != null && Files.isDirectory(dir)) {
            try (Stream<Path> files = Files.walk(dir)) {
                for (Path f : (Iterable<Path>) files.filter(x -> x.toString().endsWith(".json"))::iterator) {
                    Matcher m = p.matcher(read(f));
                    while (m.find()) {
                        String v = m.group(1).trim();
                        if (v.isEmpty() || v.matches(".*(#[^#]+#|<<.*>>|\\$\\{.*\\}|@[^@]+@|%[^%]+%).*")) {
                            continue;
                        }
                        seen.merge(v, 1, Integer::sum);
                        example.putIfAbsent(v, dir.relativize(f).toString().replace('\\', '/'));
                    }
                }
            } catch (IOException | RuntimeException ignored) {
                // none from here
            }
        }
        String best = null;
        for (Map.Entry<String, Integer> e : seen.entrySet()) {
            if (best == null || e.getValue() > seen.get(best)
                    || (e.getValue().equals(seen.get(best)) && e.getKey().compareTo(best) < 0)) {
                best = e.getKey();
            }
        }
        return best == null ? new String[0]
                : new String[] {best, seen.get(best) + " converted bodies, e.g. templates/" + example.get(best)};
    }

    private static final Pattern DOMAIN_KEY = Pattern.compile("(?i)^Properties\\.(Hardcodeddomain|Domain)$");
    private static volatile String domain;

    /**
     * The e-mail domain the converted suites' data uses most
     * (test_data_defaults/*.json, Properties.Hardcodeddomain / Domain), or null.
     */
    public static String frozenDomain() {
        String got = domain;
        if (got == null) {
            got = scanDomain(Paths.get(System.getProperty("user.dir"), "src", "main", "resources", "test_data_defaults"));
            domain = got;
        }
        return got.isEmpty() ? null : got;
    }

    static String scanDomain(Path dir) {
        Map<String, Integer> seen = new HashMap<>();
        if (dir != null && Files.isDirectory(dir)) {
            try (Stream<Path> files = Files.list(dir)) {
                for (Path p : (Iterable<Path>) files.filter(f -> f.toString().endsWith(".json"))::iterator) {
                    com.fasterxml.jackson.databind.JsonNode root =
                            new com.fasterxml.jackson.databind.ObjectMapper().readTree(read(p));
                    root.fields().forEachRemaining(e -> {
                        String v = e.getValue().asText("").trim().toLowerCase(java.util.Locale.ROOT)
                                .replaceFirst("^www\\.", "");
                        if (DOMAIN_KEY.matcher(e.getKey()).matches() && v.matches("[a-z0-9-]+(\\.[a-z0-9-]+)+")) {
                            seen.merge(v, 1, Integer::sum);
                        }
                    });
                }
            } catch (IOException | RuntimeException ignored) {
                // none from here
            }
        }
        return seen.entrySet().stream()
                .sorted((a, b) -> b.getValue().equals(a.getValue()) ? a.getKey().compareTo(b.getKey())
                        : b.getValue() - a.getValue())
                .map(Map.Entry::getKey).findFirst().orElse("");
    }

    private static Path templatesDir() {
        return Paths.get(System.getProperty("user.dir"), "src", "main", "resources", "templates");
    }

    private static String read(Path p) {
        try {
            return new String(Files.readAllBytes(p), StandardCharsets.UTF_8);
        } catch (IOException e) {
            return "";
        }
    }
}
