package com.ak.api.rest.utilities;

import java.util.regex.Pattern;

/**
 * Strip identifying values out of text that leaves the machine.
 *
 * <p>Split out of {@code FailureDigestListener} because response BODIES now
 * reach the digest, and that listener's masking was tuned for one-line
 * assertion messages. It did not mask bare hostnames -- only {@code www.}
 * prefixed ones -- so real customer domains were already appearing in digests
 * pasted into issues. A full account-create body carries far more: email
 * domains, website domains, addresses, account ids.</p>
 *
 * <p>Deliberately AGGRESSIVE. A digest exists to show the SHAPE of a failure
 * -- "constraint violation on field emailAddress" -- and that survives
 * masking intact. An over-masked digest costs one log lookup; an
 * under-masked one published to a public repository cannot be recalled.</p>
 */
public final class ResponseMasking {

    private ResponseMasking() {
    }

    private static final Pattern[] MASKS = {
        // UUIDs
        Pattern.compile("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                + "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
        // emails (before hostnames, or the host half would be masked first)
        Pattern.compile("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}"),
        // ISO timestamps
        Pattern.compile("\\b\\d{4}-\\d{2}-\\d{2}[T ]\\d{2}:\\d{2}:\\d{2}\\S*"),
        // bearer/JWT-ish blobs
        Pattern.compile("\\b[A-Za-z0-9_-]{24,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}"),
        // ANY hostname, not just www. -- this is the gap that leaked domains
        Pattern.compile("\\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\\.)+"
                + "(?:com|net|org|io|co|uk|de|mx|in|testing|gov|edu)\\b"),
        // long digit runs (account / guest / member ids)
        Pattern.compile("\\b\\d{5,}\\b"),
    };

    /** Replace every identifying value with {@code <*>}. Null-safe. */
    public static String mask(String s) {
        if (s == null) {
            return null;
        }
        String out = s;
        for (Pattern p : MASKS) {
            out = p.matcher(out).replaceAll("<*>");
        }
        return out;
    }
}
