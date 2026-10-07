"""Does each converted suite actually build, send, read and reuse a token?

WHY THIS EXISTS
---------------
`audit_service_keys.py` answers WHERE a token request goes. It does not
answer whether the rest of the chain survived conversion, and the chain
has five links that fail independently and silently:

    body ---> POST ---> extract access_token ---> publish to ctx ---> reused as Authorization

A suite can route perfectly and still never authenticate because the
extract never runs, because the token is never published to ctx, because
nothing reads it back, or because the published value lost its `Bearer `
prefix. None of that fails the convert, fails to compile, or shows up as
anything but `401` at runtime -- the same `401` a wrong host produces,
which is why the two audits are separate.

WHAT IT CANNOT TELL YOU: whether the extracted FIELD NAME is the right
one. Auth servers differ (`access_token`, `id_token`, ...), so the field
is REPORTED for a human to check, not judged. An extract against the
token response that names a field the server does not return still reads
as present here and still 401s at runtime.

The scheme prefix matters more than it looks: this converter publishes
the token WITH it (`"Bearer " + token`), so consumers must use
`AuthUtilities.bearerOnce`. A suite that publishes a bare token and a
consumer that prefixes unconditionally are each fine alone and send
`Bearer Bearer ...` together.

    python tools/audit_token_chain.py [--root .] [--config PATH]

Exits 1 when a suite has a token step whose chain is broken. A suite
with NO token step is reported, not failed: some suites legitimately
call an unauthenticated service.

Prints suite names, ctx key names and counts -- never a token, a
credential or a host -- so its output is safe to paste into a ticket.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict

TOKEN_STEP = re.compile(r'\.name\("([^"]*[Tt]oken[^"]*)"\)')
EXTRACT = re.compile(r'safeJsonExtract\(\s*(\w+)\s*,\s*"([^"]+)"')
# Match only the CALL; the statement is read to its `;` separately. The
# value can be a ternary over 100 characters long, and a fixed-width
# capture here reported every correct suite as publishing a bare token.
PUBLISH = re.compile(r'putExtracted\(\s*ctx\s*,\s*"([^"]+)"\s*,')
SB = re.compile(r'Config\.serviceBase\(\s*"([^"]+)"')
BEARER_LITERAL = re.compile(r'"Bearer "\s*\+')


PLACEHOLDER = re.compile(r"#([A-Za-z0-9_.]+)#")


def token_body_gaps(root, suite):
    """Placeholders in the suite's token-request template that no CSV fills.

    The body is substituted against `mergedRow(row, ctx)` -- the CSV row
    plus ctx -- and nothing else. There is no Config fallback on that
    path, so a placeholder with no column goes out on the wire as the
    literal `#client_id#`.

    Returns (template_name, [unfilled placeholders]) or (None, []).
    """
    tpl_dir = os.path.join(root, "src", "main", "resources", "templates", suite)
    tpl = None
    for dirpath, _dirs, files in os.walk(tpl_dir):
        for fn in files:
            if "token" in fn.lower():
                tpl = os.path.join(dirpath, fn)
                break
        if tpl:
            break
    if not tpl:
        return None, []
    names = sorted(set(PLACEHOLDER.findall(read(tpl))))
    if not names:
        return os.path.basename(tpl), []
    csv_dir = os.path.join(root, "src", "test", "resources", "csv", suite)
    headers = set()
    for dirpath, _dirs, files in os.walk(csv_dir):
        for fn in files:
            if not fn.endswith(".csv"):
                continue
            head = read(os.path.join(dirpath, fn)).split("\n", 1)[0]
            for col in head.split(","):
                headers.add(col.strip().strip('"').lower())
    return os.path.basename(tpl), [n for n in names if n.lower() not in headers]


def statement_at(text, start):
    """The putExtracted statement from `start` to its terminating `;`.

    Bounded, so an unterminated statement cannot swallow the file."""
    end = text.find(";", start)
    if end < 0 or end - start > 600:
        end = min(len(text), start + 600)
    return text[start:end]


def support_files(root):
    base = os.path.join(root, "src", "main", "java")
    for dirpath, _dirs, files in os.walk(base):
        for fn in sorted(files):
            if fn.endswith(".java"):
                yield os.path.join(dirpath, fn)


def suite_of(path):
    norm = path.replace("\\", "/")
    for marker in ("/support/", "/tests/imported/", "/tests/classic/"):
        if marker in norm:
            tail = norm.split(marker, 1)[1]
            seg = tail.split("/")[0]
            if seg.endswith(".java"):
                return ""
            return seg
    base = os.path.basename(path)
    if base.endswith("Client.java"):
        # Lowercasing alone is not the suite name: a suite directory can
        # carry an underscore the client class drops
        # (topicsprogramaccountsstgkafka_events ->
        # TopicsprogramaccountsstgkafkaEventsClient). Taking the
        # lowercased class invented a second, empty suite and reported it
        # as having no token step. Resolved against the real directories
        # by the caller; the squashed form is returned as a fallback.
        return base[: -len("Client.java")].lower()
    return ""


def resolve_client_suite(name, known):
    """Map a client-derived name onto the real suite directory."""
    if name in known:
        return name
    squashed = {k.replace("_", ""): k for k in known}
    return squashed.get(name.replace("_", ""), name)


def read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--config", default="")
    args = ap.parse_args()

    # Whole-suite text, because the chain spans several files: the step
    # and extract sit in SetupHelper, the consumers in cases/Specs*.
    text_by_suite = defaultdict(str)
    route_key_raw = {}

    for path in support_files(args.root):
        text = read(path)
        if not text:
            continue
        suite = suite_of(path)
        if not suite:
            continue
        if os.path.basename(path).endswith("Client.java"):
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if "realms/applications/token" in line or "/oauth2/token" in line:
                    for j in range(i, min(len(lines), i + 25)):
                        m = SB.search(lines[j])
                        if m:
                            route_key_raw.setdefault(suite, m.group(1))
                            break
                    break
            continue
        text_by_suite[suite] += "\n" + text

    # Clients are named after a suite but not spelled like its directory,
    # so fold them onto the directories that actually hold the code.
    route_key = {resolve_client_suite(s, set(text_by_suite)): k
                 for s, k in route_key_raw.items()}

    has_step = defaultdict(bool)
    extracts = defaultdict(set)
    publishes = defaultdict(set)
    scheme_ok = defaultdict(bool)
    consumes = defaultdict(set)

    for suite, text in text_by_suite.items():
        has_step[suite] = bool(TOKEN_STEP.search(text))
        for m in EXTRACT.finditer(text):
            if "token" in m.group(1).lower() or "token" in m.group(2).lower():
                extracts[suite].add(m.group(2))
        n_publish = defaultdict(int)
        for m in PUBLISH.finditer(text):
            key = m.group(1)
            if "token" not in key.lower():
                continue
            publishes[suite].add(key)
            n_publish[key] += 1
            if BEARER_LITERAL.search(statement_at(text, m.start())):
                scheme_ok[suite] = True
        # A consumer is any OTHER mention of the published key. Consumers
        # do not share one accessor -- `Ref.ctx("...")` in a spec and
        # `ctxGet(ctx, "...")` in a helper are both reads -- so matching
        # one spelling reported every correct suite as never reusing its
        # token.
        for key in publishes[suite]:
            total = text.count('"%s"' % key)
            if total - n_publish[key] > 0:
                consumes[suite].add(key)

    # `scenario` is the shared support package, not a converted suite.
    SHARED = {"scenario"}
    suites = sorted((set(has_step) | set(publishes) | set(route_key)
                     | set(extracts) | set(consumes)) - SHARED)
    if not suites:
        print("audit_token_chain: no converted suite found -- nothing to audit")
        return 0

    services = {}
    if args.config and os.path.isfile(args.config):
        try:
            with open(args.config, encoding="utf-8") as fh:
                raw = json.load(fh)
            for blk, body in raw.items():
                if isinstance(body, dict):
                    services[blk] = body.get("services") or {}
        except (OSError, ValueError):
            services = {}

    print("audit_token_chain: %d suite(s)\n" % len(suites))
    width = max(len(s) for s in suites)
    print("  %-*s  %-5s %-16s %-8s %-7s %-7s  %s"
          % (width, "suite", "step", "extracts field", "publish", "scheme",
             "reused", "routes via"))
    print("  " + "-" * (width + 68))

    broken = []
    for s in suites:
        step = "yes" if has_step[s] else "-"
        # The field name, not a verdict: see WHAT IT CANNOT TELL YOU.
        ext = (",".join(sorted(extracts[s]))[:16] if extracts[s]
               else ("MISSING" if has_step[s] else "-"))
        pub = "yes" if publishes[s] else ("MISSING" if has_step[s] else "-")
        sch = "Bearer" if scheme_ok[s] else ("BARE" if publishes[s] else "-")
        use = str(len(consumes[s])) if consumes[s] else ("NONE" if publishes[s] else "-")
        key = route_key.get(s, "-")
        unset = ""
        if key != "-" and services and not any(
                (svc.get(key) or "").strip() for svc in services.values()):
            unset = "  <-- key UNSET in every config block"
        print("  %-*s  %-5s %-16s %-8s %-7s %-7s  %s%s"
              % (width, s, step, ext, pub, sch, use, key, unset))

        if has_step[s]:
            tpl, unfilled = token_body_gaps(args.root, s)
            if unfilled:
                broken.append(
                    (s, "sends a token body (%s) whose placeholder(s) %s have "
                        "no CSV column -- the body is substituted against the "
                        "row plus ctx and nothing else, so these go out as "
                        "literal #name# text"
                     % (tpl, ", ".join(unfilled))))
            if not extracts[s]:
                broken.append((s, "sends a token request but never extracts a "
                                  "token from the response"))
            if not publishes[s]:
                broken.append((s, "extracts a token but never publishes it to "
                                  "ctx, so no later step can send it"))
            elif not scheme_ok[s]:
                broken.append((s, "publishes a token WITHOUT the `Bearer ` "
                                  "prefix this converter's consumers expect "
                                  "(bearerOnce will not add one)"))
            if publishes[s] and not consumes[s]:
                broken.append((s, "publishes a token that no step reads back"))

    no_step = [s for s in suites if not has_step[s]]
    print()
    if no_step:
        print("  %d suite(s) have no token step at all (fine if the service "
              "is unauthenticated): %s" % (len(no_step), ", ".join(no_step)))
    if broken:
        print()
        for s, why in broken:
            print("  FAIL  %s %s" % (s, why))
        print("\n%d broken token chain(s). Each COMPILES and fails at runtime "
              "as a 401, which is also what a wrong host looks like -- run "
              "audit_service_keys.py too before blaming credentials."
              % len(broken))
        return 1
    print("\nEvery suite with a token step extracts it, publishes it with the "
          "scheme, and reads it back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
