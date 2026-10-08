package com.hi.api.rest.utilities.phase;

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.hi.api.config.Config;
import com.hi.api.rest.utilities.ResponseAsserts;
import com.hi.api.rest.utilities.RestStep;
import com.hi.api.rest.utilities.RestUtilities;
import com.hi.api.support.ImportedScenario;

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
        if (trailingParamIsAuthorEmpty(spec, c)) {
            step.allowAuthorEmptyTrailingSegment();
            url = withoutEmptyTrailingSegment(url);
        }
        if (trailingParamIsAuthorEmpty(spec, c)) {
            // The generated client builds the wire URL from the arguments;
            // tell the route filler the empty tail is the author's.
            final String sendUrl = url;
            res = com.hi.api.rest.ApiRoutes.withAuthorEmptyTail(
                    () -> send(spec.verb, step, sendUrl, exchange));
        } else {
            res = send(spec.verb, step, url, exchange);
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
                    ResponseAsserts.jsonEqualsAt(c.softAssert, res, c.ctx, c.row, spec.step,
                            k.jsonPath, k.expected, k.column);
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

    private static Response send(String verb, RestStep step, String url,
                                 RestStep.Exchange exchange) throws Exception {
        switch (verb) {
            case "GET":    return step.get(url, exchange);
            case "PUT":    return step.put(url, exchange);
            case "PATCH":  return step.patch(url, exchange);
            case "DELETE": return step.delete(url, exchange);
            default:       return step.post(url, exchange);
        }
    }

    /** Test seam: the resolved URL without sending anything. */
    public static String resolvedPathForTest(PhaseSpec spec, PhaseContext c) {
        return resolvedPath(spec, c);
    }

    /**
     * Does the path END in a parameter the ReadyAPI author saved with no value?
     *
     * <p>{@code POST .../partneraccounts/{partneraccount}} is recorded with
     * {@code partneraccount=""}: the project posts to the collection. Only the
     * LAST parameter qualifies, and only when its Ref says the emptiness is the
     * author's ({@link Ref#authorEmpty}); an empty id in the middle of a path,
     * or one an extract failed to supply, is still a broken path.</p>
     */
    static boolean trailingParamIsAuthorEmpty(PhaseSpec spec, PhaseContext c) {
        String p = spec.path;
        if (p == null || !p.endsWith("}")) {
            return false;
        }
        int params = 0;
        for (int i = p.indexOf('{'); i >= 0; i = p.indexOf('{', i + 1)) {
            params++;
        }
        return params > 0 && spec.arg(params - 1).authorEmpty(c);
    }

    /**
     * Drop the slash an author-empty last parameter leaves behind.
     *
     * <p>{@code .../partneraccounts/{partneraccount}} with the parameter saved
     * empty is recorded by ReadyAPI as {@code .../partneraccounts}: it drops
     * the empty segment together with its slash. Substituting "" keeps the
     * slash, and the API answers 404 for {@code .../partneraccounts/}.</p>
     */
    public static String withoutEmptyTrailingSegment(String url) {
        if (url == null) {
            return null;
        }
        int q = url.indexOf('?');
        String path = q >= 0 ? url.substring(0, q) : url;
        String rest = q >= 0 ? url.substring(q) : "";
        if (path.length() > 1 && path.endsWith("/")) {
            path = path.substring(0, path.length() - 1);    // one, as ApiRoutes.fill does
        }
        return path + rest;
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
