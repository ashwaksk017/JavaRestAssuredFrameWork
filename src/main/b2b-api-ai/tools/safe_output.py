"""Run a workbench command with output that a slow log file cannot end.

    python tools/safe_output.py <script.py> [args...]

The workbench starts every command of a job through this (jobs.start), so
its standard output and error -- the job's log file -- tolerate a write
that times out. Seen on a Mac whose project sat in a OneDrive folder
(~/Library/CloudStorage): a write to the log raised TimeoutError
[Errno 60] while OneDrive held the file, and the command CRASHED on a
print. diag.run already protects the commands that use it; this covers
the ones that do not (the converter, verify_all, the audits) and any added
later. A line the log cannot take is tried again, then dropped, and the
drop is said once -- see diag._Tee.

The script runs as `__main__` with its own folder first on sys.path, as
`python <script.py>` would run it; its exit code is this one's.
"""
from __future__ import annotations

import importlib.util
import os
import runpy
import sys


def _tee_class():
    """diag._Tee, loaded by path (tools/ is not a package)."""
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("safe_output_diag", os.path.join(here, "diag.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._Tee


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("usage: python tools/safe_output.py <script.py> [args...]")
    script = os.path.abspath(argv[0])
    try:
        tee = _tee_class()
        sys.stdout = tee(sys.stdout)
        sys.stderr = tee(sys.stderr)
    except Exception:                                    # noqa: BLE001
        pass                     # without the guard the command still runs, as before
    sys.argv = [script] + argv[1:]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
