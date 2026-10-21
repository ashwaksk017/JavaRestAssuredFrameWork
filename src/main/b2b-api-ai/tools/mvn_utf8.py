"""Run Maven and keep its output UTF-8, on the console and in a log file.

    python tools/mvn_utf8.py --log full-run.log test "-DsuiteXmlFile=Suites/<Suite>_Regression.xml"

Every argument after `--log FILE` goes to mvn unchanged. The log is
written here, byte for byte as Maven printed it: a PowerShell 5.1 redirect
(`... > run.log`) re-decodes a program's output with the console code page,
whatever the program wrote, so non-Latin datasheet values come out mangled.
The JVMs are already UTF-8 (pom argLine + .mvn/jvm.config).

Maven is the project's wrapper, else mvn on PATH; a JAVA_HOME that holds
no java falls back to the oldest installed JDK >= 17, as the workbench does.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _mvnrun():
    name = "mvn_utf8_mvnrun"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "agent", "mvnrun.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def split_args(argv: list) -> tuple:
    """(log path or "", the arguments for mvn)."""
    if argv and argv[0] == "--log" and len(argv) > 1:
        return argv[1], argv[2:]
    if argv and argv[0].startswith("--log="):
        return argv[0][len("--log="):], argv[1:]
    return "", list(argv)


def main(argv=None) -> int:
    log, args = split_args(sys.argv[1:] if argv is None else argv)
    mvnrun = _mvnrun()
    mvn = mvnrun.maven(ROOT)
    if not mvn:
        print("FAIL no Maven: neither mvnw in the project nor mvn on PATH.")
        return 2
    env, note = mvnrun.java_env()
    if note:
        print(note)
    out = sys.stdout.buffer
    sink = open(log, "wb") if log else None
    try:
        proc = subprocess.Popen([mvn] + args, cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for chunk in iter(lambda: proc.stdout.read1(65536), b""):
            out.write(chunk)
            out.flush()
            if sink:
                sink.write(chunk)
        rc = proc.wait()
    finally:
        if sink:
            sink.close()
    if rc != 0:
        print(f"FAIL mvn exited {rc}" + (f"; the output is in {log}" if log else ""))
    return rc


if __name__ == "__main__":
    sys.exit(_mvnrun().diag.run("mvn_utf8", main))
