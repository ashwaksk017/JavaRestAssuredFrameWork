"""Compile the project, or run tests, from the page: no command line.

    python tools/agent/mvnrun.py compile --job J
    python tools/agent/mvnrun.py test --job J --suite Suites/jira/group-smoke.xml --env stg
    python tools/agent/mvnrun.py test --job J --test GuestProgramAccountTest[#method]
    python tools/agent/mvnrun.py test --job J --group smoke --env stg [--compile-first]

What is run is one of three: a suite file under Suites/, one test class
(or one method of it), or one TestNG group. Each is checked against what
is on disk -- a suite that is not a file under Suites/, a class that is
not a *Test.java under src/test/java, a group name with odd characters
-- before Maven is started; nothing typed on the page reaches a shell.

Maven runs offline first and online only when its output says something
was missing (as every build of the workbench does). A JAVA_HOME that
points at a JDK which is not there -- seen on a machine where JDK 17 was
uninstalled and 25 put in its place -- is not a reason to fail: the JDK
used is then the oldest installed one that is at least 17 (the pom's
release), and the log says so.

Writes mvnrun-result.json into the job: what was run, the exit code, the
verdict, and the tests (testng-results.xml read as the push gate reads
it). Exit 0 when the build passed and every test that ran passed.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, path: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


loop = _load("mvnrun_loop", os.path.join(HERE, "loop.py"))
buildsteps = _load("mvnrun_buildsteps", os.path.join(HERE, "buildsteps.py"))
execution = loop.execution
diag = loop.diag
ROOT = loop.ROOT
RESULT = "mvnrun-result.json"
RESULTS_XML = loop.RUN_RESULTS
MIN_JAVA = 17
SUITE_RX = re.compile(r"^(?:Suites/[A-Za-z0-9_./ -]{1,200}|src/test/resources/testng[A-Za-z0-9_-]{0,60})\.xml$")
TEST_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,200}(#[A-Za-z_*][A-Za-z0-9_*]{0,100})?$")
GROUP_RX = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ENV_RX = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
COMPILE_TIMEOUT = 20 * 60
SHOWN = ("Running ", "Tests run:", "[ERROR]", "BUILD ", "FAIL", "PASSED", "SKIPPED",
         "COMPILATION ERROR", "[WARNING] COMPILATION")


def say(msg: str) -> None:
    loop.say(msg)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- what may be run -------------------------------------------------------------

def list_suites(root: str = "") -> list:
    """[{path, name, group}] of every suite file under Suites/, jira ones first."""
    root = root or ROOT
    out = []
    found = glob.glob(os.path.join(root, "Suites", "**", "*.xml"), recursive=True) \
        + glob.glob(os.path.join(root, "src", "test", "resources", "testng*.xml"))
    for path in found:
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if not SUITE_RX.match(rel):
            continue
        name = os.path.basename(rel)[:-4]
        out.append({"path": rel, "name": name,
                    "kind": "project" if rel.startswith("src/") else
                            "group" if name.startswith("group-") else
                            "job" if name.startswith("job-") else
                            "jira" if rel.startswith("Suites/jira/") else "converted"})
    order = {"group": 0, "jira": 1, "project": 2, "job": 3, "converted": 4}
    out.sort(key=lambda s: (order[s["kind"]], s["path"].lower()))
    return out


def list_envs(root: str = "") -> dict:
    """{"envs": [...], "active": str}: the blocks of program_configuration.json."""
    root = root or ROOT
    envs = []
    try:
        with io.open(os.path.join(root, "src", "main", "resources", "program_configuration.json"),
                     encoding="utf-8-sig") as fh:
            doc = json.load(fh)
        envs = [k for k, v in doc.items() if isinstance(v, dict) and ENV_RX.match(k)]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        pc = _load("mvnrun_projectconfig", os.path.join(os.path.dirname(HERE), "jira", "projectconfig.py"))
        active = pc.active_env()[0]
    except Exception:                                    # noqa: BLE001
        active = ""
    if active and active not in envs:
        envs.insert(0, active)
    return {"envs": envs, "active": active}


def check_suite(root: str, suite: str) -> str:
    suite = str(suite or "").strip().replace("\\", "/")
    if not SUITE_RX.match(suite) or ".." in suite:
        raise ValueError("a suite is a .xml file under Suites/, or src/test/resources/testng*.xml")
    if not os.path.isfile(os.path.join(root, suite.replace("/", os.sep))):
        raise ValueError(f"{suite} is not there")
    return suite


def group_suite(root: str, group: str) -> str:
    """The suite file that runs one group (surefire ignores -Dgroups when a
    suite file is set, and the pom always sets one), or ""."""
    rel = "src/test/resources/testng-jira.xml" if group == "jira" else loop.suitemap.suite_rel(group)
    return rel if os.path.isfile(os.path.join(root, rel.replace("/", os.sep))) else ""


def list_groups(root: str = "") -> list:
    """The groups that have a suite file to run them."""
    root = root or ROOT
    out = ["jira"] if group_suite(root, "jira") else []
    for path in sorted(glob.glob(os.path.join(root, "Suites", "jira", "group-*.xml"))):
        g = os.path.basename(path)[len("group-"):-len(".xml")]
        if GROUP_RX.match(g) and g not in out:
            out.append(g)
    return out


def check_test(root: str, test: str) -> str:
    """`Class` or `Class#method`; the class must be a test source of this project."""
    test = str(test or "").strip()
    if not TEST_RX.match(test):
        raise ValueError("a test is a class name, optionally #method (e.g. MyTest#returns200)")
    cls = test.split("#")[0].split(".")[-1]
    found = glob.glob(os.path.join(root, "src", "test", "java", "**", cls + ".java"), recursive=True)
    if not found:
        raise ValueError(f"there is no {cls}.java under src/test/java")
    return test


# ---- Java and Maven -----------------------------------------------------------------

def _java_exe(home: str) -> str:
    exe = os.path.join(home, "bin", "java.exe" if os.name == "nt" else "java")
    return exe if os.path.isfile(exe) else ""


def java_version(home: str) -> int:
    """The major version of the JDK at `home` (its release file), 0 unknown."""
    try:
        with io.open(os.path.join(home, "release"), encoding="utf-8", errors="replace") as fh:
            m = re.search(r'JAVA_VERSION="(\d+)(?:\.(\d+))?', fh.read())
    except OSError:
        m = re.search(r"(?:jdk|openjdk|temurin|zulu)[-_]?(\d+)", os.path.basename(home).lower())
    if not m:
        return 0
    major = int(m.group(1))
    return int(m.group(2) or 0) if major == 1 else major


def jdk_candidates() -> list:
    """Installed JDK homes, from the usual places."""
    pats = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            for vendor in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "Amazon Corretto",
                           "BellSoft", "OpenJDK", "Semeru"):
                pats.append(os.path.join(base, vendor, "*"))
    else:
        pats += ["/usr/lib/jvm/*", "/Library/Java/JavaVirtualMachines/*/Contents/Home", "/opt/java/*"]
    pats.append(os.path.join(os.path.expanduser("~"), ".jdks", "*"))
    pats.append(os.path.join(os.path.expanduser("~"), ".sdkman", "candidates", "java", "*"))
    out = []
    for p in pats:
        for home in glob.glob(p):
            if os.path.isdir(home) and _java_exe(home) and os.path.isfile(os.path.join(home, "bin",
                                                                                      "javac.exe" if os.name == "nt" else "javac")):
                out.append(home)
    return sorted(set(out))


def saved_java_home() -> str:
    """JAVA_HOME as Windows has it saved (user, else system): what a console
    opened now would see. A process started earlier -- the workbench, the
    console or IDE that started it -- keeps the environment it was born
    with, so a JAVA_HOME set since then is not in os.environ. "" elsewhere."""
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    for hive, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, key) as k:
                value, _kind = winreg.QueryValueEx(k, "JAVA_HOME")
        except OSError:
            continue
        value = winreg.ExpandEnvironmentStrings(str(value or "")).strip().strip('"')
        if value:
            return value
    return ""


def java_env(env: dict = None, candidates=None, saved=None) -> tuple:
    """(environment for Maven, note or ""). JAVA_HOME is kept when it
    holds a java; when this process has none, the one saved in Windows is
    used (saved_java_home); otherwise the oldest JDK >= 17 found is used.
    `saved`: where the saved value comes from -- the registry for this
    process's own environment, nothing for an environment passed in."""
    own = env is None
    env = dict(os.environ if own else env)
    home = env.get("JAVA_HOME", "").strip().strip('"')
    if not home:
        stored = (saved or (saved_java_home if own else (lambda: "")))()
        if stored:
            # Checked below like any JAVA_HOME; the PATH gets its bin too,
            # as a console opened now would have it.
            home = stored
            env["JAVA_HOME"] = stored
            if _java_exe(stored):
                env["PATH"] = os.path.join(stored, "bin") + os.pathsep + env.get("PATH", "")
    if home and _java_exe(home):
        v = java_version(home)
        if v and v < MIN_JAVA:
            note = f"JAVA_HOME is Java {v}; the project needs {MIN_JAVA} or later"
        else:
            return env, ""
    else:
        note = (f"JAVA_HOME ({home}) has no bin/java" if home else "JAVA_HOME is not set")
    found = [(java_version(h), h) for h in (jdk_candidates() if candidates is None else candidates)]
    good = sorted((v, h) for v, h in found if v >= MIN_JAVA)
    if not good:
        return env, note + f", and no JDK {MIN_JAVA}+ was found: install one, or set JAVA_HOME"
    v, h = good[0]
    env["JAVA_HOME"] = h
    env["PATH"] = os.path.join(h, "bin") + os.pathsep + env.get("PATH", "")
    return env, f"{note}: using Java {v} at {h} for this run (set JAVA_HOME to it to make that permanent)"


def maven(root: str):
    """The Maven to run: the project's wrapper, else mvn on PATH."""
    for name in (("mvnw.cmd", "mvnw") if os.name == "nt" else ("mvnw",)):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return p
    return shutil.which("mvn") or ""


def stream(root: str, argv: list, env: dict, timeout: float) -> tuple:
    """(rc, text). Lines worth seeing are said as they come."""
    lines, start = [], time.time()
    proc = subprocess.Popen(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env, **loop.NO_WINDOW)
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            lines.append(line)
            if any(w in line for w in SHOWN):
                say("    " + line[:300])
            if time.time() - start > timeout:
                proc.kill()
                lines.append(f"stopped after {timeout:.0f}s")
                say(f"FAIL stopped: it ran longer than {timeout / 60:.0f} minutes")
                break
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    return (proc.returncode if proc.returncode is not None else 124), "\n".join(lines)


def run_maven(root: str, args: list, timeout: float, runner=None) -> tuple:
    """(rc, text, "offline" | "online" | "") -- offline first."""
    if runner is not None:
        rc, text = runner(args)
        return rc, text, "offline"
    exe = maven(root)
    if not exe:
        say("FAIL Maven is not on PATH (and the project has no mvnw)")
        return 127, "", ""
    env, note = java_env()
    if note:
        say(f"NOTE {note}")
    rc, text = stream(root, [exe, "-o", "-B", *args], env, timeout)
    if rc != 0 and any(w in text.lower() for w in loop._NEEDS_NETWORK):
        say(" ..  offline was not enough (something is not in the local repository); running online")
        rc, text = stream(root, [exe, "-B", *args], env, timeout)
        return rc, text, "online"
    return rc, text, "offline"


# ---- the two commands ---------------------------------------------------------------

def _write(job_dir: str, result: dict) -> None:
    os.makedirs(job_dir, exist_ok=True)
    tmp = os.path.join(job_dir, RESULT + ".tmp")
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, os.path.join(job_dir, RESULT))


def compile_(job_dir: str, root: str, runner=None, clean: bool = False) -> tuple:
    if clean:
        removed = buildsteps.clean_build_outputs(root)
        say(" ..  clean build: removed " + (", ".join(removed) or "nothing (already clean)")
            + " -- uploads, the failure digest and Jira fetches in target/ are kept")
    diag.step("Compiling the project and its tests (mvn test-compile)")
    say(" ..  mvn test-compile (offline first)")
    started = time.time()
    rc, text, how = run_maven(root, ["test-compile", "-q"], COMPILE_TIMEOUT, runner)
    errors = loop.compile_errors(text) if rc else []
    for e in errors[:40]:
        say("    " + e[:300])
    return rc, {"compile": {"ok": rc == 0, "exit_code": rc, "how": how, "errors": errors[:200],
                            "seconds": round(time.time() - started, 1)}}


def allure_report(root: str, runner=None, run=None) -> dict:
    """`mvn allure:report` after a run, passed or not: target/allure-report,
    which the workbench serves on its own port. A report that cannot be
    built is said; the run's verdict stands. `run(args)` -> (rc, text, how)
    runs Maven (default: run_maven, offline first -- the Allure command
    line is downloaded once, online, the first time)."""
    run = run or (lambda args: run_maven(root, args, COMPILE_TIMEOUT, runner))
    diag.step("Building the Allure report (mvn allure:report)")
    say(" ..  mvn allure:report")
    started = time.time()
    rc, text, how = run(["allure:report", "-q"])
    ok = rc == 0 and (runner is not None or buildsteps.report_ready(root))
    seconds = round(time.time() - started, 1)
    if ok:
        say(f" ..  the Allure report is ready ({seconds:.0f}s): Open the Allure report")
        return {"ok": True, "how": how, "seconds": seconds}
    tail = [ln for ln in (text or "").strip().splitlines() if ln.strip()][-6:]
    say("WARN the Allure report was not built (the test result stands):")
    for line in tail:
        say("    " + line[:300])
    return {"ok": False, "how": how, "seconds": seconds,
            "why": " / ".join(tail)[:600] or f"mvn allure:report exited {rc}"}


def cmd_compile(job_dir: str, root: str, runner=None) -> int:
    rc, part = compile_(job_dir, root, runner)
    result = {"kind": "mvnrun", "what": "compile", "at": _now(), **part,
              "ok": rc == 0, "verdict": "compiled" if rc == 0 else "the compile failed"}
    _write(job_dir, result)
    say(("DONE: the project compiles" if rc == 0 else
         f"FAIL: the compile failed ({len(part['compile']['errors'])} error(s) listed above)"))
    return 0 if rc == 0 else 1


def cmd_test(job_dir: str, root: str, suite: str = "", test: str = "", group: str = "",
             env: str = "", compile_first: bool = False, runner=None) -> int:
    chosen = [w for w in (suite, test, group) if str(w or "").strip()]
    if len(chosen) != 1:
        say("FAIL choose one thing to run: a suite, a test class, or a group")
        return 1
    try:
        if suite:
            suite = check_suite(root, suite)
        if test:
            test = check_test(root, test)
        if group and not GROUP_RX.match(group.strip()):
            raise ValueError("a group name is letters, digits, - and _")
        envs = list_envs(root)
        env = (env or envs["active"] or "stg").strip()
        if not ENV_RX.match(env):
            raise ValueError(f"{env!r} is not an environment name")
    except ValueError as e:
        say(f"FAIL {e}")
        return 1
    result = {"kind": "mvnrun", "what": "test", "at": _now(), "env": env,
              "suite": suite, "test": test, "group": group.strip()}
    if compile_first:
        rc, part = compile_(job_dir, root, runner, clean=True)
        result.update(part)
        if rc != 0:
            result.update({"ok": False, "verdict": "the compile failed: no test was run"})
            _write(job_dir, result)
            say("FAIL the compile failed: no test was run")
            return 1
    args = ["test", f"-Denv={env}", "-DfailIfNoTests=false", "-Dsurefire.failIfNoSpecifiedTests=false"]
    if suite:
        args.append(f"-DsuiteXmlFile={suite}")
        what = suite
    elif test:
        args.append(f"-Dtest={test}")
        what = f"the test {test}"
    else:
        rel = group_suite(root, group.strip())
        if not rel:
            say(f"FAIL the group {group.strip()} has no suite file ({loop.suitemap.suite_rel(group.strip())}): "
                f"it is written when a job's cases are first mapped to the group")
            return 1
        args.append(f"-DsuiteXmlFile={rel}")
        what = f"the group {group.strip()} ({rel})"
    results = os.path.join(root, RESULTS_XML)
    try:
        os.remove(results)                  # never read an earlier run's results as this one's
    except OSError:
        pass
    try:
        wb = _load("mvnrun_settings", os.path.join(HERE, "settings.py")).workbench_settings(root)
    except Exception:                                    # noqa: BLE001
        wb = {}
    timeout = 60 * int(wb.get("run_timeout_min") or loop.RUN_TIMEOUT // 60)
    diag.step(f"Running {what} against {env}")
    say(f" ..  running {what} against {env} (mvn test -Denv={env}); this can take minutes")
    started = time.time()
    rc, text, how = run_maven(root, args, timeout, runner)
    result.update({"exit_code": rc, "how": how, "seconds": round(time.time() - started, 1)})
    try:
        res = execution.read(results)
    except ValueError as e:
        errors = loop.compile_errors(text)
        why = ("it did not compile" if errors else f"the run left no test results ({e})")
        result.update({"ok": False, "verdict": why, "errors": errors[:200],
                       "tail": text.strip().splitlines()[-30:]})
        _write(job_dir, result)
        say(f"FAIL {why}. The end of its output:")
        for line in (errors[:20] or text.strip().splitlines()[-25:]):
            say("    " + line[:300])
        return 1
    ok, why = execution.verdict(res)
    result.update({"ok": ok, "verdict": why, "passed": res["passed"], "failed": res["failed"],
                   "skipped": res["skipped"], "switched_off": res["switched_off"],
                   "tests": res["tests"][:2000]})
    result["allure"] = allure_report(root, runner)
    _write(job_dir, result)
    say(f"{'PASSED' if ok else 'FAILED'}: {why} -- {res['passed']} passed, {res['failed']} failed, "
        f"{res['skipped']} skipped, in {result['seconds']:.0f}s")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile")
    c.add_argument("--job", required=True)
    t = sub.add_parser("test")
    t.add_argument("--job", required=True)
    t.add_argument("--suite", default="")
    t.add_argument("--test", default="")
    t.add_argument("--group", default="")
    t.add_argument("--env", default="")
    t.add_argument("--compile-first", action="store_true")
    a = ap.parse_args(argv)
    try:
        job_dir = loop.intake.job_dir(loop.intake.safe_job(a.job))
    except ValueError as e:
        say(f"FAIL {e}")
        return 1
    try:
        os.remove(os.path.join(job_dir, RESULT))
    except OSError:
        pass
    if a.cmd == "compile":
        return cmd_compile(job_dir, ROOT)
    return cmd_test(job_dir, ROOT, a.suite, a.test, a.group, a.env, a.compile_first)


if __name__ == "__main__":
    sys.exit(diag.run("mvnrun", main))
