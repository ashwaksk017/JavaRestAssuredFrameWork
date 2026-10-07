"""Every service key a converted app routes through, and who answers for it.

WHY THIS EXISTS
---------------
`Config.serviceBase(key, recordedBase, callerBase)` prefers a configured
`services.<key>`; with none it falls back to the host the converter
RECORDED. That fallback is the trap, because the source XML carries two
kinds of host and NEITHER is reliably the live target:

    <con:endpoint>     the project's configured target when exported
    <con:originalUri>  where the request was first imported from

The converter prefers `originalUri` when present. Which one is right
depends on how that project was last pointed, and the two applications
in this repo are INVERTED on their token step:

    GOAL  endpoint = the real gateway, originalUri = a stale localhost
    B2B   endpoint = a local mock on :9006, originalUri = the real gateway

So a recorded host is a GUESS, not a reading of intent, and reading
either element as authoritative gets one application wrong. Keys left
unset in this project pointed at a test-tier gateway (every call 401'd
against a token minted elsewhere), a `localhost` with its port stripped
(nine suites could not mint a token at all), and a developer's machine.

What this check reports is therefore "nobody decided this host", not
"this host is wrong" -- the decision belongs to whoever owns the
environment.

None of that is visible in the emitted Java, which looks correct, nor at
compile time, nor to any other check. It surfaces as a run that fails
for reasons nobody connects to configuration.

    python tools/audit_service_keys.py [--root .] [--config PATH] [--input DIR]

With --input it cross-checks every recorded host against the endpoints
the source XMLs declare, and reports the ones that are originalUri-only.
Exits 1 when a key is unset AND its recorded fallback is unattested.

It prints key names, suite names and counts -- never a host value from
the config -- so its output is safe to paste into a public issue.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

# The recorded base may legitimately be EMPTY: the converter emits no
# host when the only one it recorded was loopback, so the call follows
# callerBase. `[^"]*` keeps those keys visible here instead of dropping
# them from the audit entirely.
SB = re.compile(r'Config\.serviceBase\(\s*"([^"]+)"\s*,\s*"([^"]*)"')
FILL = re.compile(r'ApiRoutes\.fill\("([^"]+)"|\.(?:get|post|put|patch|delete)\("([^"]+)"')
TOKEN_PATH = re.compile(r"realms/applications/token|/oauth2?/token\b")
ENDPOINT = re.compile(r"<con:endpoint>\s*(https?://[^<\s]+)\s*</con:endpoint>")


def host_of(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else ""


def is_loopback(host):
    """A recorded host that means "the machine making the call".

    Checked separately from "is this key configured", because a
    loopback fallback is a defect even when the key IS set. The key
    minted from such a host is the HOSTNAME -- `services.localhost` --
    so every unversioned local recording in the project lands on one
    config value. In this repo that was four unrelated services (token,
    rateplan, groups, shop) across 18 suites behind a single knob,
    which cannot be correct for more than one of them at a time. The
    key was set, so the unset/unattested check above stayed silent
    while 101 calls died on `Connection refused` -- 49% of the failures
    and 49% of the skips in one full run.
    """
    h = (host or "").split(":")[0].lower()
    return (h in ("localhost", "::1", "0.0.0.0", "host.docker.internal")
            or h.startswith("127."))


def java_sources(root):
    for sub in ("src/main/java", "src/test/java"):
        base = os.path.join(root, sub)
        for dirpath, _dirs, files in os.walk(base):
            for fn in sorted(files):
                if fn.endswith(".java"):
                    yield os.path.join(dirpath, fn)


def suite_of(path):
    """Suite name, from an emitted test path or a generated client."""
    norm = path.replace("\\", "/")
    for marker in ("/tests/imported/", "/tests/classic/"):
        if marker in norm:
            return norm.split(marker, 1)[1].split("/")[0]
    base = os.path.basename(path)
    if base.endswith("Client.java"):
        return base[: -len("Client.java")].lower()
    return ""


def scan(root):
    """key -> Counter(recorded host), and suite -> the key its token uses."""
    keys = defaultdict(Counter)
    token_key = {}
    for path in java_sources(root):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        if "Config.serviceBase(" not in text:
            continue
        suite = suite_of(path)
        lines = text.split("\n")
        for i, line in enumerate(lines):
            m = SB.search(line)
            if not m:
                continue
            keys[m.group(1)][host_of(m.group(2))] += 1
            # Walk back to the path this call uses, to spot the token step.
            for j in range(i, max(-1, i - 30), -1):
                fm = FILL.search(lines[j])
                if not fm:
                    continue
                pth = fm.group(1) or fm.group(2) or ""
                if pth and TOKEN_PATH.search(pth) and suite:
                    token_key.setdefault(suite, m.group(1))
                break
    return keys, token_key


def attested_hosts(input_dir):
    """Hosts the source XMLs declare as a real <con:endpoint>."""
    if not input_dir or not os.path.isdir(input_dir):
        return None
    seen = set()
    for path in glob.glob(os.path.join(input_dir, "*.xml")):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        for url in ENDPOINT.findall(text):
            seen.add(host_of(url))
    return seen


def config_blocks(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: (v.get("services") or {})
            for k, v in raw.items() if isinstance(v, dict)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--config", default="",
                    help="program_configuration.json; host values are never printed")
    ap.add_argument("--input", default="",
                    help="source XML directory, to tell an endpoint from an originalUri")
    args = ap.parse_args()

    keys, token_key = scan(args.root)
    if not keys:
        print("audit_service_keys: no Config.serviceBase call found -- "
              "nothing to audit (is this a converted tree?)")
        return 0

    blocks = config_blocks(args.config)
    attested = attested_hosts(args.input)

    print("audit_service_keys: %d service key(s) across the emitted tree"
          % len(keys))
    if blocks:
        print("  config blocks: " + ", ".join(sorted(blocks)))
    else:
        print("  (no --config given: cannot say which keys are answered for)")
    if attested is None:
        print("  (no --input given: cannot tell a real endpoint from an "
              "originalUri artifact)")
    print()

    problems = []
    width = max(len(k) for k in keys)
    for key in sorted(keys):
        set_in = [b for b, svc in blocks.items() if (svc.get(key) or "").strip()]
        unset_in = [b for b in blocks if b not in set_in]
        recorded = keys[key]
        unattested = [h for h in recorded
                      if attested is not None and h and h not in attested]
        state = ("set in " + ",".join(sorted(set_in))) if set_in else "UNSET everywhere"
        print("  %-*s  %-26s  %d call(s), %d recorded host(s)"
              % (width, key, state, sum(recorded.values()), len(recorded)))
        if unattested:
            print("  %-*s    %d recorded host(s) NOT declared as <con:endpoint>"
                  % (width, "", len(unattested)))
        if unset_in and unattested:
            problems.append((key, sorted(unset_in), len(unattested)))

    print()
    if token_key:
        by_key = defaultdict(list)
        for suite, key in token_key.items():
            by_key[key].append(suite)
        print("  the token exchange routes through:")
        for key in sorted(by_key):
            suites = sorted(by_key[key])
            flag = ""
            if blocks and not any((svc.get(key) or "").strip()
                                  for svc in blocks.values()):
                flag = "   <-- UNSET: the token falls back to a recorded host"
            print("    %-*s  %d suite(s): %s%s"
                  % (width, key, len(suites),
                     ", ".join(suites[:5]) + (" ..." if len(suites) > 5 else ""),
                     flag))
        print()

    # A loopback fallback fails on its own terms, whether or not the key
    # is configured -- see is_loopback().
    loopback = {k: {h: n for h, n in hosts.items() if is_loopback(h)}
                for k, hosts in keys.items()}
    loopback = {k: v for k, v in loopback.items() if v}
    if loopback:
        total = sum(sum(v.values()) for v in loopback.values())
        print("  LOOPBACK FALLBACKS (%d call site(s) across %d key(s)):"
              % (total, len(loopback)))
        for key in sorted(loopback):
            hosts = ", ".join("%s x%d" % (h, n)
                              for h, n in sorted(loopback[key].items()))
            print("    FAIL  services.%-24s falls back to %s" % (key, hosts))
        print("          A loopback host is never the machine under test, so "
              "no environment value makes these right. Re-run the converter: "
              "it no longer emits one, and takes the real host from "
              "<con:endpoint> where the XML has it.")
        print()

    if problems or loopback:
        for key, unset_in, n in problems:
            print("  FAIL  %s is unset in %s, and its recorded fallback names "
                  "%d host(s) no source XML declares as an endpoint"
                  % (key, "/".join(unset_in), n))
        if problems:
            print("\n%d key(s) would route live traffic at a host NOBODY "
                  "DECIDED: not configured, and not declared as an endpoint "
                  "either. That is a question for whoever owns the "
                  "environment, not a verdict that the host is wrong -- set "
                  "each one in program_configuration.json, per environment "
                  "block." % len(problems))
        return 1
    print("Every service key is either configured or falls back to a host "
          "the source XML actually declares, and none falls back to loopback.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
