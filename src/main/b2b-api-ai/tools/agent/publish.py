"""Put a job's results where the team works: a Jira issue, a Confluence page.

    python tools/agent/publish.py status --job J
    python tools/agent/publish.py attach --job J --key ABC-12 --file manual-tests.md --confirm ABC-12
    python tools/agent/publish.py page   --job J --what manual --space QA --parent 12345 \
                                         --title "Group booking: manual tests" --confirm "Group booking: manual tests"

Everything else in this workbench reads Jira and Confluence. These two
commands WRITE to them, and what is written is seen by other people and
cannot be taken back from here. So:

OFF UNLESS SWITCHED ON
----------------------
In the gitignored program_configuration.json:

    "jira_config":       { "publish": { "attach": true, "projects": ["ABC"] } }
    "confluence_config": { "publish": { "create_page": true, "spaces": ["QA"] } }

`attach` / `create_page` must be exactly `true`. `projects` may be left
out (any project the token can write to); when it is there it must be a
list of project keys -- a restriction that is mistyped is an error, not
"no restriction". `spaces` is required: a page is only ever created in a
space that is listed.

ONE THING PER COMMAND, SAID TWICE
---------------------------------
* `attach` adds ONE file to ONE issue. The key is given twice
  (`--confirm`). The file is one of this job's own results, chosen from a
  fixed list of names -- never a path. It adds; it never removes or
  replaces an attachment, and it refuses when a file of the same name is
  already on the issue.
* `page` creates ONE new page under ONE parent. The title is given twice.
  The parent must exist and be in the named space. It never edits a page:
  if the space already has a page with that title, it refuses.
* `--expect mock|real` is what the person was TOLD the target is. When
  the connection that would be used is the other one -- the switch in
  mock.json was changed after the page was drawn -- nothing is written.

WHAT IS SENT
------------
* Only a result: the file a failed step leaves behind is not one.
* A result made by a stand-in (mock), or not provably real, is not sent
  to a real Jira or Confluence.
* What is about to be sent is searched for the private configuration's
  values and hosts, and refused if any is in it. It is searched as it
  was BEFORE this tool escaped it: `tok_abc` is written `tok\\_abc` in a
  Markdown report and `a&b` is written `a&amp;b` on a page.
* A page is built here from the job's result file, every word of it
  escaped: nothing a model, a document or a ticket said becomes markup.
* The token goes only to the configured https address, in a header; a
  write is never followed through a redirect.

WHAT IS RECORDED
----------------
`publish.log` in the job directory gets a line BEFORE each write and one
after, and the result file says "attempted, outcome not known" from just
before the write until the answer is in -- so a write whose answer never
came, or a command that died, is still on record as possibly done.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
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


sys.path.insert(0, os.path.join(TOOLS, "jira"))
import search  # noqa: E402

loop = _load("publish_loop", os.path.join(HERE, "loop.py"))
mdsafe = _load("publish_mdsafe", os.path.join(HERE, "mdsafe.py"))
attachments = _load("publish_attachments", os.path.join(TOOLS, "confluence", "attachments.py"))
ROOT = loop.ROOT

RESULT = "publish-result.json"        # the last WRITE (or refusal of one)
STATUS = "publish-status.json"        # what is switched on; never replaces RESULT
LOG = "publish.log"
MAX_ATTACH_BYTES = 10 * 1024 * 1024
MAX_PAGE_CHARS = 400_000
_KEY_RX = re.compile(r"^[A-Z][A-Z0-9_]+-[0-9]+$")
_PROJECT_RX = re.compile(r"^[A-Z][A-Z0-9_]{1,19}$")
_SPACE_RX = re.compile(r"^[A-Za-z0-9~][A-Za-z0-9_.~-]{0,63}$")
_FILENAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

# A file that may be attached, what kind of result it belongs to, and how
# it is declared to Jira.
FILES = {
    "design.md": ("design", "text/markdown"),
    "test-cases.csv": ("design", "text/csv"),
    "xray.csv": ("design", "text/csv"),
    "manual-tests.md": ("manual", "text/markdown"),
    "manual-new-cases.csv": ("manual", "text/csv"),
    "manual-xray-updates.csv": ("manual", "text/csv"),
    "code-review.md": ("review", "text/markdown"),
}
# The result file each kind of content comes from.
PAGES = {"manual": "manual-result.json", "design": "design.json", "review": "code-review.json"}


def say(msg: str = "") -> None:
    print(msg, flush=True)


class Refused(RuntimeError):
    """This will not be done, and why. Nothing was written."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _log(job_dir: str, line: str) -> None:
    """One line in the record. Never the reason a command fails: what was
    written to Jira does not depend on whether this file could be."""
    line = re.sub(r"[\r\n]+", " ", line).encode("utf-8", "replace").decode("utf-8")
    try:
        with io.open(os.path.join(job_dir, LOG), "a", encoding="utf-8") as fh:
            fh.write(f"{_now()}  {line}\n")
    except OSError:
        pass


def _json(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _words(value, cap: int = 300) -> str:
    """Somebody else's text for a log line or a message: one line, no
    character that cannot be written down."""
    s = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return re.sub(r"[\ud800-\udfff]", "?", s)[:cap]


# ---- what is switched on ---------------------------------------------------

def _mock():
    return search.mock()


def mock_confluence() -> bool:
    try:
        return bool(_mock().on("confluence"))
    except RuntimeError as e:
        raise Refused(str(e)) from None


def _switch_block(cfg, name: str) -> dict:
    p = (cfg or {}).get("publish") if isinstance(cfg, dict) else None
    if p is None:
        return {}
    if not isinstance(p, dict):
        raise Refused(f"{name}.publish must be an object, like {{\"attach\": true}}")
    return p


def settings(jira_cfg: dict = None, conf_cfg: dict = None, jira_mock=None,
             conf_mock=None) -> dict:
    """{attach, projects, create_page, spaces}. Only a plain `true` is on.

    The stand-in's own settings (everything on) are used ONLY for a
    connection that is the stand-in's: `jira_mock` / `conf_mock` say which
    connection the caller holds.
    """
    jira_mock = search.mock_jira() if jira_mock is None else jira_mock
    conf_mock = mock_confluence() if conf_mock is None else conf_mock
    out = {"attach": False, "projects": [], "create_page": False, "spaces": []}
    if jira_mock:
        out.update(attach=True)                    # a file on this machine: try it
    else:
        if jira_cfg is None:
            jira_cfg, _ = search.projectconfig.section("jira_config")
        p = _switch_block(jira_cfg, "jira_config")
        out["attach"] = p.get("attach") is True
        if "projects" in p:
            # A restriction. Mistyped, it must not turn into "anywhere".
            listed = p["projects"]
            if (not isinstance(listed, list) or not listed
                    or not all(isinstance(x, str) and _PROJECT_RX.match(x.strip().upper())
                               for x in listed)):
                raise Refused("jira_config.publish.projects must be a list of project keys, "
                              "like [\"ABC\"]. Leave the setting out altogether to allow any "
                              "project the token can write to.")
            out["projects"] = [x.strip().upper() for x in listed]
    if conf_mock:
        out.update(create_page=True, spaces=list(_mock().CONFLUENCE_SPACES))
    else:
        if conf_cfg is None:
            conf_cfg, _ = search.projectconfig.section("confluence_config")
        p = _switch_block(conf_cfg, "confluence_config")
        out["create_page"] = p.get("create_page") is True
        listed = p.get("spaces")
        if listed is not None and (not isinstance(listed, list) or not all(
                isinstance(x, str) and _SPACE_RX.match(x.strip()) for x in listed)):
            raise Refused("confluence_config.publish.spaces must be a list of space keys, "
                          "like [\"QA\"]")
        out["spaces"] = [x.strip() for x in listed or []]
    return out


def expected(expect: str, mocked: bool, what: str) -> None:
    """What the person was told, against what is about to be used."""
    e = (expect or "").strip().lower()
    if not e:
        return
    if e not in ("mock", "real"):
        raise Refused("--expect is `mock` or `real`")
    if (e == "mock") != bool(mocked):
        raise Refused(
            f"the page said {what} here is {'a stand-in' if e == 'mock' else 'the real one'}, "
            f"and it is now {'a stand-in' if mocked else 'the REAL one'} (tools/agent/mock.json "
            f"was changed). Nothing was written. Press \"Check what can be published\" and "
            f"read what it says before trying again.")


def private_values(root: str) -> dict:
    """The values that must not leave. Refuses when they cannot be known."""
    path = os.path.join(root, "src", "main", "resources", "program_configuration.json")
    if os.path.isfile(path):
        try:
            with io.open(path, encoding="utf-8-sig") as fh:
                json.load(fh)
        except (OSError, ValueError):
            raise Refused("program_configuration.json cannot be read, so what is about to "
                          "be sent cannot be checked for private values. Fix the file.") from None
    return loop.config_secrets(path)


def _unescaped(text: str) -> str:
    """Text as it read before this project's writers escaped it: the
    backslash Markdown puts before punctuation, the zero-width space put
    in to stop a link, HTML entities, the doubled quote of a CSV cell."""
    t = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    t = re.sub(r"\\([\\`*_{}\[\]<>#|!~])", r"\1", t)
    t = html.unescape(t)
    return t.replace('""', '"')


def _strings(node, out: list) -> list:
    if isinstance(node, dict):
        for k, v in node.items():
            out.append(str(k))
            _strings(v, out)
    elif isinstance(node, list):
        for v in node:
            _strings(v, out)
    elif isinstance(node, str):
        out.append(node)
    return out


def leaks(texts, private: dict) -> list:
    """Which private values are in any of these texts, as written or as
    they were before being escaped. [] is "none found"."""
    if isinstance(texts, str):
        texts = [texts]
    hay = "\n".join(texts)
    low = (hay + "\n" + _unescaped(hay)).lower()
    squeezed = re.sub(r"\s+", " ", low)

    def forms(value: str) -> set:
        # As written; with runs of spaces made one (the reports do that);
        # and as mdsafe.code() writes a name (it swaps four characters).
        v = value.lower()
        out = {v, re.sub(r"\s+", " ", v)}
        coded = v
        for bad, safe in (("`", "'"), ("|", chr(0xA6)), ("<", chr(0x2039)), (">", chr(0x203A))):
            coded = coded.replace(bad, safe)
        out.add(coded)
        return out
    return sorted({why for value, why in private.items()
                   if len(value) >= 6 and any(f in low or f in squeezed for f in forms(value))})


def result_for(job_dir: str, what: str) -> dict:
    """The result a publication is made from, or Refused: it must be
    there, be a RESULT (not the error a failed step leaves in its place),
    and hold something."""
    name = PAGES[what]
    data = _json(os.path.join(job_dir, name))
    if not data:
        raise Refused(f"{name} is not there (or cannot be read): there is nothing to publish")
    if data.get("kind") == "error":
        raise Refused(f"the last run of this step failed ({_words(data.get('message'), 160)}); "
                      f"there is no result to publish")
    if what == "manual":
        ok = data.get("kind") == "manual-tests" and (
            isinstance(data.get("new_cases"), list) and isinstance(data.get("updates"), list)
            and (data["new_cases"] or data["updates"]))
    elif what == "review":
        ok = data.get("kind") == "code-review" and isinstance(data.get("findings"), list)
    else:
        ok = isinstance(data.get("test_cases"), list) and bool(data["test_cases"])
    if not ok:
        raise Refused(f"{name} does not hold a result that can be published")
    return data


def provably_real(data: dict, what: str) -> bool:
    """Only a result that SAYS it is real is. One that does not say --
    an older file, a hand-made one -- is treated as a stand-in's."""
    if data.get("mock") is not False:
        return False
    if what == "manual":
        inv = data.get("inventory")
        return isinstance(inv, dict) and inv.get("mock_jira") is False
    return True


# ---- sending --------------------------------------------------------------

class RedirectedWrite(Exception):
    """The server answered a write with a redirect. It was not followed --
    and so the server did not do what was asked: nothing was written."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A write is not followed anywhere: the body and the token would go
    with it, and "created somewhere else" is not what was asked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RedirectedWrite()


_REDIRECTED = ("answered with a redirect, which was not followed: nothing was written. An "
               "address that redirects -- to a sign-in page, to another host name -- is not "
               "the one to put in the configuration.")


def _opener():
    return urllib.request.build_opener(_NoRedirect())


def _send(req, timeout: int) -> tuple:
    """(status, body text). Raises OSError-family for "no answer"."""
    try:
        with _opener().open(req, timeout=timeout) as r:
            return r.status, r.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4000).decode("utf-8", "replace")
        except Exception:                                # noqa: BLE001
            diag.trace()
            body = ""
        return e.code, body


def https_only(base: str, what: str) -> None:
    u = urllib.parse.urlparse(base)
    if u.scheme != "https" or "@" in u.netloc or not u.netloc:
        raise Refused(f"the {what} address is not a plain https address. A write carries "
                      f"the token and what is written; it is not sent in clear, and an "
                      f"address with a user name in it is not used.")


def upload(url: str, token: str, timeout: int, filename: str, data: bytes, ctype: str):
    """POST one file as multipart/form-data. (status, parsed JSON or text)."""
    if not _FILENAME_RX.match(filename) or not re.fullmatch(r"[a-z]+/[a-z0-9.+-]+", ctype):
        raise ValueError("unusable file name or type")   # they go into a header
    boundary = "----workbench" + uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n").encode("utf-8")
    body = head + data + f"\r\n--{boundary}--\r\n".encode("ascii")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-Atlassian-Token", "no-check")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    status, text = _send(req, timeout)
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text[:300]


def conf_request(method: str, url: str, token: str, timeout: int, body=None):
    """(status, parsed JSON or text) from Confluence. A GET follows a
    redirect on the same host and scheme; a write follows none."""
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    if method == "GET":
        try:
            with attachments.fetch._opener(url).open(req, timeout=timeout) as r:
                status, text = r.status, r.read(4 * 1024 * 1024).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, text = e.code, ""
    else:
        status, text = _send(req, timeout)
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text[:300]


def _attempted(done: dict, on_attempt, target: str) -> None:
    """Said BEFORE the write: from here until the answer is in, the true
    state of things is "possibly done"."""
    if on_attempt:
        on_attempt(dict(done, ok=None, message=(
            f"A write to {target} was started and its outcome is not known (the command "
            f"did not finish). Look there before trying again.")))


# ---- Jira: attach one file to one issue -----------------------------------

def attach(job: str, key: str, name: str, confirm: str, root: str = "", conn=None,
           cfg: dict = None, transport=None, uploader=None, expect: str = "",
           on_attempt=None) -> dict:
    """Attach one of the job's result files to one issue."""
    root = root or ROOT
    job_dir = loop.intake.job_dir(job, root)
    key = (key or "").strip().upper()
    if not _KEY_RX.match(key):
        raise Refused(f"{key!r} is not an issue key like ABC-12")
    if (confirm or "").strip().upper() != key:
        raise Refused("the key must be given twice, the same: this writes to Jira")
    if name not in FILES:
        raise Refused(f"{name!r} is not a file that can be attached. It can be one of: "
                      f"{', '.join(sorted(FILES))}")
    conn = conn or search.connect()
    mocked = bool(conn.get("mock"))
    expected(expect, mocked, "Jira")
    if mocked and transport is None:
        # The stand-in's issue and the stand-in's attachments are kept
        # under the same root: what was "attached" is what is then found.
        transport = lambda m, u, t, s, b=None: _mock().jira_transport(m, u, t, s, b, root)
    opts = settings(jira_cfg=cfg, conf_cfg={}, jira_mock=mocked, conf_mock=False)
    if not opts["attach"]:
        raise Refused("attaching to Jira is switched off. To allow it, set "
                      "jira_config.publish.attach to true in program_configuration.json.")
    project = key.split("-")[0]
    if opts["projects"] and project not in opts["projects"]:
        raise Refused(f"project {project} is not in jira_config.publish.projects "
                      f"({', '.join(opts['projects'])})")
    if not mocked:
        https_only(conn["base"], "Jira")
    what = FILES[name][0]
    result = result_for(job_dir, what)
    path = os.path.join(job_dir, name)
    try:
        with io.open(path, "rb") as fh:
            data = fh.read(MAX_ATTACH_BYTES + 1)
    except OSError:
        raise Refused(f"this job has no {name} yet") from None
    if not data.strip():
        raise Refused(f"{name} is empty")
    if len(data) > MAX_ATTACH_BYTES:
        raise Refused(f"{name} is larger than {MAX_ATTACH_BYTES:,} bytes")
    if not mocked and not provably_real(result, what):
        raise Refused(f"{name} was made by a stand-in (mock), or from sample data, or does "
                      f"not say how it was made. It is not attached to a real issue. Run "
                      f"the step again for real first.")
    found = leaks(data.decode("utf-8", "replace"), private_values(root))
    if found:
        raise Refused(f"{name} contains private values and is not sent: "
                      + "; ".join(found[:4]))
    # The issue as Jira has it now: that it exists, can be read with this
    # token, and does not already carry this file.
    sent_as = f"{loop.intake.safe_job(job)}-{name}"
    issue = search._call(conn, "GET", f"/issue/{key}?fields=summary,attachment", None,
                         transport)
    fields = issue.get("fields") if isinstance(issue, dict) and isinstance(
        issue.get("fields"), dict) else {}
    if not isinstance(issue, dict) or str(issue.get("key") or "").upper() != key:
        raise Refused(f"Jira did not answer with {key} when asked for it; nothing was sent")
    have = [str(a.get("filename") or "") for a in fields.get("attachment") or []
            if isinstance(a, dict)] if isinstance(fields.get("attachment"), list) else []
    if sent_as.lower() in [h.lower() for h in have]:
        raise Refused(f"{key} already has an attachment called {sent_as}. This adds and "
                      f"never replaces: remove that one in Jira, or use another job name.")
    done = {"kind": "publish", "what": "attach", "key": key, "file": name, "sent_as": sent_as,
            "bytes": len(data), "summary": _words(fields.get("summary"), 200), "mock": mocked,
            "ok": None, "at": _now()}
    _log(job_dir, f"ATTEMPT attach {sent_as} ({len(data)} bytes) to {key} "
                  f"{'(MOCK)' if mocked else 'at ' + conn['base']}")
    _attempted(done, on_attempt, key)
    try:
        if mocked:
            _mock().jira_attach(root, key, sent_as, len(data))
            status, answer = 200, [{"filename": sent_as}]
        else:
            status, answer = (uploader or upload)(
                f"{conn['base']}{conn['api']}/issue/{key}/attachments", conn["token"],
                conn["timeout"], sent_as, data, FILES[name][1])
    except RedirectedWrite:
        _log(job_dir, f"REDIRECTED attach {sent_as} to {key}: not followed, nothing written")
        done.update(ok=False, message=f"Jira {_REDIRECTED}")
        return done
    except Exception as e:                               # noqa: BLE001
        diag.trace()
        # No answer. It may have arrived: say that, not "failed".
        _log(job_dir, f"NO ANSWER attach {sent_as} to {key} ({type(e).__name__})")
        done["message"] = (f"Jira did not answer ({type(e).__name__}). The file MAY have "
                           f"been attached to {key}: look at the issue before trying again.")
        return done
    if 200 <= status < 300 and isinstance(answer, list):
        _log(job_dir, f"DONE attach {sent_as} to {key} (HTTP {status})")
        done.update(ok=True, message=f"{sent_as} is attached to {key}"
                                     + (" (MOCK: a file on this machine was changed; no Jira "
                                        "was contacted)" if mocked else "") + ".")
    elif 400 <= status < 500:
        _log(job_dir, f"REFUSED BY JIRA attach {sent_as} to {key} (HTTP {status})")
        done.update(ok=False, message=f"Jira refused (HTTP {status}): nothing was attached. "
                                      + ("The token may not attach to this issue."
                                         if status in (401, 403) else ""))
    else:
        _log(job_dir, f"UNCLEAR attach {sent_as} to {key} (HTTP {status})")
        done["message"] = (f"Jira answered HTTP {status}. The file MAY have been attached "
                           f"to {key}: look at the issue before trying again.")
    return done


# ---- Confluence: create one page -----------------------------------------

def _cell(text) -> str:
    return mdsafe.xhtml(text)


def _rows(value) -> list:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _table(headers: list, rows: list) -> str:
    out = ["<table><tbody><tr>" + "".join(f"<th>{_cell(h)}</th>" for h in headers) + "</tr>"]
    for row in rows:
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>")
    return "".join(out) + "</tbody></table>"


def _steps(steps) -> str:
    items = []
    for s in _rows(steps):
        line = _cell(s.get("action"))
        if s.get("data"):
            line += f" <em>[{_cell(s.get('data'))}]</em>"
        if s.get("expected"):
            line += f"<br/>&#8594; {_cell(s.get('expected'))}"
        items.append(f"<li>{line}</li>")
    return f"<ol>{''.join(items)}</ol>" if items else ""


def page_body(what: str, data: dict, job: str) -> str:
    """The page, in Confluence's storage format, from a result file. Every
    value is escaped; the only markup is what is written here."""
    out = [f"<p><em>Written by the test workbench from job {_cell(job)}, "
           f"{_cell(data.get('at') or _now())}. Review before relying on it.</em></p>"]
    if data.get("mock") is not False:
        out.append("<p><strong>MOCK. This content was made up by a stand-in. It is not a "
                   "result.</strong></p>")
    if what == "manual":
        cases = _rows(data.get("new_cases"))
        out.append(f"<h2>New test cases ({len(cases)})</h2>")
        out.append(_table(["ID", "Title", "Module", "Priority", "Preconditions", "Steps",
                           "Source"],
                          [[_cell(c.get("id")), _cell(c.get("title")), _cell(c.get("module")),
                            _cell(c.get("priority")), _cell(c.get("preconditions")),
                            _steps(c.get("steps")), _cell(c.get("source"))] for c in cases]))
        ups = _rows(data.get("updates"))
        out.append(f"<h2>Existing tests to update ({len(ups)})</h2>")
        if ups:
            out.append(_table(["Test", "Existing title", "Why", "Change"],
                              [[_cell(u.get("key")), _cell(u.get("existing_title")),
                                _cell(u.get("reason")), _cell(u.get("changes"))] for u in ups]))
    elif what == "design":
        cases = _rows(data.get("test_cases"))
        out.append(f"<h2>API test cases ({len(cases)})</h2>")
        out.append(_table(["ID", "Title", "Endpoint", "Steps"],
                          [[_cell(c.get("id")), _cell(c.get("title")), _cell(c.get("endpoint")),
                            _steps(c.get("steps"))] for c in cases]))
    else:
        finds = _rows(data.get("findings"))
        out.append(f"<p>{_cell(data.get('message'))}</p>")
        out.append(f"<h2>Findings ({len(finds)})</h2>")
        if finds:
            out.append(_table(["Severity", "Where", "What", "Why", "Suggested change"],
                              [[_cell(str(f.get("severity") or "").upper()),
                                _cell(f"{f.get('file') or '?'}:{f.get('line')}" if f.get("line")
                                      else (f.get("file") or "")),
                                _cell(f.get("title")), _cell(f.get("why")), _cell(f.get("fix"))]
                               for f in finds]))
    return "".join(out)


def create_page(job: str, what: str, space: str, parent: str, title: str, confirm: str,
                root: str = "", cfg: dict = None, send=None, mocked=None, expect: str = "",
                on_attempt=None) -> dict:
    """Create one new Confluence page from one of the job's results."""
    root = root or ROOT
    job_dir = loop.intake.job_dir(job, root)
    if what not in PAGES:
        raise Refused(f"unknown content {what!r}. Known: {', '.join(PAGES)}")
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not title or len(title) > 200 or re.search(r"[\x00-\x1f\x7f\ud800-\udfff]", title):
        raise Refused("the page needs a title of 1 to 200 characters")
    if re.sub(r"\s+", " ", str(confirm or "")).strip() != title:
        raise Refused("the title must be given twice, the same: this writes to Confluence")
    space = (space or "").strip()
    if not _SPACE_RX.match(space):
        raise Refused(f"{space!r} is not a space key")
    parent = (parent or "").strip()
    if not re.fullmatch(r"[0-9]{1,19}", parent):
        raise Refused("the parent page is given by its numeric id (the pageId in its address)")
    mocked = mock_confluence() if mocked is None else mocked
    expected(expect, mocked, "Confluence")
    opts = settings(jira_cfg={}, conf_cfg=cfg, jira_mock=False, conf_mock=mocked)
    if not opts["create_page"]:
        raise Refused("creating Confluence pages is switched off. To allow it, set "
                      "confluence_config.publish.create_page to true, and list the spaces "
                      "in confluence_config.publish.spaces.")
    if space not in opts["spaces"]:
        raise Refused(f"space {space} is not in confluence_config.publish.spaces "
                      f"({', '.join(opts['spaces']) or 'none are listed'})")
    data = result_for(job_dir, what)
    if not mocked and not provably_real(data, what):
        raise Refused(f"{PAGES[what]} was made by a stand-in (mock), or from sample data, or "
                      f"does not say how it was made. It is not published to a real "
                      f"Confluence.")
    body = page_body(what, data, loop.intake.safe_job(job))
    if len(body) > MAX_PAGE_CHARS:
        raise Refused(f"the page would be {len(body):,} characters; the limit is "
                      f"{MAX_PAGE_CHARS:,}")
    # Searched as the words were BEFORE they were escaped for the page.
    found = leaks(_strings(data, [title]), private_values(root))
    if found:
        raise Refused("the page contains private values and is not sent: "
                      + "; ".join(found[:4]))
    if mocked:
        base, api, token, timeout = "https://mock-confluence.invalid", "/rest/api/content", "", 5
        send = send or (lambda m, u, t, s, b=None: _mock().confluence(m, u, b, root))
    else:
        try:
            conn = attachments.connect(parent, cfg)
        except attachments.Refused as e:
            raise Refused(str(e)) from None
        base, api, token, timeout = conn["base"], conn["api"], conn["token"], conn["timeout"]
        https_only(base, "Confluence")
        send = send or conf_request

    def ask(url: str) -> tuple:
        # A read that fails is a refusal: nothing has been written yet.
        try:
            return send("GET", url, token, timeout)
        except Exception as e:                           # noqa: BLE001
            diag.trace()
            raise Refused(f"Confluence could not be asked ({type(e).__name__}); nothing "
                          f"was created") from None
    # The parent, as Confluence has it now, and that no page has this title.
    status, got = ask(f"{base}{api}/{parent}?expand=space")
    if status != 200 or not isinstance(got, dict) or str(got.get("id")) != parent:
        raise Refused(f"the parent page {parent} could not be read (HTTP {status}); "
                      f"nothing was created")
    in_space = str(got["space"].get("key") or "") if isinstance(got.get("space"), dict) else ""
    if in_space != space:
        raise Refused(f"the parent page {parent} is in space {_words(in_space, 40) or '?'}, "
                      f"not {space}")
    q = urllib.parse.urlencode({"spaceKey": space, "title": title})
    status, got = ask(f"{base}{api}?{q}")
    if status != 200 or not isinstance(got, dict) or not isinstance(got.get("results"), list):
        raise Refused(f"whether {space} already has a page with this title could not be "
                      f"checked (HTTP {status}); nothing was created")
    if got["results"]:
        raise Refused(f"space {space} already has a page titled {title!r}. This creates and "
                      f"never edits: choose another title.")
    done = {"kind": "publish", "what": "page", "space": space, "parent": parent,
            "title": title, "content": what, "mock": mocked, "ok": None, "at": _now()}
    _log(job_dir, f"ATTEMPT create page {title!r} in {space} under {parent} "
                  f"({len(body)} characters) {'(MOCK)' if mocked else 'at ' + base}")
    _attempted(done, on_attempt, f"Confluence space {space}")
    try:
        status, got = send("POST", f"{base}{api}", token, timeout, {
            "type": "page", "title": title, "space": {"key": space},
            "ancestors": [{"id": parent}],
            "body": {"storage": {"value": body, "representation": "storage"}}})
    except RedirectedWrite:
        _log(job_dir, f"REDIRECTED create page {title!r}: not followed, nothing written")
        done.update(ok=False, message=f"Confluence {_REDIRECTED}")
        return done
    except Exception as e:                               # noqa: BLE001
        diag.trace()
        _log(job_dir, f"NO ANSWER create page {title!r} ({type(e).__name__})")
        done["message"] = (f"Confluence did not answer ({type(e).__name__}). The page MAY "
                           f"have been created: look in {space} before trying again.")
        return done
    if 200 <= status < 300 and isinstance(got, dict) and got.get("id"):
        link = ""
        links = got.get("_links") if isinstance(got.get("_links"), dict) else {}
        if isinstance(links.get("webui"), str) and re.fullmatch(r"/[\x21-\x7e]{0,400}",
                                                                 links["webui"]):
            link = base + links["webui"]
        _log(job_dir, f"DONE create page {title!r} in {space}: id {_words(got.get('id'), 30)}")
        done.update(ok=True, id=_words(got.get("id"), 30), link=link,
                    message=f"The page {title!r} was created in {space}"
                            + (" (MOCK: a file on this machine was changed; no Confluence "
                               "was contacted)" if mocked else "") + ".")
    elif 400 <= status < 500:
        _log(job_dir, f"REFUSED BY CONFLUENCE create page {title!r} (HTTP {status})")
        done.update(ok=False, message=f"Confluence refused (HTTP {status}): no page was "
                                      f"created.")
    else:
        _log(job_dir, f"UNCLEAR create page {title!r} (HTTP {status})")
        done["message"] = (f"Confluence answered HTTP {status}. The page MAY have been "
                           f"created: look in {space} before trying again.")
    return done


def status(job: str, root: str = "") -> dict:
    """What can be published from this job, and what is switched on NOW."""
    root = root or ROOT
    job_dir = loop.intake.job_dir(job, root)
    opts, problem = {"attach": False, "projects": [], "create_page": False, "spaces": []}, ""
    try:
        mock_j, mock_c = bool(search.mock_jira()), mock_confluence()
    except (Refused, RuntimeError) as e:
        diag.trace()
        mock_j, mock_c, problem = True, True, str(e)
    if not problem:
        try:
            opts = settings(jira_mock=mock_j, conf_mock=mock_c)
        except (Refused, search.Refused, RuntimeError) as e:
            diag.trace()
            problem = str(e)
    usable = []
    for what in PAGES:
        try:
            result_for(job_dir, what)
            usable.append(what)
        except Refused:
            pass
    return {"kind": "publish-status", "at": _now(), "job": job, "problem": problem,
            "attach": opts["attach"], "projects": opts["projects"],
            "create_page": opts["create_page"], "spaces": opts["spaces"],
            "mock_jira": mock_j, "mock_confluence": mock_c,
            "files": [n for n in sorted(FILES) if FILES[n][0] in usable
                      and os.path.isfile(os.path.join(job_dir, n))],
            "pages": usable}


# ---- command --------------------------------------------------------------

def main(argv=None, **replace) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="what is switched on, and what this job has")
    p.add_argument("--job", required=True)
    p = sub.add_parser("attach", help="attach one result file to one Jira issue")
    p.add_argument("--job", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--confirm", "--confirm-key", dest="confirm", required=True)
    p.add_argument("--expect", default="")
    p = sub.add_parser("page", help="create one Confluence page from a result")
    p.add_argument("--job", required=True)
    p.add_argument("--what", required=True)
    p.add_argument("--space", required=True)
    p.add_argument("--parent", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--confirm", "--confirm-title", dest="confirm", required=True)
    p.add_argument("--expect", default="")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        job_dir = loop.intake.job_dir(a.job)
    except ValueError as e:
        say(f"REFUSED {e}")
        return 2
    if not os.path.isdir(job_dir):
        say(f"REFUSED there is no job {a.job!r}")
        return 2
    mine = STATUS if a.cmd == "status" else RESULT

    def tell(payload: dict) -> None:
        # ASCII only, and no error this can raise is one the command dies
        # of: by now something may have been written to Jira.
        try:
            _write(os.path.join(job_dir, mine), json.dumps(payload, ensure_ascii=True))
        except (OSError, ValueError):
            pass
    attempted = []

    def on_attempt(payload: dict) -> None:
        attempted.append(payload)
        tell(payload)
    try:
        if a.cmd == "status":
            r = status(a.job)
            tell(r)
            say(json.dumps({k: r[k] for k in ("attach", "create_page", "spaces", "files",
                                              "pages", "mock_jira", "mock_confluence")},
                           indent=2))
            return 0
        if a.cmd == "attach":
            r = attach(a.job, a.key, a.file, a.confirm, expect=a.expect,
                       on_attempt=on_attempt, **replace)
        else:
            r = create_page(a.job, a.what, a.space, a.parent, a.title, a.confirm,
                            expect=a.expect, on_attempt=on_attempt, **replace)
    except (Refused, search.Refused) as e:
        say(f"REFUSED {e}")
        tell({"kind": "error", "refused": True, "message": str(e), "at": _now()})
        return 2
    except search.JiraError as e:
        say(f"FAIL {e}")
        tell({"kind": "error", "refused": False, "at": _now(),
              "message": f"{_words(e, 300)} Nothing was written."})
        return 1
    except Exception as e:                               # noqa: BLE001
        diag.trace()
        say(f"FAIL unexpected {type(e).__name__} in publish.py "
            f"(line {e.__traceback__.tb_lineno if e.__traceback__ else '?'})")
        if not attempted:
            tell({"kind": "error", "refused": False, "at": _now(),
                  "message": f"unexpected {type(e).__name__} before anything was sent. "
                             f"Nothing was written."})
        # After an attempt, the "outcome not known" already written stands.
        return 1
    tell(r)
    say({True: " ok   ", False: "FAIL  ", None: "UNCLEAR "}[r["ok"]]
        + ("MOCK " if r["mock"] else "") + r["message"])
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(diag.run("publish", main))
