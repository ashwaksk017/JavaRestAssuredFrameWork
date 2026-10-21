"""Run a test file's tests in parallel processes.

    python tools/agent/test_loop.py              # through this: parallel
    python tools/agent/test_loop.py Push         # a class (or Class.test): plain unittest
    WORKBENCH_TESTS_SERIAL=1 python tools/agent/test_loop.py   # one process, as before

Measured (2026-10-21, this laptop): test_loop took 560 s, and 93% of it
was starting git processes -- about 45 a test, ~45 ms each on Windows.
The tests do not share anything: each builds its own repository in its
own temporary folder. So they run side by side, in chunks of a few
tests a process, as many processes as the machine has cores (at most
MAX_WORKERS). The exit code is 0 only when every chunk passed; a failing
chunk's whole output is printed.
"""
from __future__ import annotations

import concurrent.futures
import inspect
import os
import re
import subprocess
import sys
import time
import unittest

MAX_WORKERS = 8
CHUNK = 8                       # tests a process: small enough to balance, large enough to amortise


def _chunks(module, workers: int) -> list:
    """[[test id, ...], ...]: every test of the module, in chunks from one
    class, largest first (they bound the wall time). A chunk is at most
    CHUNK tests, and smaller for a small file: about two chunks a worker."""
    loader = unittest.defaultTestLoader
    classes = []
    for name, cls in vars(module).items():
        if inspect.isclass(cls) and issubclass(cls, unittest.TestCase) and cls.__module__ == module.__name__:
            classes.append((name, loader.getTestCaseNames(cls)))
    total = sum(len(names) for _n, names in classes)
    size = max(1, min(CHUNK, -(-total // (2 * workers))))
    out = []
    for name, names in classes:
        for i in range(0, len(names), size):
            out.append([f"{module.__name__}.{name}.{n}" for n in names[i:i + size]])
    return sorted(out, key=len, reverse=True)


def _run(ids: list, cwd: str) -> tuple:
    started = time.time()
    r = subprocess.run([sys.executable, "-m", "unittest", *ids], cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return ids, r.returncode, r.stdout.decode("utf-8", "replace"), time.time() - started


def main(module_file: str) -> None:
    """For `if __name__ == "__main__":` of a test file."""
    if len(sys.argv) > 1 or os.environ.get("WORKBENCH_TESTS_SERIAL") == "1":
        unittest.main(module="__main__", verbosity=1)
        return
    here = os.path.dirname(os.path.abspath(module_file))
    name = os.path.splitext(os.path.basename(module_file))[0]
    sys.path.insert(0, here)
    module = __import__(name)
    workers = max(1, min(MAX_WORKERS, os.cpu_count() or 2))
    chunks = _chunks(module, workers)
    workers = min(workers, len(chunks)) or 1
    started = time.time()
    ran = skipped = 0
    failed = []
    expected = sum(len(c) for c in chunks)
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for ids, rc, text, _secs in pool.map(lambda c: _run(c, here), chunks):
            m = re.search(r"^Ran (\d+) tests?", text, re.M)
            ran += int(m.group(1)) if m else 0
            s = re.search(r"skipped=(\d+)", text.splitlines()[-1] if text.strip() else "")
            skipped += int(s.group(1)) if s else 0
            if rc != 0:
                failed.append((ids, text))
    for ids, text in failed:
        print(f"===== FAILED: {ids[0].rsplit('.', 1)[0]} ({len(ids)} test(s) in this process) =====")
        print(text.rstrip())
    print("-" * 70)
    print(f"Ran {ran} tests in {time.time() - started:.3f}s ({len(chunks)} processes, {workers} at a time)")
    print()
    if ran != expected and not failed:
        # A test that was handed out and never ran is not a pass.
        print(f"FAILED ({expected} tests were handed out, {ran} ran)")
        sys.exit(1)
    if failed:
        print(f"FAILED ({len(failed)} process(es) of {len(chunks)} failed)")
        sys.exit(1)
    print("OK" + (f" (skipped={skipped})" if skipped else ""))
