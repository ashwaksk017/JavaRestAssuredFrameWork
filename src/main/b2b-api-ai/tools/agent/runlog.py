"""target/b2b-full-run.log — when it updates, and what to check when it did not.

Log4j writes this file only inside a Surefire-started TestNG JVM
(src/test/resources/log4j2.xml). Maven output on the console is separate;
BUILD SUCCESS with Tests run: 0 does not refresh the file.
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

RUN_LOG_REL = os.path.join("target", "b2b-full-run.log")
RESULTS_REL = os.path.join("target", "surefire-reports", "testng-results.xml")
_TESTNG_TOTAL_RX = re.compile(r'<testng-results[^>]*\btotal="(\d+)"', re.I)


def _load_diag():
    import importlib.util
    name = "runlog_diag"
    if name in __import__("sys").modules:
        return __import__("sys").modules[name]
    path = os.path.join(os.path.dirname(HERE), "diag.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    __import__("sys").modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stat(root: str, rel: str) -> tuple:
    path = os.path.join(root, rel)
    try:
        st = os.stat(path)
        return path, st.st_mtime, st.st_size
    except OSError:
        return path, None, 0


def _format_mtime(ts: float | None) -> str:
    if ts is None:
        return "not on disk"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def testng_total(root: str) -> int | None:
    path = os.path.join(root, RESULTS_REL)
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(12_000)
    except OSError:
        return None
    m = _TESTNG_TOTAL_RX.search(head)
    return int(m.group(1)) if m else None


def log_level_flag(settings: dict | None) -> str:
    """Maven -Dlog.level=… for framework loggers in log4j2.xml."""
    level = str((settings or {}).get("run_log_level") or "debug").strip().lower()
    if level not in ("info", "debug", "trace", "warn", "error"):
        level = "debug"
    return f"-Dlog.level={level}"


def inspect(root: str, lines: int = 400) -> dict:
    """Hints for the page, the tail of the log, and timestamps for tests."""
    log_path, log_m, log_sz = _stat(root, RUN_LOG_REL)
    _, res_m, _ = _stat(root, RESULTS_REL)
    total = testng_total(root)
    hints = [
        f"{RUN_LOG_REL}  last modified: {_format_mtime(log_m)}  ({log_sz} bytes)"
        f"  — Log4j writes this only after Surefire starts TestNG.",
    ]
    if res_m is not None:
        extra = f"  tests in results: {total}" if total is not None else ""
        hints.append(f"{RESULTS_REL}  last modified: {_format_mtime(res_m)}{extra}")
    else:
        hints.append(f"{RESULTS_REL}  not on disk — the last mvn test probably did not reach Surefire.")

    if log_m is None:
        hints.append(
            "WHY IT DID NOT UPDATE: Log4j creates this file only when Surefire starts TestNG. "
            "A compile failure, running mvn outside the b2b-api-ai folder, -DskipTests, or "
            "BUILD SUCCESS with Tests run: 0 leaves it missing or unchanged."
        )
    elif res_m is not None and res_m > log_m + 2:
        hints.append(
            "WHY IT LOOKS STALE: Surefire results are newer than the run log. Tests ran but "
            "the file appender did not refresh — check src/test/resources/log4j2.xml."
        )
    elif res_m is None or log_m > res_m + 2:
        hints.append(
            "WHY IT DID NOT UPDATE: the run log is newer than Surefire results, or results are "
            "missing. The last mvn command likely stopped before the test phase (compile error, "
            "offline -o could not resolve a plugin, or the wrong working directory)."
        )

    if total == 0:
        hints.append(
            "Surefire reported zero tests. testng-jira.xml runs only @Test(groups = \"jira\"). "
            "Tests run: 0 and BUILD SUCCESS still leave this log unchanged."
        )

    hints.append(
        "More framework detail: add -Dlog.level=debug to mvn test (see log4j2.xml). "
        "To keep UTF-8 in a saved Maven log: python tools/mvn_utf8.py --log FILE test ..."
    )
    log_has_xray_diag = False
    if log_m is not None:
        try:
            with io.open(log_path, encoding="utf-8", errors="replace") as fh:
                if "--- workbench Xray diagnostics" in fh.read(900_000):
                    log_has_xray_diag = True
        except OSError:
            pass
    if log_has_xray_diag:
        hints.append(
            "Run the tests appended an Xray trace and Maven [XrayClient] lines to the end of this file "
            "(search for --- workbench Xray diagnostics)."
        )
    else:
        trace_path = os.path.join(root, "target", "xray-maven.trace")
        if os.path.isfile(trace_path):
            hints.append(
                "Maven [XrayClient] lines from the last workbench run are in target/xray-maven.trace. "
                "Use Run the tests again to append the Xray trace to this file."
            )

    tail = ""
    if log_m is not None:
        tail = _load_diag().tail(log_path, lines)
    return {"hints": hints, "tail": tail, "log_mtime": log_m, "results_mtime": res_m, "tests_total": total}


def text_for_page(root: str, lines: int = 400) -> str:
    info = inspect(root, lines)
    body = "\n".join(info["hints"])
    if info["tail"]:
        body += "\n---\n" + info["tail"]
    if "xraytrace_runlog" not in __import__("sys").modules:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "xraytrace_runlog", os.path.join(HERE, "xraytrace.py"))
        mod = importlib.util.module_from_spec(spec)
        __import__("sys").modules["xraytrace_runlog"] = mod
        spec.loader.exec_module(mod)
    body += __import__("sys").modules["xraytrace_runlog"].text_section(root)
    return body


def after_maven(root: str, started_before: float) -> dict:
    """After cmd_run's mvn: warn when the run log was not refreshed."""
    _, log_m, _ = _stat(root, RUN_LOG_REL)
    if log_m is None:
        return {"warn": "target/b2b-full-run.log was not created — Surefire may not have started TestNG."}
    if log_m < started_before - 1.0:
        return {
            "warn": "target/b2b-full-run.log was not refreshed by this run (timestamp unchanged). "
                    "Open the Logs tab → Maven test run log for why.",
        }
    return {}
