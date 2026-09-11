"""Persist Allure history so trend charts survive across report generations.

Allure reads ``<results>/history/`` when generating a report, then writes an
updated copy to ``<report>/history/``. CI caches that folder as ``allure-history/``
(gitignored) and restores it before the next ``mvn allure:report``.

Usage (from repo root)::

    python tools/allure_history.py restore    # cache -> results/history
    python tools/allure_history.py executor   # write results/executor.json from CI env
    mvn allure:report
    python tools/allure_history.py save       # report/history -> cache
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

HISTORY_FILES = (
    "history.json",
    "history-trend.json",
    "duration-trend.json",
    "retry-trend.json",
    "categories-trend.json",
)

DEFAULT_HISTORY = "allure-history"
DEFAULT_RESULTS = "target/allure-results"
DEFAULT_REPORT = "target/allure-report"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _dir(value: Optional[str], default: str, root: Path) -> Path:
    path = Path(value or default)
    if not path.is_absolute():
        path = root / path
    return path


def history_file_names(source: Path) -> list[str]:
    if not source.is_dir():
        return []
    names = []
    for name in HISTORY_FILES:
        if (source / name).is_file():
            names.append(name)
    for extra in sorted(p.name for p in source.iterdir() if p.is_file() and p.name not in names):
        names.append(extra)
    return names


def copy_history(source: Path, dest: Path) -> int:
    """Copy Allure history files from ``source`` into ``dest``. Returns file count."""
    names = history_file_names(source)
    if not names:
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in names:
        src = source / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            copied += 1
    return copied


def restore(
    history_dir: Path,
    results_dir: Path,
) -> int:
    """Copy cached history into allure-results so the next generate can trend."""
    dest = results_dir / "history"
    count = copy_history(history_dir, dest)
    return count


def save(
    report_dir: Path,
    history_dir: Path,
) -> int:
    """Replace the cache folder with the freshly generated report history."""
    source = report_dir / "history"
    names = history_file_names(source)
    if not names:
        return 0
    if history_dir.exists():
        shutil.rmtree(history_dir)
    return copy_history(source, history_dir)


def write_executor(results_dir: Path, payload: dict) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / "executor.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def executor_from_env(env: Optional[dict] = None) -> Optional[dict]:
    """Build Allure executor.json from GitHub Actions or GitLab CI environment."""
    env = env if env is not None else os.environ
    if env.get("GITHUB_ACTIONS") == "true" or env.get("GITHUB_RUN_ID"):
        server = env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        repo = env.get("GITHUB_REPOSITORY", "")
        run_id = env.get("GITHUB_RUN_ID", "")
        run_number = env.get("GITHUB_RUN_NUMBER") or "0"
        attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
        build_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else server
        try:
            build_order = int(run_number)
        except ValueError:
            build_order = 0
        return {
            "name": "GitHub Actions",
            "type": "github",
            "url": f"{server}/{repo}" if repo else server,
            "buildOrder": build_order,
            "buildName": f"#{run_number}" + (f" attempt {attempt}" if attempt not in ("", "1") else ""),
            "buildUrl": build_url,
            "reportName": "Allure",
        }
    if env.get("GITLAB_CI") == "true" or env.get("CI_PIPELINE_ID"):
        project_url = (env.get("CI_PROJECT_URL") or "").rstrip("/")
        pipeline_url = env.get("CI_PIPELINE_URL") or project_url
        pages_url = (env.get("CI_PAGES_URL") or "").rstrip("/")
        iid = env.get("CI_PIPELINE_IID") or env.get("CI_PIPELINE_ID") or "0"
        try:
            build_order = int(iid)
        except ValueError:
            build_order = 0
        payload = {
            "name": "GitLab CI",
            "type": "gitlab",
            "url": project_url,
            "buildOrder": build_order,
            "buildName": env.get("CI_PIPELINE_ID", ""),
            "buildUrl": pipeline_url,
            "reportName": "Allure",
        }
        if pages_url:
            payload["reportUrl"] = pages_url
        return payload
    return None


def status_lines(
    history_dir: Path,
    results_dir: Path,
    report_dir: Path,
) -> Iterable[str]:
    cache_n = len(history_file_names(history_dir))
    results_n = len(history_file_names(results_dir / "history"))
    report_n = len(history_file_names(report_dir / "history"))
    yield f"cache   {history_dir}: {cache_n} file(s)"
    yield f"results {results_dir / 'history'}: {results_n} file(s)"
    yield f"report  {report_dir / 'history'}: {report_n} file(s)"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "command",
        choices=("restore", "save", "status", "executor"),
        help="restore copies cache into results; save copies report history back to cache",
    )
    p.add_argument("--history-dir", default=None, help=f"cache folder (default {DEFAULT_HISTORY})")
    p.add_argument("--results-dir", default=None, help=f"Allure results (default {DEFAULT_RESULTS})")
    p.add_argument("--report-dir", default=None, help=f"Allure report (default {DEFAULT_REPORT})")
    p.add_argument("--root", default=None, help="repo root (default: parent of tools/)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else repo_root()
    history_dir = _dir(args.history_dir, DEFAULT_HISTORY, root)
    results_dir = _dir(args.results_dir, DEFAULT_RESULTS, root)
    report_dir = _dir(args.report_dir, DEFAULT_REPORT, root)

    if args.command == "restore":
        n = restore(history_dir, results_dir)
        if n:
            print(f"Restored {n} Allure history file(s) into {results_dir / 'history'}")
        else:
            print("No Allure history cache; this report starts a new trend")
        payload = executor_from_env()
        if payload:
            path = write_executor(results_dir, payload)
            print(f"Wrote {path}")
        return 0

    if args.command == "save":
        n = save(report_dir, history_dir)
        if n:
            print(f"Saved {n} Allure history file(s) to {history_dir}")
            return 0
        print("No report history to save (did allure:report run?)")
        return 0

    if args.command == "executor":
        payload = executor_from_env()
        if not payload:
            print("Not a CI environment; skipped executor.json")
            return 0
        path = write_executor(results_dir, payload)
        print(f"Wrote {path}")
        return 0

    for line in status_lines(history_dir, results_dir, report_dir):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
