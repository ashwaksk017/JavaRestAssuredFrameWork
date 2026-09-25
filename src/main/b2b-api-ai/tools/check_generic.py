"""Committed code must compile after converting ANY single ReadyAPI XML.

WHY THIS EXISTS
---------------
`SharedClients.java` -- a committed file -- carried four typed convenience
methods, each naming a client class that only exists after converting one
particular XML:

    public static ProgramAccountsClient programAccounts(String baseUrl)
    public static SmbToHwsConsumerClient smbToHwsConsumer(String baseUrl)
    ...

Convert a different XML and `mvn compile` failed on a file the user never
touched, pointing at SharedClients rather than at the real cause. All four
had ZERO call sites -- generated code always used the generic get().

That is the whole genericity contract: drop any XML in the input folder,
run the converter, and the tree compiles. This check enforces it without
needing a 20-minute convert to find out.

WHAT COUNTS AS A VIOLATION
--------------------------
A committed (non-generated) file naming a SUITE-SPECIFIC generated type:

    com.hi.api.rest.clients.<Anything>Client   -- one per converted XML
    com.hi.api.support.<suite>.*               -- per-suite TestSupport etc.

Suite-AGNOSTIC generated types are fine: the converter always emits them
regardless of which XML was converted.

    python tools/check_generic.py
"""
from __future__ import annotations

import os
import re
import sys

# Generated trees -- skipped entirely; they are allowed to be specific.
GENERATED_DIRS = {
    os.path.join("src", "main", "java", "com", "hi", "api", "support"),
    os.path.join("src", "main", "java", "com", "hi", "api", "rest", "clients"),
    os.path.join("src", "main", "java", "com", "hi", "api", "templates"),
    os.path.join("src", "test", "java", "com", "hi", "api", "tests", "imported"),
}

# Emitted for every conversion, so committed code may depend on them.
SUITE_AGNOSTIC = {
    "ImportedRestClient", "ImportedScenario", "ImportedTemplates",
    "ImportedTestdataCleanup", "CtxFields", "ScenarioSteps",
    "CustomerOnboarding", "Insights",
}

CLIENT_REF = re.compile(r"com\.hi\.api\.rest\.clients\.(\w+)")
SUITE_SUPPORT_REF = re.compile(r"com\.hi\.api\.support\.([a-z][\w]*)\.(\w+)")

# A mention inside a comment cannot break compilation.
LINE_COMMENT = re.compile(r"^\s*(//|\*|/\*)")


TEMPLATES_REF = re.compile(r"com\.hi\.api\.templates\.([a-z][\w]*)\.(\w+)")


def _is_generated(path: str) -> bool:
    norm = os.path.normpath(path)
    return any(norm.startswith(g) for g in GENERATED_DIRS)


def scan(root: str = "src"):
    findings = []
    for dp, dn, fns in os.walk(root):
        if _is_generated(dp):
            dn[:] = []
            continue
        for fn in fns:
            if not fn.endswith(".java"):
                continue
            path = os.path.join(dp, fn)
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if LINE_COMMENT.match(line):
                    continue
                for m in CLIENT_REF.finditer(line):
                    cls = m.group(1)
                    if cls in SUITE_AGNOSTIC:
                        continue
                    findings.append((path, i, f"generated client type '{cls}'",
                                     line.strip()[:90]))
                for m in SUITE_SUPPORT_REF.finditer(line):
                    pkg, cls = m.group(1), m.group(2)
                    if cls in SUITE_AGNOSTIC or pkg == "scenario":
                        continue
                    findings.append((path, i,
                                     f"per-suite support type '{pkg}.{cls}'",
                                     line.strip()[:90]))
                # `com.hi.api.templates.<suite>.Templates` is per-suite too.
                # It was invisible here: the two regexes above cover clients
                # and support only, so a committed file importing a suite
                # Templates class passed this gate and then failed `mvn
                # compile` for anyone converting a different XML -- exactly
                # the breakage this check exists to prevent. Hand-written
                # code must go through ImportedTemplates.get(...) instead.
                for m in TEMPLATES_REF.finditer(line):
                    pkg, cls = m.group(1), m.group(2)
                    if cls in SUITE_AGNOSTIC:
                        continue
                    findings.append((path, i,
                                     f"per-suite templates type '{pkg}.{cls}'",
                                     line.strip()[:90]))
    return findings


def main() -> int:
    findings = scan()
    if not findings:
        print("OK -- no committed file names a suite-specific generated type.")
        print("     Any single ReadyAPI XML can be converted and compiled.")
        return 0
    print(f"{len(findings)} genericity violation(s):\n")
    for path, ln, what, src in findings:
        print(f"  {path}:{ln}")
        print(f"      {what}")
        print(f"      {src}")
    print()
    print("Committed code must not name a type that only exists after")
    print("converting one particular XML. Use SharedClients.get(name, baseUrl,")
    print("Ctor::new) or resolve the class reflectively and skip when absent.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
