from pathlib import Path
import re
import xml.etree.ElementTree as ET

xml = Path("target/surefire-reports/testng-results.xml")
root = ET.parse(xml).getroot()
print("suite attrs", root.find("suite").attrib if root.find("suite") is not None else "")

# unique test methods by status (ignore config)
passed, failed = set(), set()
fail_msg = {}
for tm in root.iter("test-method"):
    if tm.get("is-config") == "true":
        continue
    name = tm.get("name")
    status = tm.get("status")
    if status == "PASS":
        passed.add(name)
    elif status == "FAIL":
        failed.add(name)
        ex = tm.find("exception")
        if ex is not None and name not in fail_msg:
            msg = (ex.findtext("message") or "").replace("\n", " | ")[:220]
            fail_msg[name] = msg or ex.get("class", "")

print("unique PASS", len(passed), sorted(passed))
print("unique FAIL", len(failed))
for n in sorted(failed):
    print("-", n)
    print(" ", fail_msg.get(n, "")[:200])
    print()
