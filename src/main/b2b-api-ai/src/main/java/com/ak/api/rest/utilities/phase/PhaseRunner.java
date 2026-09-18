package com.ak.api.rest.utilities.phase;

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.ak.api.config.Config;
import com.ak.api.rest.utilities.ResponseAsserts;
import com.ak.api.rest.utilities.RestStep;
import com.ak.api.rest.utilities.RestUtilities;
import com.ak.api.support.ImportedScenario;

import io.restassured.response.Response;

/**
 * Runs one {@link PhaseSpec} through {@link RestStep}.
 *
 * <p>This is the 30-line body the converter used to copy per phase, written
 * once. The order is the generated order, on purpose: build the RestStep
 * chain, send through the typed client lambda, record the response, keep
 * the resolved request, copy extracts into ctx, run the CSV-driven checks,
 * then the case's translated hook. Anything the generated body did that is
 * not here is a behaviour change and must show up in the parity run.</p>
 */
public final class PhaseRunner {

    private static final Logger LOG = LoggerFactory.getLogger(PhaseRunner.class);

    private PhaseRunner() {
    }

    /**
     * @param exchange the typed client call for this shape, built by the
     *     generated engine method -- it is the only per-shape code left
     */
    public static Response run(PhaseSpec spec, PhaseContext c, RestStep.Exchange exchange)
            throws Exception {
        if (spec.isHookOnly()) {
            if (spec.hook != null) {
                spec.hook.after(null, c);
            }
            return null;
        }
        RestStep step = RestStep.exec(c.ctx, c.row, c.softAssert, c.holder, c.testCaseId)
                .name(spec.step);
        if (spec.template != null) {
            step.template(spec.template);
        }
        if (spec.regenIdentity) {
            step.regenIdentity();
        }
        for (Map.Entry<String, String> q : spec.query.entrySet()) {
            step.query(q.getKey(), q.getValue());
        }
        for (Map.Entry<String, String> h : spec.headers.entrySet()) {
            step.header(h.getKey(), h.getValue());
        }
        step.expectedStatus(spec.expectedStatus);
        if (spec.pollJsonPath != null) {
            step.pollUntilJsonPresent(spec.pollJsonPath,
                    Config.getInt(spec.pollConfigKey, (int) spec.pollDefaultMs));
        }

        Response res;
        String url = resolvedPath(spec, c);
        switch (spec.verb) {
            case "GET":    res = step.get(url, exchange); break;
            case "PUT":    res = step.put(url, exchange); break;
            case "PATCH":  res = step.patch(url, exchange); break;
            case "DELETE": res = step.delete(url, exchange); break;
            default:       res = step.post(url, exchange); break;
        }
        c.record(spec.step, res);

        // The generated body always kept the resolved request under
        // <step>_RawRequest; ${step#RawRequest#...} refs read it later.
        String raw = RestStep.lastResolvedBody();
        ImportedScenario.putExtracted(c.ctx, spec.step + "_RawRequest", raw == null ? "" : raw);

        // Checks BEFORE extracts: the old body ran its assertions, then the
        // auto-extracts, then any transfer step. Same order here.
        for (PhaseSpec.Check k : spec.checks) {
            switch (k.kind) {
                case EQUALS:
                    ResponseAsserts.jsonEquals(c.softAssert, res, c.ctx, c.row, spec.step, k.jsonPath, k.expected);
                    break;
                case EXISTS:
                    ResponseAsserts.jsonExists(c.softAssert, res, c.row, spec.step, k.jsonPath);
                    break;
                case ABSENT:
                    ResponseAsserts.jsonAbsent(c.softAssert, res, k.jsonPath);
                    break;
                case COUNT:
                    ResponseAsserts.jsonCount(c.softAssert, res, c.row, spec.step, k.jsonPath,
                            RestUtilities.parseIntOrDefault(k.expected, 0, spec.step + ".count"));
                    break;
                case TREE_EQUALS:
                    ResponseAsserts.jsonTreeEquals(c.softAssert, res, c.ctx, c.row, spec.step, k.jsonPath, k.expected);
                    break;
                case VALUE_IN_RESPONSE:
                    ResponseAsserts.valueInResponse(c.softAssert, res, k.expected, k.jsonPath, spec.step);
                    break;
                default:
                    throw new IllegalStateException("unknown check kind " + k.kind);
            }
        }

        for (PhaseSpec.Extract e : spec.extracts) {
            String value;
            switch (e.kind) {
                case WHOLE:
                    value = RestUtilities.getResponseAsString(res);
                    break;
                case RAW_REQUEST:
                    value = raw;
                    break;
                case RAW_REQUEST_PATH:
                    value = RestUtilities.safeJsonExtractFromString(raw, e.path);
                    break;
                default:
                    value = RestUtilities.safeJsonExtract(res, e.path);
            }
            ImportedScenario.putExtracted(c.ctx, e.key, value == null ? "" : value);
        }

        if (spec.hook != null) {
            spec.hook.after(res, c);
        }
        return res;
    }

    /** Test seam: the resolved URL without sending anything. */
    public static String resolvedPathForTest(PhaseSpec spec, PhaseContext c) {
        return resolvedPath(spec, c);
    }

    /** The path template with every {@code {param}} replaced by its resolved Ref, in order. */
    static String resolvedPath(PhaseSpec spec, PhaseContext c) {
        StringBuilder out = new StringBuilder();
        int i = 0;
        int argIdx = 0;
        String p = spec.path;
        while (i < p.length()) {
            int open = p.indexOf('{', i);
            if (open < 0) {
                out.append(p, i, p.length());
                break;
            }
            int close = p.indexOf('}', open);
            if (close < 0) {
                out.append(p, i, p.length());
                break;
            }
            out.append(p, i, open);
            String v = spec.arg(argIdx++).resolve(c);
            if (v.isEmpty()) {
                LOG.warn(" .. phase {} path parameter {} resolved EMPTY ({})",
                        spec.step, p.substring(open, close + 1), spec.arg(argIdx - 1).describe());
            }
            out.append(v);
            i = close + 1;
        }
        return out.toString();
    }
}
