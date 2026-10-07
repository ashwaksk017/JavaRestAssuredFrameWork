"""Whatever was pasted, turned into one brief a human can argue with.

    python tools/agent/intake.py --job j1 --text-file story.txt
    python tools/agent/intake.py --job j1 --text "POST /props/ABC/groups ..."
    python tools/agent/intake.py --job j1 --link https://wiki/x/AbCdEf --link ABC-123

Stage 2 of the story-to-test plan. The UI will call this with one blob
of pasted text and a list of links; the CLI exists so the same thing is
reproducible without the UI, which is how every other stage here works.

WHAT IT PRODUCES
----------------
    target/agent/<job>/brief.md        the readable summary
    target/agent/<job>/intake.json     the same thing, structured
    target/agent/<job>/sources/        one snapshot per link

WHAT IT REFUSES
---------------
No request could be read, from the pasted text or from any source. The
brief is still written -- with the sources listed and the reason on line
one -- because "I read these four things and found no request in them"
is a useful answer, and a silent empty result is not.

It does NOT invent a request from prose that merely describes one. The
extractor reads curl commands and `VERB /path` lines; a sentence saying
"the endpoint creates a rate plan" is a description, not a request, and
guessing its shape is how a test ends up asserting something nobody
specified.

LINKS ARE CLASSIFIED, NOT ASSUMED
---------------------------------
A pasted link is routed to the Jira fetcher or the Confluence fetcher by
what it looks like. Neither fetcher is asked to handle the other's URLs,
and a link that is neither is reported as such rather than attempted.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Loaded by path: tools/jira and tools/confluence both contain a module
# called `fetch`, so putting either directory on sys.path makes the name
# ambiguous -- which already caused one confusing failure in Stage 1.
_JIRA = os.path.join(TOOLS, "jira")
_CONF = os.path.join(TOOLS, "confluence")

sys.path.insert(0, _JIRA)          # extract.py imports its siblings
projectconfig = _load("agent_projectconfig", os.path.join(_JIRA, "projectconfig.py"))
jira_extract = _load("agent_jira_extract", os.path.join(_JIRA, "extract.py"))
sys.path.insert(0, _CONF)
conf_pageref = _load("agent_conf_pageref", os.path.join(_CONF, "pageref.py"))
conf_fetch = _load("agent_conf_fetch", os.path.join(_CONF, "fetch.py"))

ROOT = projectconfig.ROOT

_JIRA_KEY_RX = re.compile(r"^[A-Za-z][A-Za-z0-9_]+-\d+$")
_JIRA_BROWSE_RX = re.compile(r"/browse/[A-Z][A-Z0-9_]+-\d+", re.I)

JIRA, CONFLUENCE, UNKNOWN = "jira", "confluence", "unknown"


def classify(link: str) -> str:
    """Which fetcher a pasted link belongs to."""
    s = (link or "").strip()
    if not s:
        return UNKNOWN
    if _JIRA_KEY_RX.match(s):
        return JIRA
    if _JIRA_BROWSE_RX.search(s):
        return JIRA
    ref, _why = conf_pageref.parse(s)
    if ref is not None and ref.kind in ("id", "title", "tiny") and "://" in s:
        return CONFLUENCE
    return UNKNOWN


_SAFE_JOB_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def safe_job(job: str) -> str:
    """A job id that cannot leave target/agent/.

    This comes from a UI text box. `os.path.join` lets an ABSOLUTE
    second argument win outright, so a job id of `C:/Windows/Temp` or
    `/etc` does not land under the job root at all, and `../..` walks
    out of it. Validate rather than sanitise: a silently-rewritten id
    makes two different jobs share a directory.
    """
    j = (job or "").strip()
    if not _SAFE_JOB_RX.match(j) or j in (".", ".."):
        raise ValueError(
            f"unusable job id {job!r}. Letters, digits, dot, dash and "
            f"underscore only, up to 64 characters -- it names a directory.")
    return j


def job_dir(job: str, root: str = "") -> str:
    return os.path.join(root or ROOT, "target", "agent", safe_job(job))


def _requests_from(text: str, source: str) -> list:
    """Requests in a block of text, each carrying its expected status.

    `from_text` does not return one, and without it shape_match's
    `want_status` is always empty -- which silently disables
    DUPLICATE_SUSPECT, one of the two stops the next stage exists to
    raise. `extract.expected_status` reads it from the same prose, so
    the information is there; it was simply being dropped.
    """
    try:
        steps = jira_extract.from_text(text or "", source) or []
        status, evidence = jira_extract.expected_status(text or "")
        for step in steps:
            step.setdefault("expected_status", status or "")
            if evidence:
                step.setdefault("expected_status_evidence", evidence)
        return steps
    except Exception as e:  # a malformed paste must not kill the job
        return [{"verb": "", "raw_path": "", "error": str(e),
                 "provenance": f"{source}: could not be read"}]


def gather_confluence(link: str, sources: str, slot: str,
                      api_path: str = "") -> dict:
    """Fetch one Confluence page into its OWN directory under sources/.

    An earlier version diffed the sources directory before and after the
    fetch to find the new file. That is wrong twice: two links to the
    same page produce no new file the second time, and RE-RUNNING a job
    produces none at all because the file is already on disk. Both read
    as "fetched fine, nothing in it", and re-running is the normal thing
    to do from a UI. A directory per link makes the answer positional
    instead of inferred.
    """
    outdir = os.path.join(sources, slot)
    os.makedirs(outdir, exist_ok=True)
    argv = ["--url", link, "--out", outdir]
    if api_path:
        argv += ["--api-path", api_path]
    try:
        rc = conf_fetch.main(argv)
    except SystemExit as e:           # argparse inside a library call
        rc = int(getattr(e, "code", 1) or 1)
    except Exception as e:
        return {"link": link, "kind": CONFLUENCE, "ok": False,
                "reason": f"{type(e).__name__}: {e}"}
    if rc != 0:
        return {"link": link, "kind": CONFLUENCE, "ok": False,
                "reason": "the fetcher refused -- run it directly to see why"}
    md = sorted(n for n in os.listdir(outdir) if n.endswith(".md"))
    if not md:
        return {"link": link, "kind": CONFLUENCE, "ok": False,
                "reason": "the fetcher reported success but wrote no page"}
    return {"link": link, "kind": CONFLUENCE, "ok": True,
            "dir": slot, "files": md,
            "text_file": os.path.join(slot, md[0])}


def build(job: str, text: str = "", links=None, root: str = "",
          api_path: str = "") -> dict:
    """Run the intake. Returns the structured result it also writes."""
    links = [l for l in (links or []) if (l or "").strip()]
    out = job_dir(job, root)
    sources = os.path.join(out, "sources")
    os.makedirs(sources, exist_ok=True)

    found, notes = [], []
    found += _requests_from(text, "pasted text")

    gathered = []
    for n, link in enumerate(links, 1):
        kind = classify(link)
        if kind == CONFLUENCE:
            got = gather_confluence(link, sources, f"{n:02d}-confluence",
                                    api_path)
            gathered.append(got)
            if got.get("ok") and got.get("text_file"):
                with io.open(os.path.join(sources, got["text_file"]),
                             encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
                found += _requests_from(body, f"confluence {link}")
            elif not got.get("ok"):
                notes.append(f"{link}: {got.get('reason')}")
        elif kind == JIRA:
            # Deliberately not fetched here. tools/jira/run.py is a
            # gated chain of its own that writes its own packet, and
            # re-running half of it from inside this stage would give
            # two answers to the same question.
            gathered.append({"link": link, "kind": JIRA, "ok": False,
                             "reason": "run tools/jira/run.py for this story; "
                                       "its packet is the input here"})
            notes.append(f"{link}: a Jira story -- use tools/jira/run.py")
        else:
            gathered.append({"link": link, "kind": UNKNOWN, "ok": False,
                             "reason": "not a Jira key or a Confluence page link"})
            notes.append(f"{link}: not recognised as Jira or Confluence")

    # Extraction failures were being dropped here: a step with no verb
    # is filtered out, so a paste the extractor choked on vanished
    # without a word. Surface them before filtering.
    for step in found:
        if step.get("error"):
            notes.append(f"{step.get('provenance', 'a source')}: "
                         f"{step['error']}")

    usable = [s for s in found if s.get("verb") and s.get("raw_path")]

    # The same request often arrives twice -- pasted AND on the page it
    # was pasted from. Left in, Stage 3 proposes two tests for one
    # request. Keep the first, which carries the earliest provenance.
    seen, deduped = set(), []
    for step in usable:
        key = (step.get("verb"), step.get("raw_path"),
               tuple(step.get("query_param_names") or ()),
               (step.get("body") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(step)
    if len(deduped) != len(usable):
        notes.append(f"{len(usable) - len(deduped)} duplicate request(s) "
                     f"collapsed")
    usable = deduped

    # A request body can carry a credential -- a token-request payload
    # is literally a client id and secret. The Confluence fetcher
    # redacts what IT reads, but pasted text reaches here unredacted,
    # and intake.json feeds both the UI and the agent prompt.
    for step in usable:
        if step.get("body"):
            step["body"] = conf_fetch.redact(step["body"])
    result = {
        "job": job,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pasted_chars": len(text or ""),
        "links": gathered,
        "requests": usable,
        "notes": notes,
        "ok": bool(usable),
    }
    _write_brief(out, result)
    with io.open(os.path.join(out, "intake.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    return result


def _write_brief(out: str, r: dict) -> None:
    lines = []
    if not r["ok"]:
        lines += ["# Intake: NO REQUEST COULD BE READ", "",
                  "Nothing below describes a request this pipeline can act "
                  "on. A sentence describing an endpoint is a description, "
                  "not a request -- paste a curl command, a `VERB /path` "
                  "line, or a link to a page that carries one.", ""]
    else:
        lines += [f"# Intake: {len(r['requests'])} request(s)", ""]
    lines += [f"- job: {r['job']}", f"- at: {r['at']}",
              f"- pasted: {r['pasted_chars']} chars", ""]

    if r["links"]:
        lines += ["## Sources", ""]
        for g in r["links"]:
            mark = "ok" if g.get("ok") else "NOT READ"
            lines.append(f"- [{mark}] ({g.get('kind')}) {g.get('link')}")
            if not g.get("ok"):
                lines.append(f"      {g.get('reason', '')}")
        lines.append("")

    if r["requests"]:
        lines += ["## Requests read", ""]
        for i, s in enumerate(r["requests"], 1):
            lines.append(f"{i}. `{s.get('verb')} {s.get('raw_path')}`")
            if s.get("query_param_names"):
                lines.append(f"   - query: {', '.join(s['query_param_names'])}")
            if s.get("body"):
                lines.append(f"   - body: {len(s['body'])} chars")
            lines.append(f"   - from: {s.get('provenance', '')}")
        lines.append("")

    if r["notes"]:
        lines += ["## Not used", ""] + [f"- {n}" for n in r["notes"]] + [""]

    lines += ["## Next", "",
              ("Stage 3 locates these against the existing tests and decides "
               "create / update / duplicate." if r["ok"] else
               "Nothing to locate. Add a readable request and run intake "
               "again.")]
    with io.open(os.path.join(out, "brief.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--job", required=True, help="job id; names the directory")
    ap.add_argument("--text", default="", help="pasted story / payload text")
    ap.add_argument("--text-file", default="", help="read the text from a file")
    ap.add_argument("--link", action="append", default=[],
                    help="a Jira key or a Confluence page link; repeatable")
    ap.add_argument("--api-path", default="")
    ap.add_argument("--root", default="")
    args = ap.parse_args(argv)

    try:
        safe_job(args.job)
    except ValueError as e:
        print(f"refused: {e}")
        return 2

    text = args.text
    if args.text_file:
        if not os.path.isfile(args.text_file):
            print(f"refused: no such file: {args.text_file}")
            return 2
        with io.open(args.text_file, encoding="utf-8",
                     errors="replace") as fh:
            text = fh.read()
    if not text.strip() and not args.link:
        print("refused: give --text, --text-file or at least one --link")
        return 2

    r = build(args.job, text=text, links=args.link, root=args.root,
              api_path=args.api_path)
    out = job_dir(args.job, args.root)
    print(f"{len(r['requests'])} request(s) read, "
          f"{sum(1 for g in r['links'] if g.get('ok'))}/{len(r['links'])} "
          f"source(s) fetched")
    for n in r["notes"]:
        print(f"  note: {n}")
    print(f"  {os.path.join(os.path.relpath(out, ROOT), 'brief.md')}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
