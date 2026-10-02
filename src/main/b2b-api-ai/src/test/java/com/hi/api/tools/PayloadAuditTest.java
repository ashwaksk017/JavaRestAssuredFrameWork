package com.hi.api.tools;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

import org.testng.annotations.Test;

import com.hi.api.rest.utilities.RestUtilities;

/**
 * Render every request payload for every suite, offline, and report what
 * would go on the wire.
 *
 * <p>Not a pass/fail test of the product -- it is an audit. It answers two
 * questions that a live run cannot, because a live run stops at the first
 * failed call and never builds the payloads further down the chain:</p>
 *
 * <ol>
 *   <li>Which payload fields would be sent EMPTY, or null, or still
 *       carrying an unresolved {@code #placeholder#}?</li>
 *   <li>Where a case expects a 4xx, does the payload actually contain
 *       something invalid -- or is a negative test quietly sending a
 *       perfectly valid request?</li>
 * </ol>
 *
 * <p>It renders through {@link RestUtilities#mapJsonValues} -- the real
 * production substitution, including the case-folding and the
 * iterate-until-stable passes -- so the output is what the framework
 * would actually build, not a reimplementation that can drift.</p>
 *
 * <p>Each (template, row) is rendered twice on purpose: once strict, to
 * learn exactly which placeholders had no value, and once non-strict, to
 * get the bytes that would be sent (where unresolved becomes
 * {@code null} / {@code false} / {@code 0}). One call alone cannot tell
 * "the author meant null" from "nothing resolved".</p>
 *
 * <p>Writes {@code target/payload-audit.csv} and a summary alongside it.
 * Deliberately does not assert: the point is the report. Run it with
 * {@code mvn -o test -Dtest=PayloadAuditTest}.</p>
 */
public class PayloadAuditTest {

    private static final Pattern SURVIVING = Pattern.compile("#([A-Za-z0-9_.\\-]+)#");
    /** `"field": ""` and `"field": null` in the rendered JSON. */
    private static final Pattern EMPTY_FIELD = Pattern.compile("\"([^\"]+)\"\\s*:\\s*\"\"");
    private static final Pattern NULL_FIELD = Pattern.compile("\"([^\"]+)\"\\s*:\\s*null");

    private static Path root() {
        // The module root, whether surefire starts here or a level up.
        Path here = Paths.get("").toAbsolutePath();
        return Files.isDirectory(here.resolve("_audit")) ? here : here.resolve("src/main/b2b-api-ai");
    }

    // ------------------------------------------------------------------
    // minimal CSV reading (the emitted CSVs are quote-correct; check_csv_contracts guards that)
    // ------------------------------------------------------------------

    private static List<String> splitCsv(String line) {
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        boolean q = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (q) {
                if (c == '"') {
                    if (i + 1 < line.length() && line.charAt(i + 1) == '"') {
                        cur.append('"');
                        i++;
                    } else {
                        q = false;
                    }
                } else {
                    cur.append(c);
                }
            } else if (c == '"') {
                q = true;
            } else if (c == ',') {
                out.add(cur.toString());
                cur.setLength(0);
            } else {
                cur.append(c);
            }
        }
        out.add(cur.toString());
        return out;
    }

    /** Rows as header-keyed maps; a row spanning newlines inside quotes is joined. */
    private static List<Map<String, String>> readCsv(Path p) throws IOException {
        List<Map<String, String>> rows = new ArrayList<>();
        if (!Files.isRegularFile(p)) {
            return rows;
        }
        List<String> logical = new ArrayList<>();
        StringBuilder pending = new StringBuilder();
        for (String raw : Files.readAllLines(p, StandardCharsets.UTF_8)) {
            pending.append(pending.length() == 0 ? "" : "\n").append(raw);
            long quotes = pending.chars().filter(ch -> ch == '"').count();
            if (quotes % 2 == 0) {
                logical.add(pending.toString());
                pending.setLength(0);
            }
        }
        if (pending.length() > 0) {
            logical.add(pending.toString());
        }
        if (logical.isEmpty()) {
            return rows;
        }
        String head = logical.get(0);
        if (!head.isEmpty() && head.charAt(0) == '﻿') {
            head = head.substring(1);
        }
        List<String> hdr = splitCsv(head);
        for (int r = 1; r < logical.size(); r++) {
            if (logical.get(r).trim().isEmpty()) {
                continue;
            }
            List<String> cells = splitCsv(logical.get(r));
            Map<String, String> row = new LinkedHashMap<>();
            for (int i = 0; i < hdr.size(); i++) {
                row.put(hdr.get(i), i < cells.size() ? cells.get(i) : "");
            }
            rows.add(row);
        }
        return rows;
    }

    // ------------------------------------------------------------------

    private record CaseInfo(String csvPath, String expectedStatus) {
    }

    @Test
    public void auditEveryPayload() throws Exception {
        Path root = root();
        Path auditDir = root.resolve("_audit");
        Path outCsv = root.resolve("target/payload-audit.csv");
        Path outTxt = root.resolve("target/payload-audit-summary.txt");
        Files.createDirectories(outCsv.getParent());

        List<String> report = new ArrayList<>();
        report.add("suite,case,step,template,csv_row,expected_status,finding,detail");

        int payloads = 0, withEmpty = 0, withNull = 0, withUnresolved = 0;
        int negativeCases = 0, negativeLookingValid = 0, ctxSupplied = 0;
        Set<String> suites = new LinkedHashSet<>();

        List<Path> templateAudits;
        try (Stream<Path> st = Files.walk(auditDir, 2)) {
            templateAudits = st.filter(p -> p.getFileName().toString().equals("templates.csv"))
                    .sorted().toList();
        }

        for (Path tplAudit : templateAudits) {
            String suite = tplAudit.getParent().getFileName().toString();
            suites.add(suite);

            // (case|placeholder) -> kind, from the converter's own audit.
            // kind=csv means the CSV is supposed to supply it, so a blank
            // column there is a real defect. kind=runtime/config means ctx
            // or program_configuration fills it and a blank column is
            // expected -- the same placeholder is csv in one case and
            // runtime in another, so this has to be per (case, placeholder)
            // and cannot be decided from the name alone.
            Map<String, String> kinds = new LinkedHashMap<>();
            for (Map<String, String> ph : readCsv(tplAudit.getParent().resolve("placeholders.csv"))) {
                kinds.put(ph.getOrDefault("case", "") + "|" + ph.getOrDefault("placeholder", ""),
                        ph.getOrDefault("kind", ""));
            }

            Map<String, CaseInfo> cases = new LinkedHashMap<>();
            Path mapping = tplAudit.getParent().resolve("case_to_method_mapping.csv");
            for (Map<String, String> m : readCsv(mapping)) {
                cases.put(m.getOrDefault("soapui_case", ""),
                        new CaseInfo(m.getOrDefault("csv_path", ""),
                                m.getOrDefault("expected_status", "")));
            }

            for (Map<String, String> t : readCsv(tplAudit)) {
                String caseName = t.getOrDefault("case", "");
                String step = t.getOrDefault("step", "");
                String template = t.getOrDefault("template", "");
                if (template.isEmpty()) {
                    continue;
                }
                Path tplFile = root.resolve("src/main/resources").resolve(template);
                if (!Files.isRegularFile(tplFile)) {
                    report.add(csv(suite, caseName, step, template, "", "",
                            "TEMPLATE_MISSING", tplFile.toString()));
                    continue;
                }
                String tplText = Files.readString(tplFile, StandardCharsets.UTF_8);

                CaseInfo info = cases.get(caseName);
                if (info == null || info.csvPath().isEmpty()) {
                    report.add(csv(suite, caseName, step, template, "", "",
                            "NO_CSV_FOR_CASE", "case not in case_to_method_mapping"));
                    continue;
                }
                List<Map<String, String>> rows = readCsv(root.resolve(info.csvPath()));
                if (rows.isEmpty()) {
                    report.add(csv(suite, caseName, step, template, "", info.expectedStatus(),
                            "CSV_HAS_NO_ROWS", info.csvPath()));
                    continue;
                }

                for (int r = 0; r < rows.size(); r++) {
                    Map<String, String> row = rows.get(r);
                    String rowId = "row" + (r + 1);
                    payloads++;

                    // expected status for THIS step beats the case-level one
                    String expected = firstNonEmpty(
                            row.get("expected_" + step + "_status_code"),
                            row.get("expected_status_code"),
                            info.expectedStatus());

                    // strict: which placeholders had nothing behind them
                    List<String> unresolved = new ArrayList<>();
                    try {
                        RestUtilities.mapJsonValues(tplText, row, true, true);
                    } catch (Exception e) {
                        String msg = String.valueOf(e.getMessage());
                        for (Matcher m = SURVIVING.matcher(msg); m.find(); ) {
                            unresolved.add(m.group(1));
                        }
                        if (unresolved.isEmpty()) {
                            unresolved.add(msg.length() > 120 ? msg.substring(0, 120) : msg);
                        }
                    }

                    // non-strict: the bytes that would actually be sent
                    String wire;
                    try {
                        wire = RestUtilities.mapJsonValues(tplText, row, false, true);
                    } catch (Exception e) {
                        report.add(csv(suite, caseName, step, template, rowId, expected,
                                "RENDER_FAILED", String.valueOf(e.getMessage())));
                        continue;
                    }

                    Set<String> empties = matches(EMPTY_FIELD, wire);
                    Set<String> nulls = matches(NULL_FIELD, wire);
                    Set<String> left = matches(SURVIVING, wire);

                    // A placeholder with no value here is only a DEFECT when
                    // the CSV actually owns that column. At runtime the map is
                    // the row merged with ctx -- tokens, ids extracted from
                    // earlier steps, generated identities -- and this audit has
                    // no ctx. So:
                    //   column present but blank -> an empty value really is
                    //     going on the wire (the finding we are looking for)
                    //   column absent           -> supplied from ctx at run
                    //     time; not a finding, counted separately so the
                    //     numbers stay honest.
                    List<String> blankColumn = new ArrayList<>();
                    List<String> fromCtx = new ArrayList<>();
                    for (String key : dedupe(unresolved)) {
                        String kind = kinds.get(caseName + "|" + key);
                        if ("csv".equals(kind)) {
                            // the CSV owns this value and has not got one
                            blankColumn.add(key);
                        } else {
                            fromCtx.add(key + (kind == null || kind.isEmpty() ? "" : "[" + kind + "]"));
                        }
                    }
                    if (!blankColumn.isEmpty()) {
                        withUnresolved++;
                        report.add(csv(suite, caseName, step, template, rowId, expected,
                                "EMPTY_CSV_COLUMN", String.join(" ", blankColumn)));
                    }
                    if (!fromCtx.isEmpty()) {
                        ctxSupplied++;
                        report.add(csv(suite, caseName, step, template, rowId, expected,
                                "FROM_CTX_AT_RUNTIME", String.join(" ", fromCtx)));
                    }
                    if (!empties.isEmpty()) {
                        withEmpty++;
                        report.add(csv(suite, caseName, step, template, rowId, expected,
                                "EMPTY_VALUE_SENT", String.join(" ", empties)));
                    }
                    if (!nulls.isEmpty()) {
                        withNull++;
                        report.add(csv(suite, caseName, step, template, rowId, expected,
                                "NULL_VALUE_SENT", String.join(" ", nulls)));
                    }
                    if (!left.isEmpty()) {
                        report.add(csv(suite, caseName, step, template, rowId, expected,
                                "PLACEHOLDER_ON_THE_WIRE", String.join(" ", left)));
                    }

                    // Item 4: a case that expects a 4xx should be sending
                    // something the server can reject. A fully clean payload
                    // under a 4xx expectation means either the invalidity
                    // lives somewhere this audit cannot see (a query param,
                    // a header, an omitted field) or the negative case is
                    // not actually negative.
                    if (is4xx(expected)) {
                        negativeCases++;
                        if (empties.isEmpty() && nulls.isEmpty() && left.isEmpty()
                                && blankColumn.isEmpty()) {
                            negativeLookingValid++;
                            report.add(csv(suite, caseName, step, template, rowId, expected,
                                    "NEGATIVE_CASE_VALID_PAYLOAD",
                                    "expects " + expected + " but body has no empty/null/unresolved field"));
                        }
                    }
                }
            }
        }

        Files.write(outCsv, report, StandardCharsets.UTF_8);

        List<String> summary = new ArrayList<>();
        summary.add("payload audit");
        summary.add("suites                        : " + suites.size() + " " + suites);
        summary.add("payloads rendered             : " + payloads);
        summary.add("with an EMPTY field           : " + withEmpty);
        summary.add("with a NULL field             : " + withNull);
        summary.add("CSV-owned value MISSING       : " + withUnresolved + "   <- real: kind=csv but the column is blank");
        summary.add("supplied by ctx/config at run : " + ctxSupplied + "   (expected; this audit has no ctx)");
        summary.add("4xx-expecting payloads        : " + negativeCases);
        summary.add("  ...of those, fully valid    : " + negativeLookingValid);
        summary.add("");
        summary.add("detail: " + outCsv.getFileName() + " (" + (report.size() - 1) + " finding rows)");
        Files.write(outTxt, summary, StandardCharsets.UTF_8);
        summary.forEach(System.out::println);
    }

    private static boolean is4xx(String s) {
        if (s == null) {
            return false;
        }
        Matcher m = Pattern.compile("\\b4\\d\\d\\b").matcher(s);
        return m.find();
    }

    private static String firstNonEmpty(String... xs) {
        for (String x : xs) {
            if (x != null && !x.trim().isEmpty()) {
                return x.trim();
            }
        }
        return "";
    }

    private static Set<String> matches(Pattern p, String text) {
        Set<String> out = new LinkedHashSet<>();
        for (Matcher m = p.matcher(text); m.find(); ) {
            out.add(m.group(1));
        }
        return out;
    }

    private static List<String> dedupe(List<String> in) {
        return new ArrayList<>(new LinkedHashSet<>(in));
    }

    private static String csv(String... cells) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < cells.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            String c = cells[i] == null ? "" : cells[i];
            sb.append('"').append(c.replace("\"", "\"\"")).append('"');
        }
        return sb.toString();
    }
}
