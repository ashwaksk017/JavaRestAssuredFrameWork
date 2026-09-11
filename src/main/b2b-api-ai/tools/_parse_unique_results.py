"""Unique TestNG outcomes: last-write-wins on (class, method, test_case_id). Skip retries/config."""
from collections import defaultdict
import re
import xml.etree.ElementTree as ET

root = ET.parse("target/surefire-reports/testng-results.xml").getroot()
suite = root.find("suite")
print(
    "header passed=%s failed=%s skipped=%s retried=%s total=%s suite=%s..%s"
    % (
        root.get("passed"),
        root.get("failed"),
        root.get("skipped"),
        root.get("retried"),
        root.get("total"),
        suite.get("started-at") if suite is not None else "",
        suite.get("finished-at") if suite is not None else "",
    )
)

latest = {}
for tm in root.iter("test-method"):
    if tm.get("is-config") == "true":
        continue
    status = (tm.get("status") or "").upper()
    if status in {"SKIP", "SKIPPED"} or tm.get("retried") == "true":
        continue
    sig = tm.get("signature") or ""
    m = re.search(r"instance:([^\]]+)", sig)
    cls = m.group(1).split("@")[0] if m else ""
    method = tm.get("name") or ""
    case_id = ""
    params = tm.find("params")
    if params is not None:
        mm = re.search(r"test_case_id=([^,\s}]+)", "".join(params.itertext()))
        if mm:
            case_id = mm.group(1)
    msg = ""
    exc = tm.find("exception")
    if exc is not None:
        message = exc.find("message")
        if message is not None and (message.text or "").strip():
            msg = message.text.strip()[:1200]
    latest[(cls, method, case_id)] = (status, msg)

passed = failed = 0
by_class = defaultdict(lambda: [0, 0])
print("passes:")
for (cls, method, case_id), (status, msg) in sorted(latest.items()):
    short = cls.rsplit(".", 1)[-1] if cls else "?"
    if status in {"PASS", "PASSED"}:
        passed += 1
        by_class[short][0] += 1
        print("  PASS %s#%s %s" % (short, method, case_id))
print("fails:")
for (cls, method, case_id), (status, msg) in sorted(latest.items()):
    short = cls.rsplit(".", 1)[-1] if cls else "?"
    if status in {"FAIL", "FAILED"}:
        failed += 1
        by_class[short][1] += 1
        print("  FAIL %s#%s %s" % (short, method, case_id))
        print((msg or "").strip()[:800])
        print()
print("unique PASS=%d FAIL=%d" % (passed, failed))
print("by class:")
for name, (p, f) in sorted(by_class.items()):
    print("  %s pass=%d fail=%d" % (name, p, f))
