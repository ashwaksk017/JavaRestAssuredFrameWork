"""Every inline exchange a --classic tree emits has the right shape.

WHY THIS EXISTS
---------------
Stage 2's review turned up three faults in the emitted request, and all
three compiled:

  * the Authorization value was built with the bearer helper that
    prefixes unconditionally, against ctx tokens that already carry the
    scheme -- `Bearer Bearer ...`, 401 on every call;
  * `.headers(auth).headers(extra)` was chained, leaving authentication
    dependent on whether RestAssured merges or replaces a repeated
    `headers(Map)`;
  * Salesforce data steps were routed at `Config.baseUrl()` because the
    fallback only covered the OAuth token POST -- the right shape, the
    wrong host.

None is reachable by the existing gate. The 18 phase-structural checks
are N/A on a classic tree, `java-compile` accepts all three, and a
wrong host or a doubled scheme only shows up as a run that fails for
reasons nobody connects to the converter.

So this reads the emitted request itself. It is deliberately narrow:
these are the invariants of the shape classic emits, not a style guide.

    python tools/check_classic_shape.py [--root .]

On a tree with no classic suites it exits 0 saying it checked nothing,
which `verify_all` reports as SKIP rather than PASS -- a check that
passes for having found nothing is counted as evidence, and should not
be.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

GIVEN = "io.restassured.RestAssured.given()"
# A host written into the request instead of resolved from config.
LITERAL_HOST = re.compile(r'"https?://')


def classic_suites(root: str) -> list:
    try:
        with open(os.path.join(root, "_audit", "emit_mode.json"),
                  encoding="utf-8") as fh:
            modes = json.load(fh).get("suites") or {}
    except (OSError, ValueError):
        return []
    return sorted(k for k, v in modes.items() if v == "classic")


# Only what the converter writes. `tests/imported/<suite>/...` is the
# emitted tree; BaseApiTest and anything hand-written beside it are
# author-owned and have their own reasons for the shape they use.
EMITTED = os.sep + os.path.join("tests", "imported") + os.sep


def test_files(root: str):
    base = os.path.join(root, "src", "test", "java")
    for dirpath, _d, files in os.walk(base):
        if EMITTED not in dirpath + os.sep:
            continue
        for fn in sorted(files):
            if fn.endswith("Test.java"):
                yield os.path.join(dirpath, fn)


BUILDER = "RestStep.exec("


def exchanges(lines: list):
    """Each inline exchange as (start_line, [lines]).

    From the statement that OPENS the exchange to the `;` that closes
    it. Not from `given()`: the verb call and its
    `(body, q, h, url) ->` signature are above that line, and a block
    starting at `given()` reported every correct exchange as missing its
    lambda.
    """
    i = 0
    while i < len(lines):
        if GIVEN not in lines[i]:
            i += 1
            continue
        # Back up to the builder that opens this statement. Bounded so a
        # hand-written given() with no builder still yields a block
        # rather than swallowing the file above it.
        start = i
        for k in range(i, max(-1, i - 40), -1):
            if BUILDER in lines[k]:
                start = k
                break
        block, j = [], start
        while j < len(lines):
            block.append(lines[j])
            if j >= i and lines[j].rstrip().endswith(";"):
                break
            j += 1
        yield start + 1, block
        i = j + 1


def faults_in(block: list) -> list:
    text = "\n".join(block)
    out = []
    # One headers() call. Two makes auth depend on library merge rules.
    n_headers = len(re.findall(r"\.headers\(", text))
    if n_headers != 1:
        out.append("%d .headers(...) calls -- expected exactly 1, so the "
                   "merge is ours and not RestAssured's" % n_headers)
    # The idempotent bearer. The other one double-prefixes a ctx token.
    if "Authorization" in text and "bearerOnce(" not in text:
        out.append("Authorization is not built with AuthUtilities."
                   "bearerOnce -- the unconditional bearer() sends "
                   "`Bearer Bearer ...` for a ctx token that already "
                   "carries the scheme")
    # The host comes from config, never from the request.
    if "Config.serviceBase(" not in text and "Config.baseUrl()" not in text:
        out.append("no Config.serviceBase/baseUrl -- the call does not "
                   "route through configuration")
    m = LITERAL_HOST.search(text)
    if m and "Config.serviceBase(" not in text:
        out.append("a literal host is written into the request")
    # The resolved url arrives as the lambda's 4th argument; recomputing
    # it at the call site is a second answer to the same question.
    if "(body, q, h, url)" not in text:
        out.append("not an InlineExchange lambda (body, q, h, url) -- the "
                   "resolved url must come from RestStep, not be rebuilt")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    suites = classic_suites(args.root)
    if not suites:
        print("check_classic_shape: no --classic suites in this tree -- "
              "nothing to check")
        return 0

    problems, n = [], 0
    for path in test_files(args.root):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                lines = fh.read().split("\n")
        except OSError:
            continue
        rel = os.path.relpath(path, args.root).replace(os.sep, "/")
        for line_no, block in exchanges(lines):
            n += 1
            for f in faults_in(block):
                problems.append("%s:%d  %s" % (rel, line_no, f))

    print("check_classic_shape: %d inline exchange(s) across %d classic "
          "suite(s)" % (n, len(suites)))
    if not n:
        print("No inline exchange found -- nothing to check.")
        return 0
    if problems:
        print()
        for p in problems[:40]:
            print("  FAIL  " + p)
        if len(problems) > 40:
            print("  ... and %d more" % (len(problems) - 40))
        print("\n%d malformed exchange(s). Each of these COMPILES and "
              "fails at runtime -- wrong host, doubled auth scheme, or a "
              "header map the library may have dropped." % len(problems))
        return 1
    print("Every inline exchange routes through config, builds its "
          "Authorization with bearerOnce, and merges headers once.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
