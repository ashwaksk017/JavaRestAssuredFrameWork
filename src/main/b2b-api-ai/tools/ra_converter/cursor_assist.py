"""Call a Cursor agent when ra_converter is confused or likely wrong.

Flow (when enabled and gaps exist):
  1. Write ``_audit/cursor_assist_report.md`` (full conversion-gap inventory)
  2. Call one local Cursor agent to fix emit/helpers (samples + memory)
  3. Write ``_audit/cursor_assist_after.md`` (agent result + git diff)
  4. Refresh ``_audit/cursor_assist.md`` index
  5. Append a run stub to ``cursor_assist_memory.md`` so later converts
     see prior lessons

Config (never commit a real key):
  tools/ra_converter/cursor_agent.json          gitignored
  tools/ra_converter/cursor_agent.json.example  committed shape

Key resolution (highest first): CURSOR_API_KEY env, then config ``apiKey``.
The convert does not fail if assist is off, the key is missing, or
``cursor-sdk`` is not installed.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

CONFUSED_COVERAGE = frozenset({"TODO", "STUB", "PARTIAL"})
PREFLIGHT_SEV = frozenset({"HIGH", "BLOCKER"})
PREFLIGHT_OK = frozenset({
    "token-injected",
    "token-hoist",
    "hardcoded-path-id-rewritten",
    "hardcoded-path-id-preserved-as-testfake",
})

_PENDING: list[tuple[str, list["Gap"]]] = []

_DEFAULT_REL = "cursor_agent.json"
_MEMORY_REL = "cursor_assist_memory.md"
_MEMORY_MAX_CHARS = 12000

# CLI summary: show counts in full, then this many rows per category.
_CLI_ROWS_PER_CATEGORY = 12


_WATCH_PATHS = (
    "tools/ra_converter/ra_converter.py",
    "tools/ra_converter/groovy_translator.py",
    "tools/ra_converter/fluent_scenario.py",
    "tools/ra_converter/cursor_assist.py",
    "tools/ra_converter/cursor_assist_memory.md",
    "src/main/java/com/hi/api/support/ImportedScenario.java",
    "src/main/java/com/hi/api/support/CtxFields.java",
    "src/main/java/com/hi/api/rest/utilities/ResponseAsserts.java",
    "src/main/java/com/hi/api/rest/utilities/AuthHelper.java",
    "src/main/java/com/hi/api/db/Db.java",
    ".cursor/skills/readyapi-restassured-migration/",
)


@dataclass
class CursorAssistConfig:
    enabled: bool = False
    api_key: str = ""
    model: str = "composer-2.5"
    runtime: str = "local"
    cwd: str = ""
    max_samples_per_category: int = 3
    config_path: str = ""


@dataclass
class Gap:
    suite: str
    kind: str
    category: str
    case: str
    step: str
    detail: str
    severity: str = ""


def converter_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def repo_root() -> str:
    return os.path.abspath(os.path.join(converter_dir(), "..", ".."))


def memory_path() -> str:
    override = (os.environ.get("CURSOR_ASSIST_MEMORY") or "").strip()
    if override:
        return override
    return os.path.join(converter_dir(), _MEMORY_REL)


def config_path_from_args(args: Any = None) -> str:
    if args is not None:
        explicit = getattr(args, "cursor_config", None)
        if explicit:
            return os.path.abspath(explicit)
    return os.path.join(converter_dir(), _DEFAULT_REL)


def load_config(args: Any = None) -> CursorAssistConfig:
    path = config_path_from_args(args)
    raw: dict = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            raw = loaded
    cfg = CursorAssistConfig(
        enabled=_as_bool(raw.get("enabled", False)),
        api_key=str(raw.get("apiKey") or raw.get("api_key") or "").strip(),
        model=str(raw.get("model") or "composer-2.5").strip() or "composer-2.5",
        runtime=str(raw.get("runtime") or "local").strip().lower() or "local",
        cwd=str(raw.get("cwd") or "").strip(),
        max_samples_per_category=max(
            1, int(raw.get("maxSamplesPerCategory") or raw.get("max_samples") or 3)),
        config_path=path if os.path.isfile(path) else "",
    )
    env_key = (os.environ.get("CURSOR_API_KEY") or "").strip()
    if env_key:
        cfg.api_key = env_key
    if args is not None:
        if getattr(args, "cursor_assist", False):
            cfg.enabled = True
        if getattr(args, "no_cursor_assist", False):
            cfg.enabled = False
    if not cfg.cwd:
        out = getattr(args, "output", None) if args is not None else None
        cfg.cwd = os.path.abspath(out) if out else repo_root()
    return cfg


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def collect_gaps(ledger: Any, suite: str) -> list[Gap]:
    """Pull confusion / likely-wrong rows from an AuditLedger."""
    out: list[Gap] = []
    suite = suite or ""
    for row in getattr(ledger, "assertions", []) or []:
        if len(row) < 7:
            continue
        cov = str(row[6] or "").upper()
        if cov in CONFUSED_COVERAGE:
            out.append(Gap(suite, "assertion", cov, str(row[1]), str(row[2]),
                           str(row[3]), cov))
    for row in getattr(ledger, "groovy", []) or []:
        if len(row) < 5:
            continue
        cov = str(row[4] or "").upper()
        if cov in CONFUSED_COVERAGE:
            preview = str(row[5]) if len(row) > 5 else ""
            out.append(Gap(suite, "groovy", cov, str(row[1]), str(row[2]),
                           preview[:400], cov))
    for row in getattr(ledger, "steps", []) or []:
        if len(row) < 7:
            continue
        cov = str(row[6] or "").upper()
        if cov in CONFUSED_COVERAGE:
            detail = str(row[7]) if len(row) > 7 else str(row[3])
            out.append(Gap(suite, "step", cov, str(row[1]), str(row[2]),
                           detail, cov))
    for row in getattr(ledger, "unmapped", []) or []:
        if len(row) < 5:
            continue
        out.append(Gap(suite, "unmapped", str(row[3]), str(row[1]), str(row[2]),
                       str(row[4]), "HIGH"))
    for row in getattr(ledger, "preflight", []) or []:
        if len(row) < 4:
            continue
        sev = str(row[0] or "").upper()
        cat = str(row[1] or "")
        if sev not in PREFLIGHT_SEV or cat in PREFLIGHT_OK:
            continue
        out.append(Gap(suite, "preflight", cat, str(row[2]), "",
                       str(row[3]), sev))
    for row in getattr(ledger, "runtime_skips", []) or []:
        if len(row) < 5:
            continue
        out.append(Gap(suite, "runtime_skip", "skip-exception", str(row[1]),
                       str(row[4]), str(row[3]), "HIGH"))
    return out


def enqueue(suite: str, ledger: Any) -> list[Gap]:
    gaps = collect_gaps(ledger, suite)
    if gaps:
        _PENDING.append((suite, gaps))
    return gaps


def pending_count() -> int:
    return sum(len(g) for _, g in _PENDING)


def clear_pending() -> None:
    _PENDING.clear()


def group_samples(gaps: list[Gap], max_per: int) -> dict[str, list[Gap]]:
    grouped: dict[str, list[Gap]] = defaultdict(list)
    for g in gaps:
        key = f"{g.kind}:{g.category}"
        if len(grouped[key]) < max_per:
            grouped[key].append(g)
    return dict(grouped)


def gap_counts(gaps: list[Gap]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for g in gaps:
        counts[f"{g.kind}:{g.category}"] += 1
    return dict(counts)


def build_conversion_report(
        gaps: list[Gap], suite_names: list[str], *,
        max_rows_per_category: int = 40) -> str:
    """Full inventory for humans. Not sampled down to 3."""
    counts = gap_counts(gaps)
    grouped: dict[str, list[Gap]] = defaultdict(list)
    for g in gaps:
        grouped[f"{g.kind}:{g.category}"].append(g)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Conversion report (before assist fix)",
        "",
        f"- generated: {now}",
        f"- suites: {', '.join(suite_names) or '(unknown)'}",
        f"- gap rows: {len(gaps)}",
        "",
        "This is **not** a full ReadyAPI XML vs Java walk. Rows are converter "
        "confusion signals (TODO/STUB/PARTIAL, unmapped, HIGH preflight, "
        "runtime skips). Coverage `FULL` is omitted — those cases can still "
        "diverge from ReadyAPI (example: DataGenInput space-concat `name`/`name2`).",
        "",
        "## Counts",
        "",
    ]
    for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{key}`: {n}")
    lines.append("")
    for key, rows in sorted(grouped.items()):
        shown = rows[:max_rows_per_category]
        extra = len(rows) - len(shown)
        lines.append(f"## {key}  (n={len(rows)})")
        lines.append("")
        for g in shown:
            step = f" step=`{g.step}`" if g.step else ""
            lines.append(
                f"- [{g.severity or g.category}] suite=`{g.suite}` "
                f"case=`{g.case}`{step}")
            detail = (g.detail or "").replace("\n", " ").strip()
            if detail:
                lines.append(f"  {detail[:500]}")
        if extra > 0:
            lines.append(f"- … {extra} more in this category")
        lines.append("")
    lines.append("## Next")
    lines.append("")
    lines.append("If assist is enabled and an API key is set, a Cursor agent "
                 "will attempt **emitter/helper** fixes (not hand-edits of "
                 "imported tests), then write `cursor_assist_after.md`.")
    lines.append("")
    return "\n".join(lines)


def format_cli_summary(
        gaps: list[Gap], suite_names: list[str], *,
        report_path: str = "",
        max_rows_per_category: int = _CLI_ROWS_PER_CATEGORY) -> str:
    """Compact conversion-gap summary for stdout (full list stays in the md)."""
    counts = gap_counts(gaps)
    grouped: dict[str, list[Gap]] = defaultdict(list)
    for g in gaps:
        grouped[f"{g.kind}:{g.category}"].append(g)
    suite_label = ", ".join(dict.fromkeys(suite_names)) or "(unknown)"
    lines = [
        "[ra_converter] cursor assist: conversion report",
        f"  file: {report_path or '_audit/cursor_assist_report.md'}",
        f"  suites: {suite_label}",
        f"  gap rows: {len(gaps)}",
        "",
        "  Counts:",
    ]
    key_w = max((len(k) for k in counts), default=8)
    for key, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"    {key.ljust(key_w)}  {n}")
    lines.append("")
    lines.append("  Details:")
    for key, rows in sorted(grouped.items()):
        shown = rows[:max_rows_per_category]
        extra = len(rows) - len(shown)
        lines.append(f"    {key}  (n={len(rows)})")
        for g in shown:
            step = f"  step={g.step}" if g.step else ""
            lines.append(
                f"      [{g.severity or g.category}] case={g.case}{step}")
            detail = (g.detail or "").replace("\n", " ").strip()
            if detail:
                lines.append(f"        {detail[:220]}")
        if extra > 0:
            lines.append(f"      ... {extra} more in this category (see report file)")
    lines.append("")
    return "\n".join(lines)


def load_memory() -> str:
    path = memory_path()
    if not os.path.isfile(path):
        return ""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    if len(text) > _MEMORY_MAX_CHARS:
        return text[-_MEMORY_MAX_CHARS:]
    return text


def _memory_header() -> str:
    return (
        "# Cursor assist memory\n\n"
        "Lessons from converter-assist runs. The next convert prepends this "
        "file to the fix-agent prompt so the same ReadyAPI-vs-emit mistakes "
        "are less likely to repeat. Keep bullets short. No secrets.\n"
    )


def append_memory_run(
        suites: list[str], gap_n: int, *,
        status: str, run_id: str, changed: str, lesson: str) -> None:
    path = memory_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(_memory_header())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lesson = (lesson or "").strip() or "(no lesson extracted)"
    block = (
        f"\n## {now}\n"
        f"- suites: {', '.join(suites) or '(unknown)'}\n"
        f"- gaps: {gap_n}\n"
        f"- agent status: {status or '(none)'}  run: {run_id or '(none)'}\n"
        f"- changed (watch paths):\n{changed or '  (none)'}\n"
        f"- lesson:\n{lesson}\n"
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(block)


def extract_lesson(agent_text: str) -> str:
    """Prefer an After-fix / Lesson heading; else a short tail of the result."""
    text = (agent_text or "").strip()
    if not text:
        return ""
    for marker in ("## After-fix summary", "## Lesson", "## Lessons"):
        idx = text.lower().rfind(marker.lower())
        if idx >= 0:
            return text[idx:idx + 1500]
    return text[-800:]


def build_prompt(
        gaps: list[Gap], max_per: int, suite_names: list[str], *,
        report_path: str = "",
        memory_text: str = "") -> str:
    grouped = group_samples(gaps, max_per)
    counts = gap_counts(gaps)
    lines = [
        "ReadyAPI (SoapUI) was converted to Rest Assured by tools/ra_converter.",
        "A conversion report was already written (read it before editing):",
        f"  {report_path or '_audit/cursor_assist_report.md'}",
        "The samples below are a subset. Do not assume unlisted FULL cases are correct.",
        "",
        "Hard rules:",
        "- Do NOT hand-edit tests/imported/, support/<suite>/, or that suite's CSVs/templates.",
        "- Fix emit in ra_converter.py / groovy_translator.py / fluent_scenario.py,",
        "  or runtime helpers (ImportedScenario, CtxFields, ResponseAsserts, AuthHelper, Db).",
        "- Token fetch is setup (SetupHelper.flow_A / AuthHelper), never a @Test.",
        "- Cleanup is SuiteCleanup.afterEachTest, never a @Test.",
        "- Do not invent Salesforce credentials or Selenium for HWS.",
        "- Do not expand Suites/Smbtohwsconsumerregressione2e_Smoke.xml past B2B7816.",
        "- Do not regress: identity pack, putExtracted hiltonmemberid, POST /remove,",
        "  token skip-regen, jsonAbsent polarity, member-enroll Properties.Email overlay,",
        "  ReadyAPI last-write-wins setPropertyValue, DataGenInput space-concat name/name2,",
        "  jsonEquals path mismatch (no sibling-scalar pass), OTP pad, unsafeSqlReason gate.",
        "- Follow .cursor/skills/readyapi-restassured-migration/SKILL.md",
        "- Add converter unit tests for each emit/helper change.",
        "- Do not reconvert unless emit changed; if you reconvert, snapshot and restore",
        "  narrowed smoke XMLs.",
        "- Append 1-5 new bullets to tools/ra_converter/cursor_assist_memory.md",
        "  (pattern vs ReadyAPI, file fixed, do-not-regress). Do not duplicate existing bullets.",
        "",
        f"Suites: {', '.join(suite_names) or '(unknown)'}",
        f"Total gap rows: {len(gaps)}  (samples capped at {max_per} per category)",
        "",
    ]
    if memory_text.strip():
        lines.append("## Prior assist memory (do not repeat these mistakes)")
        lines.append("")
        lines.append(memory_text.strip())
        lines.append("")
    for key, samples in grouped.items():
        lines.append(f"### {key}  (n={counts[key]})")
        for g in samples:
            step = f" step={g.step}" if g.step else ""
            lines.append(f"- [{g.severity or g.category}] suite={g.suite} case={g.case}{step}")
            detail = (g.detail or "").replace("\n", " ").strip()
            if detail:
                lines.append(f"  {detail[:500]}")
        lines.append("")
    lines.append(
        "Apply emitter/helper fixes for these patterns. "
        "End your reply with:\n\n"
        "## After-fix summary\n"
        "- files changed:\n"
        "- reconvert needed: yes/no\n"
        "- tests added:\n"
        "- residual risk:\n"
    )
    return "\n".join(lines)


def redact_for_log(text: str, api_key: str) -> str:
    if api_key and api_key in text:
        return text.replace(api_key, "<redacted>")
    return text


def _read_bridge_discovery(
        process: Any, timeout: float, parse_line: Callable[[str], Any],
        error_cls: type[Exception] = RuntimeError) -> Any:
    """Read ``cursor-sdk-bridge ready ...`` from stderr without ``select()``.

    cursor-sdk uses ``selectors.DefaultSelector`` on the bridge stderr pipe.
    On Windows that is ``select()``, which only accepts sockets and raises
    ``WinError 10038``. Poll the pipe from a reader thread instead.
    """
    if getattr(process, "stderr", None) is None:
        raise error_cls("Bridge process stderr is unavailable")
    done: queue.Queue = queue.Queue()

    def _reader() -> None:
        try:
            for line in process.stderr:
                done.put(("line", line))
            done.put(("eof", None))
        except Exception as exc:
            done.put(("err", exc))

    threading.Thread(target=_reader, daemon=True).start()
    stderr_bits: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        try:
            kind, payload = done.get(timeout=min(0.1, remaining))
        except queue.Empty:
            if process.poll() is None:
                continue
            try:
                kind, payload = done.get(timeout=0.2)
            except queue.Empty:
                raise error_cls(
                    "Bridge exited before discovery with status "
                    f"{process.poll()}: " + "".join(stderr_bits)
                )
        if kind == "line":
            stderr_bits.append(str(payload))
            parsed = parse_line(str(payload))
            if parsed is not None:
                return parsed
            continue
        if kind == "err":
            raise error_cls(str(payload)) from payload
        raise error_cls(
            "Bridge exited before discovery with status "
            f"{process.poll()}: " + "".join(stderr_bits)
        )
    raise error_cls("Timed out waiting for bridge discovery")


def _patch_cursor_sdk_bridge_for_windows() -> None:
    """Replace cursor-sdk's select()-based bridge discovery on Windows."""
    if os.name != "nt":
        return
    import cursor_sdk._bridge as bridge
    from cursor_sdk.errors import CursorSDKError
    if getattr(bridge._read_discovery, "_ra_windows_safe", False):
        return

    def _read_discovery_win(process: Any, timeout: float) -> Any:
        return _read_bridge_discovery(
            process, timeout, bridge.parse_discovery_line,
            error_cls=CursorSDKError)

    _read_discovery_win._ra_windows_safe = True  # type: ignore[attr-defined]
    bridge._read_discovery = _read_discovery_win


def _sdk_prompt(prompt: str, cfg: CursorAssistConfig) -> dict[str, Any]:
    try:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ImportError as e:
        raise RuntimeError(
            "cursor-sdk is not installed. Run: pip install cursor-sdk"
        ) from e
    if cfg.runtime != "local":
        raise RuntimeError(
            f"cursor assist runtime={cfg.runtime!r} is not supported; use local"
        )
    _patch_cursor_sdk_bridge_for_windows()
    options = AgentOptions(
        api_key=cfg.api_key,
        model=cfg.model,
        local=LocalAgentOptions(cwd=cfg.cwd),
    )
    try:
        result = Agent.prompt(prompt, options)
    except OSError as e:
        if getattr(e, "winerror", None) == 10038:
            raise RuntimeError(
                "Cursor SDK failed to start the local bridge on Windows "
                "(WinError 10038: select() on a pipe). The converter patches "
                "this; if it still fails, upgrade cursor-sdk."
            ) from e
        raise
    status = getattr(result, "status", None) or ""
    text = getattr(result, "result", None)
    if text is None:
        text = str(result)
    run_id = getattr(result, "id", "") or ""
    if str(status).lower() == "error":
        raise RuntimeError(f"Cursor agent run failed id={run_id} status={status}")
    return {"status": str(status), "result": str(text), "id": str(run_id)}


def _git(cwd: str, *git_args: str) -> str:
    try:
        r = subprocess.run(
            ["git", *git_args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return out.strip()
    except Exception as ex:
        return f"(git unavailable: {ex})"


def git_watch_diff(cwd: str) -> str:
    parts = []
    st = _git(cwd, "status", "--porcelain", "--", *_WATCH_PATHS)
    if st:
        parts.append("### git status (watch paths)\n\n```\n" + st[:4000] + "\n```")
    diff = _git(cwd, "diff", "--stat", "--", *_WATCH_PATHS)
    if diff:
        parts.append("### git diff --stat\n\n```\n" + diff[:4000] + "\n```")
    full = _git(cwd, "diff", "--", *_WATCH_PATHS)
    if full:
        parts.append("### git diff (truncated)\n\n```diff\n" + full[:12000] + "\n```")
    return "\n\n".join(parts) if parts else "(no changes on converter/helper watch paths)"


def _audit_dir(output_dir: str) -> str:
    base = os.path.join(output_dir, "_audit")
    os.makedirs(base, exist_ok=True)
    return base


def write_text(path: str, body: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def write_report(output_dir: str, body: str) -> str:
    """Index file path kept for callers/tests."""
    return write_text(os.path.join(_audit_dir(output_dir), "cursor_assist.md"), body)


def write_index(
        output_dir: str, *,
        suites: list[str], gap_n: int, model: str,
        report_rel: str, after_rel: str,
        fix_status: str, run_id: str = "") -> str:
    body = (
        f"# Cursor assist\n\n"
        f"1. **Conversion report** (before fix): [{report_rel}]({report_rel})\n"
        f"2. **Fix attempt**: {fix_status}"
        f"{f' (run `{run_id}`)' if run_id else ''}\n"
        f"3. **After-fix report**: [{after_rel}]({after_rel})\n"
        f"4. **Memory** (next convert reads this): "
        f"`tools/ra_converter/cursor_assist_memory.md`\n\n"
        f"- suites: {', '.join(suites) or '(unknown)'}\n"
        f"- gaps: {gap_n}\n"
        f"- model: {model}\n"
        f"- generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
    )
    return write_report(output_dir, body)


def flush(
    args: Any = None,
    *,
    call_agent: Optional[Callable[[str, CursorAssistConfig], dict[str, Any]]] = None,
) -> Optional[str]:
    """Conversion report, then one fix-agent call, then after-fix report."""
    gaps: list[Gap] = []
    suites: list[str] = []
    for suite, suite_gaps in _PENDING:
        suites.append(suite)
        gaps.extend(suite_gaps)
    clear_pending()
    cfg = load_config(args)
    if not cfg.enabled:
        return None
    out = getattr(args, "output", ".") if args is not None else "."
    if not gaps:
        print("[ra_converter] cursor assist: enabled, no confusion signals "
              "(FULL coverage is not reviewed)")
        return None

    report_body = build_conversion_report(gaps, suites)
    report_path = write_text(
        os.path.join(_audit_dir(out), "cursor_assist_report.md"), report_body)
    print(format_cli_summary(gaps, suites, report_path=report_path), end="")

    after_path = os.path.join(_audit_dir(out), "cursor_assist_after.md")
    if not cfg.api_key:
        print("[ra_converter] cursor assist: enabled but no API key "
              "(set CURSOR_API_KEY or apiKey in cursor_agent.json) -- "
              "fix skipped, report kept")
        write_text(after_path, "# After-fix report\n\nFix skipped: no API key.\n")
        return write_index(
            out, suites=suites, gap_n=len(gaps), model=cfg.model,
            report_rel="cursor_assist_report.md",
            after_rel="cursor_assist_after.md",
            fix_status="skipped (no API key)")

    memory = load_memory()
    prompt = build_prompt(
        gaps, cfg.max_samples_per_category, suites,
        report_path=report_path, memory_text=memory)
    print(f"[ra_converter] cursor assist: calling Cursor agent to fix "
          f"(model={cfg.model}, cwd={cfg.cwd})")
    fn = call_agent or _sdk_prompt
    try:
        result = fn(prompt, cfg)
    except Exception as e:
        msg = redact_for_log(str(e), cfg.api_key)
        print(f"[ra_converter] cursor assist failed: {msg}", file=sys.stderr)
        write_text(after_path, f"# After-fix report\n\nFix failed:\n\n{msg}\n")
        return write_index(
            out, suites=suites, gap_n=len(gaps), model=cfg.model,
            report_rel="cursor_assist_report.md",
            after_rel="cursor_assist_after.md",
            fix_status=f"failed: {msg[:200]}")

    text = redact_for_log(str(result.get("result") or ""), cfg.api_key)
    changed = git_watch_diff(cfg.cwd)
    after_body = (
        f"# After-fix report\n\n"
        f"- status: {result.get('status')}\n"
        f"- run id: {result.get('id')}\n"
        f"- model: {cfg.model}\n"
        f"- gaps: {len(gaps)}\n\n"
        f"## Git (converter / helper watch paths)\n\n{changed}\n\n"
        f"## Agent result\n\n{text}\n\n"
        f"Review emitter changes, run "
        f"`python tools/ra_converter/test_converter_fixes.py` and "
        f"`test_cross_case_contracts.py`, then reconvert only if emit changed. "
        f"Restore narrowed smoke XMLs after `--clean`.\n"
    )
    write_text(after_path, after_body)
    try:
        append_memory_run(
            suites, len(gaps),
            status=str(result.get("status") or ""),
            run_id=str(result.get("id") or ""),
            changed=changed[:1500],
            lesson=extract_lesson(text),
        )
    except OSError as ex:
        print(f"[ra_converter] cursor assist: memory append skipped: {ex}")
    index = write_index(
        out, suites=suites, gap_n=len(gaps), model=cfg.model,
        report_rel="cursor_assist_report.md",
        after_rel="cursor_assist_after.md",
        fix_status=str(result.get("status") or "finished"),
        run_id=str(result.get("id") or ""))
    print(f"[ra_converter] cursor assist: wrote {index}")
    print("[ra_converter] cursor assist: review emitter changes then reconvert "
          "if the agent patched ra_converter.py")
    return index
