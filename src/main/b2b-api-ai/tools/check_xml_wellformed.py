"""Every committed XML must PARSE.

Exists because a malformed suite file survived a full 15/15 verify_all run:
nothing in the chain ever parsed the TestNG suites, so `testng-manual.xml`
was pushed with `--` inside an XML comment (illegal per the spec) and failed
only when somebody actually tried to run it. `testng-tags.xml` had been
unloadable for far longer, unnoticed for the same reason.

Parsing is not running: these suites make real HTTP calls and must never be
executed here, but checking that they are well-formed costs nothing.
"""
import os
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"target", ".git", "node_modules", ".idea"}

def main() -> int:
    bad, ok = [], 0
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.endswith(".xml"):
                continue
            p = os.path.join(root, f)
            try:
                ET.parse(p)
                ok += 1
            except Exception as e:
                bad.append((os.path.relpath(p, ROOT).replace("\\", "/"), str(e)))
    for rel, err in bad:
        print("FAIL %s: %s" % (rel, err))
    print("xml-wellformed: %d parsed, %d broken" % (ok, len(bad)))
    if bad:
        print("\nMost common cause: `--` inside an <!-- comment -->, which XML "
              "forbids. Use a single hyphen.")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
