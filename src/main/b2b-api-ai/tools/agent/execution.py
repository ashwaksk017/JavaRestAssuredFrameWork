"""The results of running a job's tests: read from TestNG's
testng-results.xml, judged for the push gate.

A run passes when at least one test passed and none failed or was skipped
-- except a data row switched off in its sheet (`execute=N`), which is the
author's choice, not a test that did not run.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

SWITCHED_OFF = "switched off in the data sheet"


def _message(method) -> str:
    msg = method.find("./exception/message")
    text = (msg.text if msg is not None else "") or ""
    if not text.strip():
        exc = method.find("./exception")
        text = exc.get("class", "") if exc is not None else ""
    return " ".join(text.split())[:2000]


def read(path: str) -> dict:
    """{passed, failed, skipped, switched_off, tests: [{class, method, status,
    message, ms}]}. Configuration methods (@Before/@After) are not tests,
    but one that FAILED is reported as a failure: the tests behind it did
    not run as written. Raises ValueError when there is no readable file."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        raise ValueError(f"no readable test results ({type(e).__name__})") from None
    out = {"passed": 0, "failed": 0, "skipped": 0, "switched_off": 0, "tests": []}
    for cls in root.iter("class"):
        cname = cls.get("name", "")
        for m in cls.findall("test-method"):
            status = (m.get("status") or "").upper()
            config = (m.get("is-config") or "").lower() == "true"
            if config and status != "FAIL":
                continue
            entry = {"class": cname, "method": m.get("name", ""), "status": status,
                     "message": _message(m) if status != "PASS" else "",
                     "ms": int(m.get("duration-ms") or 0), "config": config}
            if status == "PASS":
                out["passed"] += 1
            elif status == "SKIP" and SWITCHED_OFF in entry["message"]:
                out["switched_off"] += 1
            elif status == "SKIP":
                out["skipped"] += 1
            else:
                out["failed"] += 1
            out["tests"].append(entry)
    return out


def verdict(res: dict) -> tuple:
    """(ok, why). ok only when something passed and nothing failed or was
    skipped other than a switched-off row."""
    if res["failed"]:
        return False, f"{res['failed']} test(s) failed"
    if res["skipped"]:
        return False, (f"{res['skipped']} test(s) were skipped -- most often a configuration "
                       f"key the test reads is not set (its message names it)")
    if not res["passed"]:
        return False, "no test passed: nothing was verified"
    return True, f"{res['passed']} test(s) passed"
