"""Why a TestNG run did or did not update Xray — for the Logs tab and Run the tests.

Reads settings and test results only; never prints credential values. Scans
target/b2b-full-run.log and optional Maven stdout for [XrayClient] lines
(those go to System.out during mvn; Run the tests also appends them and an
Xray trace block to target/b2b-full-run.log).
"""
from __future__ import annotations

import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

JIRA_SUITE_REL = os.path.join("src", "test", "resources", "testng-jira.xml")
RESULTS_REL = os.path.join("target", "surefire-reports", "testng-results.xml")
RUN_LOG_REL = os.path.join("target", "b2b-full-run.log")
MAVEN_TRACE_REL = os.path.join("target", "xray-maven.trace")
_RUN_LOG_MARKER = "--- workbench Xray diagnostics (Run the tests) ---"

_KEY_RX = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
_ROW_KEY_RX = re.compile(r"jira_xray_id=([^,}\s]+)")
_XRAY_CLIENT_RX = re.compile(r"\[XrayClient\]\s*(.*)")
_LISTENER = "com.hi.api.reporting.XrayReportListener"


def _load(name: str, path: str):
    import importlib.util
    import sys
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def looks_like_issue_key(value: str) -> bool:
    return bool(_KEY_RX.match((value or "").strip()))


def _xray_block(root: str) -> tuple:
    """(env, xray dict from program_configuration, problem)."""
    st = _load("xraytrace_settings", os.path.join(HERE, "settings.py"))
    env, _src = st._env(root or "")
    try:
        doc = st._read(root or "")
    except (OSError, ValueError) as e:
        return env, {}, str(e)
    block = doc.get(env) if isinstance(doc.get(env), dict) else {}
    xray = block.get("xray") if isinstance(block.get("xray"), dict) else {}
    return env, xray, ""


def _flavour(xray: dict) -> str:
    explicit = str(xray.get("flavour") or "").strip().lower()
    if explicit in ("server", "dc", "datacenter"):
        return "SERVER"
    if explicit == "cloud":
        return "CLOUD"
    base = str(xray.get("baseUrl") or "").lower()
    if "getxray.app" in base:
        return "CLOUD"
    if str(xray.get("token") or "").strip():
        return "SERVER"
    cid = str(xray.get("clientId") or "").strip()
    sec = str(xray.get("clientSecret") or "").strip()
    if cid and sec:
        return "CLOUD"
    if str(xray.get("baseUrl") or "").strip():
        return "SERVER"
    return "CLOUD"


def _would_publish(xray: dict) -> tuple:
    """(would_run, reason_or_empty). Mirrors XrayClient.isEnabled / disabledReason."""
    enabled = str(xray.get("enabled") or "").lower() == "true"
    if not enabled:
        return False, "xray.enabled is off (Settings tab, or stg.xray.enabled in program_configuration.json)"
    flavour = _flavour(xray)
    if flavour == "SERVER":
        if not str(xray.get("token") or "").strip():
            return False, ("flavour=SERVER but xray.token is empty — Server / Data Center needs a "
                           "Jira personal access token in xray.token (Settings tab)")
        return True, ""
    cid = str(xray.get("clientId") or "").strip()
    sec = str(xray.get("clientSecret") or "").strip()
    if not (cid and sec):
        return False, ("flavour=CLOUD but xray.clientId / xray.clientSecret is empty — Cloud needs "
                       "an API key pair, or point xray.baseUrl at self-hosted Jira for SERVER")
    return True, ""


def _read_text(root: str, rel: str, limit: int = 400_000) -> str:
    path = os.path.join(root, rel)
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _suite_has_listener(root: str, suite_rel: str) -> bool:
    return _LISTENER in _read_text(root, suite_rel, 200_000)


def _xray_client_lines(*texts: str) -> list:
    out = []
    for text in texts:
        if not text:
            continue
        for line in text.splitlines():
            m = _XRAY_CLIENT_RX.search(line)
            if m:
                out.append(m.group(1).strip())
                continue
            # Log4j: ... INFO com.hi.api.xray.XrayClient - [XrayClient] publishing ...
            if "[XrayClient]" in line:
                out.append(line.split("[XrayClient]", 1)[1].strip().lstrip("- ").strip())
    return out


def _append_saved_trace(client: list, saved: str) -> list:
    """Lines in target/xray-maven.trace are already stripped of [XrayClient]."""
    out = list(client)
    seen = set(out)
    for line in (saved or "").splitlines():
        s = line.strip()
        if s and s not in seen:
            out.append(s)
            seen.add(s)
    return out


def save_maven_trace(root: str, maven_output: str) -> None:
    """Keep [XrayClient] lines from Maven stdout — they use System.out, not log4j."""
    lines = _xray_client_lines(maven_output or "")
    path = os.path.join(root or ROOT, MAVEN_TRACE_REL)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
            if lines:
                fh.write("\n")
    except OSError:
        pass


def append_diagnostics_to_run_log(root: str, suite_rel: str = "", maven_output: str = "") -> None:
    """Append Maven [XrayClient] lines and the Xray trace to target/b2b-full-run.log."""
    root = root or ROOT
    suite_rel = suite_rel or JIRA_SUITE_REL
    path = os.path.join(root, RUN_LOG_REL)
    client = _xray_client_lines(maven_output or "")
    block = ["", _RUN_LOG_MARKER]
    for line in client:
        block.append(f"[XrayClient] {line}")
    block.append(text_section(root, suite_rel, maven_output or ""))
    body = "\n".join(block)
    if not body.endswith("\n"):
        body += "\n"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "a", encoding="utf-8") as fh:
            fh.write(body)
    except OSError:
        pass


def run_log_has_workbench_diagnostics(root: str) -> bool:
    return _RUN_LOG_MARKER in _read_text(root, RUN_LOG_REL, 800_000)


def _short_class(fqcn: str) -> str:
    return (fqcn or "").rsplit(".", 1)[-1]


def _csv_keys_by_method(root: str) -> dict:
    """{(ClassSimple, method): [{key, issue, csv}, ...]} from data sheets."""
    import csv
    out: dict = {}
    base = os.path.join(root, "src", "test", "resources", "csv")
    if not os.path.isdir(base):
        return out
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            if not name.lower().endswith(".csv"):
                continue
            full = os.path.join(dirpath, name)
            cls = os.path.basename(dirpath)
            method = os.path.splitext(name)[0]
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                with io.open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read(500_000)
            except OSError:
                continue
            head = text.split("\n", 1)[0]
            if "jira_xray_id" not in head:
                continue
            delim = ";" if head.count(";") > head.count(",") else ","
            reader = csv.reader(io.StringIO(text), delimiter=delim)
            header = [h.strip().lstrip("\ufeff") for h in next(reader, [])]
            if "jira_xray_id" not in header:
                continue
            ix = header.index("jira_xray_id")
            issue_ix = header.index("jira_issue") if "jira_issue" in header else -1
            exec_ix = header.index("execute") if "execute" in header else -1
            for row in reader:
                if len(row) <= ix:
                    continue
                if exec_ix >= 0 and len(row) > exec_ix and row[exec_ix].strip().upper() == "N":
                    continue
                key = row[ix].strip()
                if not looks_like_issue_key(key):
                    continue
                issue = row[issue_ix].strip() if issue_ix >= 0 and len(row) > issue_ix else ""
                out.setdefault((cls, method), []).append({"key": key, "issue": issue, "csv": rel})
    return out


def _sheet_vs_run_hints(root: str, tests: list) -> list:
    """CSV jira_xray_id vs what TestNG actually ran (params on the method)."""
    hints = []
    csv_map = _csv_keys_by_method(root)
    if not csv_map or not tests:
        return hints
    by_cm: dict = {}
    for t in tests:
        if t.get("config"):
            continue
        by_cm.setdefault((_short_class(t.get("class", "")), t.get("method", "")), []).append(t)

    for (cls, method), sheet_rows in sorted(csv_map.items()):
        runs = by_cm.get((cls, method), [])
        if not runs:
            continue
        sheet_keys = sorted({r["key"] for r in sheet_rows})

        def _param_key(t):
            m = _ROW_KEY_RX.search(str(t.get("params") or ""))
            return m.group(1).strip() if m else ""

        passed_bare = [t for t in runs if t.get("status") == "PASS" and not looks_like_issue_key(_param_key(t))]
        if passed_bare and sheet_keys:
            keys_txt = ", ".join(sheet_keys[:4])
            if len(sheet_keys) > 4:
                keys_txt += f", … ({len(sheet_keys)} on sheet)"
            hints.append(
                f"{cls}.{method} PASSED but TestNG recorded no data row (no jira_xray_id in params). "
                f"XrayReportListener only sees keys on the Map argument. The sheet {sheet_rows[0]['csv']} "
                f"lists {keys_txt} — wire PerMethodCsvDataProvider (or @DataProvider) so the row is injected; "
                f"a pass without a key is not sent to Xray."
            )
        for t in runs:
            if t.get("status") != "SKIP":
                continue
            key = _param_key(t)
            if not looks_like_issue_key(key):
                continue
            msg = " ".join((t.get("message") or "").split())[:140]
            hints.append(
                f"{key} was on a SKIPPED row for {cls}.{method}"
                + (f" ({msg})" if msg else "")
                + " — the listener does not POST a pass for skipped invocations."
            )
    return hints[:12]


def _keys_from_results(root: str) -> dict:
    """Counts from testng-results.xml: keyed, bad shape, no key."""
    ex = _load("xraytrace_execution", os.path.join(HERE, "execution.py"))
    path = os.path.join(root, RESULTS_REL)
    try:
        res = ex.read(path)
    except ValueError:
        return {"problem": "no readable testng-results.xml", "total": 0}
    keyed, bad, none = 0, 0, 0
    bad_samples = []
    for t in res.get("tests") or []:
        if t.get("config"):
            continue
        m = _ROW_KEY_RX.search(str(t.get("params") or ""))
        key = m.group(1).strip() if m else ""
        if not key:
            none += 1
            continue
        if looks_like_issue_key(key):
            keyed += 1
        else:
            bad += 1
            if len(bad_samples) < 3:
                bad_samples.append(key[:80])
    return {"problem": "", "total": len(res.get("tests") or []), "passed": res.get("passed", 0),
            "keyed": keyed, "bad_key": bad, "no_key": none, "bad_samples": bad_samples}


def trace(root: str = "", suite_rel: str = "", maven_output: str = "") -> dict:
    """Structured trace: steps (short), hints (actionable), xray_client lines."""
    root = root or ROOT
    suite_rel = suite_rel or JIRA_SUITE_REL
    env, xray, cfg_problem = _xray_block(root)
    steps = []
    hints = []

    steps.append(f"1. Environment -Denv={env} (from program_configuration.json)")
    if cfg_problem:
        hints.append(f"Could not read Xray settings: {cfg_problem}")
        return {"steps": steps, "hints": hints, "xray_client": [], "would_publish": False}

    enabled_flag = str(xray.get("enabled") or "").lower() == "true"
    flavour = _flavour(xray)
    base = str(xray.get("baseUrl") or "").strip() or "(default Cloud host)"
    project = str(xray.get("projectKey") or "").strip() or "(inferred from test keys like B2B-99771 when unset)"
    token_note = "set" if str(xray.get("token") or "").strip() else "not set"
    steps.append(f"2. xray.enabled={enabled_flag}, flavour={flavour}, baseUrl={base}, "
                 f"projectKey={project}, token={token_note}")

    would, reason = _would_publish(xray)
    steps.append(f"3. Listener would publish at suite end: {'yes' if would else 'no'}"
                 + (f" ({reason})" if reason else ""))
    if not would:
        hints.append(reason)
        hints.append("With xray.enabled off, use Commit and push → Publish to Xray after a workbench run, "
                     "or turn publishing on in Settings and run again.")

    has_listener = _suite_has_listener(root, suite_rel)
    steps.append(f"4. Suite {suite_rel} lists XrayReportListener: {'yes' if has_listener else 'no'}")
    if not has_listener:
        hints.append(f"{suite_rel} does not register {_LISTENER} — results are never sent during the run.")

    keys = _keys_from_results(root)
    tests = []
    sheet_hints = []
    if not keys.get("problem"):
        ex = _load("xraytrace_execution2", os.path.join(HERE, "execution.py"))
        try:
            tests = (ex.read(os.path.join(root, RESULTS_REL)) or {}).get("tests") or []
        except ValueError:
            pass
    if keys.get("problem"):
        steps.append(f"5. Test results: {keys['problem']}")
        hints.append("Surefire did not leave testng-results.xml — the run may not have reached TestNG.")
    else:
        steps.append(f"5. TestNG results: {keys['total']} method(s), {keys['keyed']} with a valid "
                     f"jira_xray_id key, {keys['bad_key']} with a non-key value, {keys['no_key']} with no key")
        if keys["keyed"] == 0 and keys["total"] > 0:
            hints.append("No test row carries a valid Xray test key (ABC-123 shape). The listener skips them. "
                         "Use Create in Xray + Automate so jira_xray_id holds the real TEST key, or @XrayTest.")
        if keys["bad_key"]:
            sample = ", ".join(repr(s) for s in keys["bad_samples"])
            hints.append(f"{keys['bad_key']} row(s) have jira_xray_id that is not a Jira key (e.g. {sample}). "
                         "The generated column often holds the ReadyAPI case name; the ticket is in jira_issue.")
        sheet_hints = _sheet_vs_run_hints(root, tests)
        if sheet_hints:
            steps.append(f"6. Data sheet vs run: {len(sheet_hints)} note(s)")
            hints.extend(sheet_hints)
        else:
            steps.append("6. Data sheet vs run: no CSV key mismatch with a executed method")

    log_text = _read_text(root, RUN_LOG_REL)
    maven_saved = _read_text(root, MAVEN_TRACE_REL, 50_000)
    client = _append_saved_trace(_xray_client_lines(log_text, maven_output or ""), maven_saved)
    steps.append(f"7. [XrayClient] lines (run log + target/xray-maven.trace + this Maven output): {len(client)}")
    if would and keys.get("keyed", 0) == 0 and keys.get("passed", 0) and sheet_hints:
        hints.append("A test passed but nothing keyed reached the listener — see the data sheet notes above.")
    log_has_diag = _RUN_LOG_MARKER in log_text
    if would and keys.get("keyed", 0) > 0 and not client:
        if log_has_diag:
            hints.append("[XrayClient] had no Maven stdout lines this run; scroll to the workbench Xray "
                         "diagnostics block at the end of target/b2b-full-run.log.")
        else:
            hints.append("[XrayClient] messages go to Maven stdout (System.out). Re-run from Run the tests "
                         "(appends an Xray trace to target/b2b-full-run.log) or scroll the Maven console.")
    for line in client[-12:]:
        hints.append(f"[XrayClient] {line}")
    if client:
        last = client[-1].lower()
        if "no results with a jira_xray_id" in last:
            hints.append("XrayClient had nothing to import at suite end — a pass without jira_xray_id on the "
                         "method's Map is omitted, and skipped rows are not sent as PASS.")
        elif "skipped" in last:
            hints.append("The suite ended without importing — see the [XrayClient] line above for the reason.")
        elif "http" in last and "ok" not in last.split("http")[-1][:20]:
            hints.append("Xray returned a non-success HTTP status — check baseUrl, token, and projectKey for stg.")
    elif maven_saved.strip() and not log_has_diag:
        hints.append("Last saved Maven [XrayClient] lines are in target/xray-maven.trace. "
                     "Run the tests from the workbench to append them to target/b2b-full-run.log.")
    elif log_has_diag and not client and not maven_saved.strip():
        hints.append("Scroll to the workbench Xray diagnostics block at the end of this file for the last trace.")

    return {"steps": steps, "hints": hints, "xray_client": client, "would_publish": would,
            "has_listener": has_listener, "keys": keys}


def text_section(root: str = "", suite_rel: str = "", maven_output: str = "") -> str:
    """Plain text for the Logs tab (after the run-log hints)."""
    info = trace(root, suite_rel, maven_output)
    lines = ["", "XRAY TRACE (why the Jira/Xray ticket did or did not update):"]
    lines.extend(info["steps"])
    if info["hints"]:
        lines.append("---")
        lines.extend(info["hints"])
    return "\n".join(lines)


def say_trace(root: str, suite_rel: str, maven_output: str, say, step) -> None:
    """Print the trace on Run the tests output; step() for the page's latest-step line."""
    info = trace(root, suite_rel, maven_output)
    step("Tracing whether this run reported to Xray")
    say(" ..  Xray trace (each step):")
    for line in info["steps"]:
        say(f"      {line}")
    for hint in info["hints"]:
        say(f" ..  {hint}")
