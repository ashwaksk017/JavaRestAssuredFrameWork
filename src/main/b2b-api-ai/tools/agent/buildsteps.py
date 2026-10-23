"""Around a test run: a clean build before it, the Allure report after.

Both Run buttons use this -- the Run tests tab (mvnrun.py) and the
change's Run the tests (loop.py run).

A clean build, not `mvn clean`: `mvn clean` deletes all of target/, and
the workbench keeps things there -- ReadyAPI files uploaded on the
Convert tab and waiting to convert (target/convert-input), the Failures
tab's digest, fetched Jira stories, the converter's shape index. What is
deleted here is what Maven builds and what a run reports: the compiled
classes, Maven's record of them, the surefire reports, the Allure results
and report. The run then compiles from nothing and the report holds that
run only -- what `mvn clean compile` was asked for.

The report: `mvn allure:report` writes target/allure-report. It is an
app (scripts that fetch its data files), and its attachments are the
requests and responses of the run -- text that came from the APIs. So it
is served on a port of its own (the workbench's + 1): another origin,
whose scripts cannot reach the workbench's API (server.py).
"""
from __future__ import annotations

import os
import shutil

# Under target/: what Maven builds, and what a run reports.
CLEANED = ("classes", "test-classes", "generated-sources", "generated-test-sources",
           "maven-status", "surefire-reports", "allure-results", "allure-report")
REPORT_REL = "target/allure-report"

# Explicit: on Windows mimetypes reads the registry, which has served .js as
# text/plain -- and with nosniff the browser then refuses to run it.
TYPES = {".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
         ".ttf": "font/ttf", ".eot": "application/vnd.ms-fontobject",
         ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
         ".xml": "application/xml; charset=utf-8", ".log": "text/plain; charset=utf-8"}


def clean_build_outputs(root: str) -> list:
    """Delete what Maven builds and what a run reports (CLEANED, under
    target/); nothing else. Returns the folders removed."""
    removed = []
    for name in CLEANED:
        p = os.path.join(root, "target", name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            if not os.path.exists(p):
                removed.append(f"target/{name}")
    return removed


# What one run reports to Allure. Allure builds its report from EVERY file
# in allure-results: left from an earlier run, they are in this run's report.
RUN_REPORTS = ("allure-results", "allure-report")


def clear_run_reports(root: str) -> list:
    """Delete an earlier run's Allure results and report (RUN_REPORTS, under
    target/) -- before a run without a clean build. Returns those removed."""
    removed = []
    for name in RUN_REPORTS:
        p = os.path.join(root, "target", name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            if not os.path.exists(p):
                removed.append(f"target/{name}")
    return removed


def report_dir(root: str) -> str:
    return os.path.join(root, *REPORT_REL.split("/"))


def report_ready(root: str) -> bool:
    return os.path.isfile(os.path.join(report_dir(root), "index.html"))


def report_file(root: str, url_path: str):
    """(bytes, content type) of a file of the report for a GET of
    `url_path`, or None: only files inside the report folder, "/" is its
    index.html, and nothing else of the machine is reachable."""
    base = os.path.realpath(report_dir(root))
    rel = (url_path or "/").split("?", 1)[0].split("#", 1)[0].lstrip("/") or "index.html"
    rel = rel.replace("%20", " ")
    if "\x00" in rel or ".." in rel.replace("\\", "/").split("/"):
        return None
    full = os.path.realpath(os.path.join(base, *rel.replace("\\", "/").split("/")))
    if not (full == base or full.startswith(base + os.sep)) or not os.path.isfile(full):
        return None
    with open(full, "rb") as fh:
        data = fh.read()
    return data, TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
