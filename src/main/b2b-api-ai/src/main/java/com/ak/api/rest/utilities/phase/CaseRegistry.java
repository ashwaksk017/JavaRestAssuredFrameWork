package com.ak.api.rest.utilities.phase;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Supplier;

/**
 * The phase specs of every converted case, by case id.
 *
 * <p>A shared entry class ({@code Onboarding.start(row, caseId)}) serves
 * many cases, so the chain {@code .enrollGuest().createProgramAccount()}
 * cannot carry case data itself. It carries NAMES; this registry holds,
 * per case, which spec each name means. The converter emits one
 * {@code <TestClass>Phases} per test class that registers its cases here.</p>
 *
 * <h2>Resolution rule (agreed)</h2>
 * <ul>
 *   <li>{@code readProgramAccount()} -- the ONLY phase of that vocabulary
 *       name in the case. Two or more is an error naming the alternatives:
 *       never positional, never hidden state.</li>
 *   <li>{@code readProgramAccount("get_account_after_activate")} -- by the
 *       ReadyAPI step name, which is also the CSV column prefix and the
 *       {@code step=} in the log.</li>
 * </ul>
 */
public final class CaseRegistry {

    /** One phase of one case. */
    public static final class Entry {
        public final String vocab;
        public final String step;
        public final boolean verify;
        private final Supplier<PhaseSpec> supplier;
        private PhaseSpec built;

        Entry(String vocab, String step, boolean verify, Supplier<PhaseSpec> supplier) {
            this.vocab = vocab;
            this.step = step;
            this.verify = verify;
            this.supplier = supplier;
        }

        public synchronized PhaseSpec spec() {
            if (built == null) {
                built = supplier.get();
            }
            return built;
        }
    }

    /** All phases of one case, in ReadyAPI order. */
    public static final class Case {
        public final String caseId;
        private final List<Entry> entries = new ArrayList<>();

        Case(String caseId) {
            this.caseId = caseId;
        }

        public Case phase(String vocab, String step, Supplier<PhaseSpec> spec) {
            entries.add(new Entry(vocab, step, false, spec));
            return this;
        }

        public Case verify(String vocab, String step, Supplier<PhaseSpec> spec) {
            entries.add(new Entry(vocab, step, true, spec));
            return this;
        }

        public List<Entry> entries() {
            return Collections.unmodifiableList(entries);
        }

        /** The only phase named {@code vocab}; loud when there are several. */
        public PhaseSpec only(String vocab, boolean verify) {
            List<Entry> hits = named(vocab, verify);
            if (hits.size() == 1) {
                return hits.get(0).spec();
            }
            if (hits.isEmpty()) {
                throw new IllegalStateException("case `" + caseId + "` has no "
                        + (verify ? "verify" : "phase") + " `" + vocab + "`; it has: " + names(verify));
            }
            StringBuilder alts = new StringBuilder();
            for (Entry e : hits) {
                alts.append(alts.length() == 0 ? "" : ", ").append('"').append(e.step).append('"');
            }
            throw new IllegalStateException("case `" + caseId + "` runs `" + vocab + "` "
                    + hits.size() + " times; say which one: " + vocab + "(" + alts + ")");
        }

        /** The phase named {@code vocab} whose ReadyAPI step is {@code step}. */
        public PhaseSpec named(String vocab, String step, boolean verify) {
            for (Entry e : named(vocab, verify)) {
                if (e.step.equals(step)) {
                    return e.spec();
                }
            }
            throw new IllegalStateException("case `" + caseId + "` has no "
                    + (verify ? "verify" : "phase") + " `" + vocab + "(\"" + step + "\")`; it has: "
                    + names(verify));
        }

        private List<Entry> named(String vocab, boolean verify) {
            List<Entry> out = new ArrayList<>();
            for (Entry e : entries) {
                if (e.verify == verify && e.vocab.equals(vocab)) {
                    out.add(e);
                }
            }
            return out;
        }

        private String names(boolean verify) {
            StringBuilder sb = new StringBuilder();
            for (Entry e : entries) {
                if (e.verify != verify) {
                    continue;
                }
                sb.append(sb.length() == 0 ? "" : ", ").append(e.vocab).append("(\"").append(e.step).append("\")");
            }
            return sb.length() == 0 ? "(none)" : sb.toString();
        }
    }

    private static final Map<String, Case> CASES = Collections.synchronizedMap(new LinkedHashMap<>());

    private CaseRegistry() {
    }

    /** Called by generated {@code <TestClass>Phases} classes. Idempotent per case id. */
    public static Case register(String caseId) {
        Case c = new Case(caseId);
        CASES.put(caseId, c);
        return c;
    }

    /** The case, or null when nothing registered it (a hand-written flow). */
    public static Case forCase(String caseId) {
        return caseId == null ? null : CASES.get(caseId);
    }

    public static int size() {
        return CASES.size();
    }
}
