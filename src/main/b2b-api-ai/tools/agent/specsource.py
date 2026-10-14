"""Where a job's API specification comes from, when it is not pasted.

    python tools/agent/specsource.py confluence --job J --link <page link>
    python tools/agent/specsource.py confluence --job J --link <page link> --name openapi.yaml
    python tools/agent/specsource.py show --job J

"Design the tests" reads an OpenAPI / Swagger document. It can be pasted.
It can also be a FILE: one chosen on the page, or one attached to a
Confluence page. Either way it ends up in one place,

    .workbench/<job>/spec/<its own name>

and the design step reads it from there. The page never names a path: it
sends a file, or a link, and is told where the file was put.

WHAT IS CHECKED
---------------
* The name is the file's own name and nothing of where it came from; it
  must end in .json, .yaml or .yml.
* The content must BE a specification -- JSON or YAML with a `paths`
  section -- or it is not kept. A file kept is a file the design step
  will use without asking again.
* One specification per job. A new one replaces the old one; a failed
  attempt leaves the old one as it was.
* From Confluence: the page must be on an approved host, the attachment
  is read only from that host (tools/confluence/attachments.py), and when
  a page has several attachments that are specifications, none is chosen
  for you -- they are listed, and you name one.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def _diag():
    """tools/diag.py, once for the whole process: the record of what this
    command did, kept for finding out what went wrong."""
    import importlib.util as _ilu
    if "workbench_diag" not in sys.modules:
        _spec = _ilu.spec_from_file_location(
            "workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
        _mod = _ilu.module_from_spec(_spec)
        sys.modules["workbench_diag"] = _mod
        try:
            _spec.loader.exec_module(_mod)
        except BaseException:
            sys.modules.pop("workbench_diag", None)
            raise
    return sys.modules["workbench_diag"]


diag = _diag()
TOOLS = os.path.dirname(HERE)


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


design = _load("specsource_design", os.path.join(HERE, "design.py"))
loop = design.loop
ROOT = loop.ROOT

RESULT = "spec-result.json"
MAX_SPEC_BYTES = 20 * 1024 * 1024
MAX_CANDIDATES = 8            # attachments read to see which is a specification
_NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,100}\.(json|ya?ml)$", re.I)
_EXT_RX = re.compile(r"\.(json|ya?ml)$", re.I)
_STORE_LOCK = threading.Lock()
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def say(msg: str = "") -> None:
    print(msg, flush=True)


class Refused(RuntimeError):
    """It is not kept, and why."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def spec_name(name: str) -> str:
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if (not _NAME_RX.match(base) or ".." in base
            or base.rsplit(".", 1)[0].strip(" .").lower() in _RESERVED):
        raise Refused(f"{base or name!r} cannot be used as a file name here. A specification "
                      f"file ends in .json, .yaml or .yml, and its name may hold letters, "
                      f"digits, spaces and . _ - ( ) + only.")
    return base


def check(data: bytes, name: str) -> tuple:
    """(spec, how) for bytes that are a specification, or Refused."""
    if not data:
        raise Refused(f"{name} is empty")
    if len(data) > MAX_SPEC_BYTES:
        raise Refused(f"{name} is {len(data):,} bytes; the limit is {MAX_SPEC_BYTES:,}")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Refused(f"{name} is not text (it is not UTF-8)") from None
    spec, how = design.read_spec(text)
    if spec is None:
        raise Refused(f"{name} is not an API specification: {how}")
    if not design.endpoints_of(spec):
        raise Refused(f"{name} has a `paths` section with no endpoint in it")
    return spec, how


def current(job_dir: str, root: str = "") -> dict:
    """What is stored for the job: {name, path, bytes, endpoints, format,
    origin, at}, or {} when nothing is."""
    # From the record written when it was kept: reading a large YAML
    # again every time the page asks is tens of seconds.
    d = os.path.join(job_dir, design.SPEC_DIR)
    try:
        with io.open(os.path.join(d, design.SPEC_META), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return {}
    name = str(meta.get("name") or "") if isinstance(meta, dict) else ""
    if (not _NAME_RX.match(name) or name != os.path.basename(name)
            or not os.path.isfile(os.path.join(d, name))):
        return {}
    rel = os.path.relpath(os.path.join(d, name), root or ROOT)
    try:
        endpoints = int(meta.get("endpoints") or 0)
    except (TypeError, ValueError):
        endpoints = 0
    return {"name": name, "path": rel.replace(os.sep, "/"),
            "bytes": os.path.getsize(os.path.join(d, name)),
            "endpoints": endpoints, "format": str(meta.get("format") or ""),
            "origin": str(meta.get("origin") or ""), "at": str(meta.get("at") or "")}


def store(job_dir: str, name: str, data: bytes, origin: str, root: str = "") -> dict:
    """Keep `data` as the job's specification. Checked first: a refusal
    leaves whatever was stored before exactly as it was."""
    base = spec_name(name)
    spec, how = check(data, base)
    d = os.path.join(job_dir, design.SPEC_DIR)
    fresh = old = aside = ""
    with _STORE_LOCK:
        try:
            os.makedirs(job_dir, exist_ok=True)
            fresh = tempfile.mkdtemp(prefix="spec.new.", dir=job_dir)
            with io.open(os.path.join(fresh, base), "wb") as fh:
                fh.write(data)
            with io.open(os.path.join(fresh, design.SPEC_META), "w", encoding="utf-8") as fh:
                json.dump({"name": base, "origin": origin, "at": _now(), "format": how,
                           "endpoints": len(design.endpoints_of(spec))}, fh)
            # The old one is moved aside, not removed, until the new one
            # is in its place: whatever fails, one of the two is there.
            if os.path.isdir(d):
                old = tempfile.mkdtemp(prefix="spec.old.", dir=job_dir)
                os.rmdir(old)
                os.replace(d, old)
            try:
                os.replace(fresh, d)
            except OSError:
                if old:
                    try:
                        os.replace(old, d)
                    except OSError:
                        aside = os.path.basename(old)    # left where it is; NOT removed
                    old = ""
                raise
        except OSError as e:
            # Not the message: it carries this machine's full path.
            if aside:
                raise Refused(f"{base} could not be kept ({type(e).__name__}), and the "
                              f"specification kept before could not be put back: it is in "
                              f"the job directory as {aside}. Nothing is in use until one "
                              f"is chosen again.") from None
            raise Refused(f"{base} could not be kept ({type(e).__name__}). If the "
                          f"specification kept before is open in another program, close "
                          f"it and try again; it is still the one in use.") from None
        finally:
            for leftover in (fresh, old):
                if leftover and os.path.isdir(leftover):
                    shutil.rmtree(leftover, ignore_errors=True)
    got = current(job_dir, root)
    if got.get("name") != base:
        raise Refused(f"{base} was written but cannot be read back; nothing is kept")
    return got


def clear(job_dir: str) -> bool:
    d = os.path.join(job_dir, design.SPEC_DIR)
    had = os.path.isdir(d)
    with _STORE_LOCK:
        # The record first: without it nothing in the directory is used,
        # even if a file in it is open somewhere and cannot be removed.
        try:
            os.remove(os.path.join(d, design.SPEC_META))
        except OSError:
            pass
        shutil.rmtree(d, ignore_errors=True)
    return had


# ---- from a Confluence page ----------------------------------------------

def _attachments():
    return _load("specsource_attachments",
                 os.path.join(TOOLS, "confluence", "attachments.py"))


def from_confluence(job_dir: str, link: str, name: str = "", conf=None, conn=None,
                    lister=None, reader=None, root: str = "") -> dict:
    """Find the specification among a page's attachments and keep it.

    Returns the result for the page: {ok, message, spec, candidates}.
    `conn`, `lister`, `reader` replace Confluence in the tests.
    """
    conf = conf or _attachments()
    try:
        conn = conn or conf.connect(link)
        diag.step("Asking Confluence which files are attached to the page")
        rows = lister(conn) if lister else conf.list_attachments(conn, conf.page_id(conn))
    except conf.Refused as e:
        raise Refused(str(e)) from None
    reader = reader or (lambda att: conf.read(conn, att, MAX_SPEC_BYTES))
    more = bool(isinstance(conn, dict) and conn.get("more"))
    named = [r for r in rows if _NAME_RX.match(r["title"])]
    # Has the extension, but a name this cannot keep a file under.
    odd = [r["title"] for r in rows
           if _EXT_RX.search(r["title"]) and not _NAME_RX.match(r["title"])]
    others = [r["title"] for r in rows if not _EXT_RX.search(r["title"])]
    listed = [{"title": r["title"], "bytes": r["bytes"]} for r in named]
    if not named:
        if odd:
            raise Refused(
                f"the page's attachment(s) {', '.join(odd[:6])} end in .json, .yaml or .yml "
                f"but have a name that cannot be used for a file here (letters, digits, "
                f"spaces and . _ - ( ) + only, up to 100 characters). Download it, rename "
                f"it and use *From a file*.")
        raise Refused(
            "this page has no attachment whose name ends in .json, .yaml or .yml"
            + (f" (it has: {', '.join(others[:8])}{' ...' if len(others) > 8 else ''})"
               if others else " (it has no attachments at all)")
            + (" among the first ones listed; it has more" if more else "")
            + ". A specification written ON the page, in a code block, is found by "
              "putting the link in the links box above instead.")
    want = (name or "").strip()
    if want:
        pick = [r for r in named if r["title"] == want]
        if not pick:
            raise Refused(f"the page has no attachment called {want!r}. It has: "
                          f"{', '.join(r['title'] for r in named)}")
        if len(pick) > 1:
            raise Refused(f"the page has {len(pick)} attachments called {want!r}")
        try:
            diag.step("Downloading the attachment")
            data = reader(pick[0])
        except conf.Refused as e:
            raise Refused(str(e)) from None
        spec = store(job_dir, pick[0]["title"], data, f"Confluence: {link}", root)
        return {"ok": True, "spec": spec, "candidates": listed,
                "message": f"{spec['name']} was taken from the page: "
                           f"{spec['endpoints']} endpoint(s)."}
    # Three outcomes for each: it is one, it is not one, or it could not
    # be told (not read, or YAML with no PyYAML here). "The only one that
    # fits" is true only when every other one was TOLD not to be.
    good, bad, unknown = [], [], []
    for r in named[:MAX_CANDIDATES]:
        try:
            diag.step("Downloading the attachment")
            data = reader(r)
        except conf.Refused as e:
            unknown.append(f"{r['title']}: {e}")
            continue
        try:
            spec, _how = check(data, r["title"])
            good.append((r, data, len(design.endpoints_of(spec))))
        except Refused as e:
            (unknown if "PyYAML is not installed" in str(e) else bad).append(
                f"{r['title']}: {e}")
    for row in listed:
        hit = [n for r, _d, n in good if r["title"] == row["title"]]
        row["endpoints"] = hit[0] if hit else None
        row["note"] = ("could not be told" if any(u.startswith(row["title"] + ": ")
                                                  for u in unknown) else "")
    for row in listed[MAX_CANDIDATES:]:
        row["note"] = "not looked at"
    skipped = max(0, len(named) - MAX_CANDIDATES)
    unseen = skipped + len(unknown) + len(odd) + (1 if more else 0)
    if len(good) == 1 and not unseen:
        r, data, _n = good[0]
        spec = store(job_dir, r["title"], data, f"Confluence: {link}", root)
        return {"ok": True, "spec": spec, "candidates": listed,
                "message": f"{spec['name']} was taken from the page: "
                           f"{spec['endpoints']} endpoint(s)."}
    if not good and not unseen:
        raise Refused("none of the page's attachments is an API specification -- "
                      + "; ".join(bad[:4]))
    # Several fit, or some could not be judged. Which one is the service
    # under test is not something to guess: the wrong one designs tests
    # for another API.
    why = []
    if unknown:
        why.append(f"{len(unknown)} could not be told ({'; '.join(unknown[:3])})")
    if skipped:
        why.append(f"{skipped} more were not looked at")
    if odd:
        why.append(f"{len(odd)} with a name that cannot be used here ({', '.join(odd[:3])})")
    if more:
        why.append("the page has more attachments than were listed")
    return {"ok": False, "spec": current(job_dir, root), "candidates": listed,
            "message": (f"{len(good)} of this page's attachments "
                        f"{'is a specification' if len(good) == 1 else 'are specifications'}"
                        + (f"; {'; '.join(why)}" if why else "")
                        + ". Nothing was taken. "
                        + ("Put the name of the one you mean in the attachment box and "
                           "fetch again." if good or unknown or skipped else
                           "Download the file, rename it and use From a file."))}


# ---- command --------------------------------------------------------------

def _write_result(job_dir: str, payload: dict) -> None:
    path = os.path.join(job_dir, RESULT)
    tmp = f"{path}.{os.getpid()}.tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dict(payload, at=_now()), fh, ensure_ascii=False)
    os.replace(tmp, path)


def main(argv=None, **replace) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("confluence", help="take the specification attached to a page")
    p.add_argument("--job", required=True)
    p.add_argument("--link", required=True)
    p.add_argument("--name", default="")
    p = sub.add_parser("show", help="what is stored for the job")
    p.add_argument("--job", required=True)
    a = ap.parse_args(argv)
    try:
        job_dir = loop.intake.job_dir(a.job)
    except ValueError as e:
        say(f"REFUSED {e}")
        return 2
    if a.cmd == "show":
        say(json.dumps(current(job_dir) or {"none": True}, indent=2))
        return 0
    os.makedirs(job_dir, exist_ok=True)
    try:
        os.remove(os.path.join(job_dir, RESULT))
    except OSError:
        pass
    try:
        out = from_confluence(job_dir, a.link.strip(), a.name, **replace)
    except Refused as e:
        say(f"REFUSED {e}")
        _write_result(job_dir, {"kind": "error", "refused": True, "message": str(e)})
        return 2
    except Exception as e:                               # noqa: BLE001
        diag.trace()
        say(f"FAIL unexpected {type(e).__name__} in specsource.py "
            f"(line {e.__traceback__.tb_lineno if e.__traceback__ else '?'})")
        _write_result(job_dir, {"kind": "error", "refused": False,
                                "message": f"unexpected {type(e).__name__}; see the log"})
        return 1
    say((" ok   " if out["ok"] else "WAIT  ") + out["message"])
    for c in out["candidates"]:
        say(f"  {c['title']}  ({c['bytes']:,} bytes"
            + (f", {c['endpoints']} endpoint(s))" if c.get("endpoints") is not None else ")"))
    _write_result(job_dir, dict(out, kind="spec"))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(diag.run("specsource", main))
