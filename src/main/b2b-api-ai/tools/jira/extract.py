"""Story -> a candidate request, deterministically or not at all.

    python tools/jira/extract.py --story target/jira/B2B-1234/story.json
    python tools/jira/extract.py --story ... --json target/jira/B2B-1234/candidate.json
    python tools/jira/extract.py --story ... --index target/shape-index.json

Reads the snapshot `fetch.py` wrote and emits the `{issue_key, steps}`
document `shape_match.py` consumes.

NO INFERENCE. EVER.
-------------------
Every field comes from text that is actually in the story: a `curl`
command, an explicit `VERB /path` line, a fenced JSON body, a HAR entry.
If the verb or the path cannot be read, this writes nothing and exits 1
with what it DID find, because a guessed endpoint is the one mistake that
survives review -- it reads like a decision someone made.

WHY A CONCRETE PATH MUST BE TEMPLATIZED
---------------------------------------
A story says `POST /businesses/12345/activate`. The converter recorded
`/businesses/{accountId}/activate`, and the match signature holds the
resource path LITERALLY. Compared as-is, every story on earth comes back
`NEW` -- a false negative that would quietly make the matcher useless
while looking like it worked.

So the concrete path is matched against the templates the index actually
contains: literal segments must agree, a `{name}` segment takes anything
and names the parameter it took. Measured on the current index, 5,564 of
7,756 recorded steps have a templated path, so this is the normal case
and not an edge one.

When several templates fit, they are ALL reported and none is chosen.
When none fits, the concrete path is kept -- `NEW` is then the honest
answer rather than an artifact of formatting.

WHY PATH PARAMS ARE EMITTED AS REFS
-----------------------------------
`_param_binding_sig` reads a literal value as `row` (a CSV column can
carry it) and a `${...}`/`#...#` value as `ref` (it comes from a previous
call). Measured: 8,748 recorded path params are `ref` and 325 are `row`.
A story quotes a concrete id, which would key as `row` and miss the 96%.

That id is test DATA -- a CSV cell -- not structure, which is the same
reason values stay out of the signature at all. So it is emitted as a
ref, the literal is kept beside it as `example`, and the note says so.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import sys
from urllib.parse import urlparse, parse_qsl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import projectconfig  # noqa: E402

ROOT = projectconfig.ROOT

VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

# `POST /businesses/{accountId}/activate`, with the bold/heading noise a
# Jira description collects around it.
#
# NOT anchored to the line start. A story writes the call inside a
# sentence at least as often as on a line of its own -- "Call `POST
# /businesses/{accountId}/activate` to activate" is the normal phrasing,
# and anchoring read it as no request at all.
#
# `\b\**\s+` after the verb is what keeps this honest: a verb has to be
# followed by whitespace and then something path-shaped, so "POSTAL
# address" and "DELETE the account" are not calls.
_HTTP_LINE_RX = re.compile(
    r"(?im)(?:^|[\s>*#|`(\[-])(?:h[1-6]\.\s*)?\**(" + "|".join(VERBS) + r")\b"
    r"\**\s+[`\"']?((?:https?://[^\s`\"'|)\]]+)|/[A-Za-z0-9._~%\-/{}]*)")

# "returns 204", "responds with a 400", "-> 201", "status code 409".
_STATUS_RX = re.compile(
    r"(?i)\b(?:returns?|respond(?:s|ed)?(?:\s+with)?|status(?:\s+code)?|"
    r"expect(?:ed|s)?|HTTP)\b[^0-9\n]{0,24}\b([1-5]\d\d)\b"
    r"|(?:->|=>|→)\s*([1-5]\d\d)\b")

_FENCE_RX = re.compile(r"```[a-zA-Z0-9]*\s*\n(.*?)```", re.S)
_NOFORMAT_RX = re.compile(
    r"(?s)\{(?:code|noformat)[^}]*\}(.*?)\{(?:code|noformat)\}")
_CURL_RX = re.compile(r"(?is)\bcurl\b[^\n]*(?:\\\s*\n[^\n]*)*")

# curl flags that consume the following token. An unknown flag is skipped
# alone, so a flag-with-value we do not know about costs one stray token
# rather than silently becoming the URL.
_CURL_VALUE_FLAGS = {
    "-X", "--request", "-H", "--header", "-d", "--data", "--data-raw",
    "--data-binary", "--data-ascii", "--data-urlencode", "--url", "-u",
    "--user", "-o", "--output", "-e", "--referer", "-A", "--user-agent",
    "-b", "--cookie", "-c", "--cookie-jar", "--connect-timeout", "-m",
    "--max-time", "--retry", "-w", "--write-out", "-T", "--upload-file",
    "-F", "--form", "--cacert", "--cert", "--key", "--proxy", "-x",
    "--resolve", "--oauth2-bearer",
}
_CURL_BODY_FLAGS = {"-d", "--data", "--data-raw", "--data-binary",
                    "--data-ascii", "--data-urlencode"}

MAX_TRIM_SEGMENTS = 3


# --- path templates ---------------------------------------------------
def path_templates(index: dict) -> list[str]:
    """Every distinct recorded resource path, longest first.

    Read out of the shape index rather than from a list kept here: a list
    would drift away from what was actually converted, and a template
    that no longer exists is worse than no template at all.
    """
    seen: set = set()
    for sig in (index or {}).get("exact", {}):
        try:
            steps = json.loads(sig)
        except ValueError:
            continue
        for step in steps:
            if isinstance(step, list) and len(step) > 1 and step[1]:
                seen.add(str(step[1]))
    return sorted(seen, key=lambda p: (-len(p.split("/")), p))


def _fits(template: str, concrete: str) -> dict | None:
    """{param: value} when the template fits, else None."""
    t = [s for s in template.split("/")]
    c = [s for s in concrete.split("/")]
    if len(t) != len(c):
        return None
    got: dict = {}
    for ts, cs in zip(t, c):
        if ts.startswith("{") and ts.endswith("}") and len(ts) > 2:
            if not cs:
                return None
            got[ts[1:-1]] = cs
        elif ts.lower() != cs.lower():
            return None
    return got


def templatize(concrete: str, templates: list[str]) -> dict:
    """Which recorded path this is, and what filled its parameters.

    Returns `matched` (one template, or "" when it could not be narrowed
    to one), `params`, every `alternatives`, and the `trimmed` prefix --
    a story's curl carries `/v1/...` or a gateway prefix that the
    recorded path does not, so leading segments are dropped one at a time
    and the drop is reported.
    """
    out = {"matched": "", "params": {}, "alternatives": [], "trimmed": "",
           "notes": []}
    if not concrete:
        return out
    candidate = concrete if concrete.startswith("/") else "/" + concrete
    for drop in range(0, MAX_TRIM_SEGMENTS + 1):
        if drop:
            parts = candidate.lstrip("/").split("/")
            if len(parts) <= drop:
                break
            trial = "/" + "/".join(parts[drop:])
        else:
            trial = candidate
        fits = [(t, p) for t in templates
                for p in [_fits(t, trial)] if p is not None]
        if not fits:
            continue
        out["alternatives"] = [t for t, _ in fits]
        out["trimmed"] = "/".join(candidate.lstrip("/").split("/")[:drop])
        if len(fits) == 1:
            out["matched"], out["params"] = fits[0]
            if out["trimmed"]:
                out["notes"].append(
                    f"dropped the leading `/{out['trimmed']}` to match the "
                    f"recorded path; the converter keeps the base URL and "
                    f"version in configuration, not in the resource path")
        else:
            out["notes"].append(
                f"{len(fits)} recorded paths fit `{trial}` "
                f"({', '.join(t for t, _ in fits[:4])}"
                f"{', ...' if len(fits) > 4 else ''}). Not choosing between "
                f"them -- the concrete path is kept as given")
        return out
    return out


def _as_refs(params: dict) -> tuple[dict, dict]:
    """({param: ref}, {param: literal}) -- see the module docstring."""
    refs, examples = {}, {}
    for name, value in params.items():
        refs[name] = "${" + f"{name}" + "}"
        examples[name] = value
    return refs, examples


# --- extractors -------------------------------------------------------
def parse_curl(command: str) -> dict:
    """A curl command as a step, or {} when it carries no URL."""
    joined = re.sub(r"\\\s*\n", " ", command or "")
    try:
        toks = shlex.split(joined, posix=True)
    except ValueError:
        toks = joined.split()
    verb, url, body, headers = "", "", "", {}
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "curl":
            i += 1
            continue
        if t in ("-X", "--request") and i + 1 < len(toks):
            verb = toks[i + 1].upper()
            i += 2
            continue
        if t in ("-H", "--header") and i + 1 < len(toks):
            k, _, v = toks[i + 1].partition(":")
            if k.strip():
                headers[k.strip()] = v.strip()
            i += 2
            continue
        if t in _CURL_BODY_FLAGS and i + 1 < len(toks):
            body = toks[i + 1] if not body else body + toks[i + 1]
            i += 2
            continue
        if t == "--url" and i + 1 < len(toks):
            url = toks[i + 1]
            i += 2
            continue
        if t.startswith("-"):
            i += 2 if t in _CURL_VALUE_FLAGS else 1
            continue
        if not url and ("://" in t or t.startswith("/")):
            url = t
        i += 1
    if not url:
        return {}
    if not verb:
        # curl's own rule, not a guess: -d implies POST.
        verb = "POST" if body else "GET"
    return {"verb": verb, "url": url, "body": body, "headers": headers}


def _split_url(url: str) -> tuple[str, list]:
    if "://" in url:
        u = urlparse(url)
        return (u.path or "/"), [k for k, _ in parse_qsl(u.query)]
    path, _, query = url.partition("?")
    return (path or "/"), [k for k, _ in parse_qsl(query)]


def _media_type(headers: dict, body: str) -> str:
    for k, v in (headers or {}).items():
        if k.lower() == "content-type":
            return v.split(";")[0].strip() or "application/json"
    return "application/json"


def _json_body_near(text: str, at: int) -> str:
    """The first fenced JSON block that STARTS after `at`.

    After, not nearest: a story writes the call and then its body, and
    searching backwards would attach the previous request's payload to
    this one.
    """
    for rx in (_FENCE_RX, _NOFORMAT_RX):
        for m in rx.finditer(text or ""):
            if m.start() < at:
                continue
            block = (m.group(1) or "").strip()
            if not block.startswith(("{", "[")):
                continue
            try:
                json.loads(block)
            except ValueError:
                continue
            return block
    return ""


def expected_status(text: str) -> tuple[str, str]:
    """(status, the sentence it came from). Read, never inferred."""
    m = _STATUS_RX.search(text or "")
    if not m:
        return "", ""
    code = m.group(1) or m.group(2) or ""
    line = ""
    for candidate in (text or "").splitlines():
        if m.group(0).strip()[:24] in candidate:
            line = candidate.strip()
            break
    return code, line[:160]


def from_text(text: str, source: str) -> list[dict]:
    """Steps from a block of story text: curl first, then VERB /path."""
    steps = []
    for m in _CURL_RX.finditer(text or ""):
        parsed = parse_curl(m.group(0))
        if not parsed:
            continue
        path, query = _split_url(parsed["url"])
        steps.append({
            "verb": parsed["verb"], "raw_path": path,
            "query_param_names": query, "body": parsed["body"],
            "media_type": _media_type(parsed["headers"], parsed["body"]),
            "provenance": f"{source}: curl command", "extractor": "curl",
        })
    if steps:
        return steps
    for m in _HTTP_LINE_RX.finditer(text or ""):
        verb, target = m.group(1).upper(), m.group(2)
        path, query = _split_url(target)
        body = _json_body_near(text, m.end()) if verb in (
            "POST", "PUT", "PATCH") else ""
        steps.append({
            "verb": verb, "raw_path": path, "query_param_names": query,
            "body": body, "media_type": "application/json",
            "provenance": f"{source}: `{verb} {target}`"
                          + (" plus the fenced JSON after it" if body else ""),
            "extractor": "http-line",
        })
    return steps


def from_har(path: str) -> list[dict]:
    """Steps from an attached HAR. A recording, so it needs no reading."""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return []
    steps = []
    for entry in ((doc.get("log") or {}).get("entries") or []):
        req = entry.get("request") or {}
        url = req.get("url") or ""
        if not url:
            continue
        p, query = _split_url(url)
        headers = {h.get("name", ""): h.get("value", "")
                   for h in (req.get("headers") or [])}
        body = ((req.get("postData") or {}).get("text") or "")
        steps.append({
            "verb": (req.get("method") or "GET").upper(), "raw_path": p,
            "query_param_names": [q.get("name") for q in
                                  (req.get("queryString") or [])] or query,
            "body": body, "media_type": _media_type(headers, body),
            "provenance": f"attachment {os.path.basename(path)}: HAR entry",
            "extractor": "har",
        })
    return steps


# --- the whole story --------------------------------------------------
def story_text(story: dict) -> list[tuple[str, str]]:
    """[(where, text)] over the story, its parents and every comment."""
    out = []
    issues = [story.get("issue") or {}] + list(story.get("parents") or [])
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("_error"):
            continue
        key = issue.get("key", "?")
        f = issue.get("fields") or {}
        import fetch                                   # same package
        desc = fetch._text_of(f.get("description"))
        if desc.strip():
            out.append((f"{key} description", desc))
        for c in ((f.get("comment") or {}).get("comments") or []):
            body = fetch._text_of(c.get("body"))
            if body.strip():
                who = ((c.get("author") or {}).get("displayName") or "?")
                out.append((f"{key} comment by {who}", body))
    return out


def extract(story: dict, index: dict | None = None,
            attachments: list | None = None) -> dict:
    """The candidate document, or one that says why there is none."""
    templates = path_templates(index or {})
    blocks = story_text(story)
    summary = (story.get("summary") or {})
    key = summary.get("issue_key") or (story.get("issue") or {}).get("key", "")

    raw_steps: list = []
    for a in (attachments or []):
        p = a.get("saved_to") or ""
        if p.lower().endswith(".har") and os.path.isfile(p):
            raw_steps.extend(from_har(p))
    if not raw_steps:
        for where, text in blocks:
            raw_steps.extend(from_text(text, where))

    all_text = "\n".join(t for _, t in blocks)
    status, status_evidence = expected_status(all_text)

    notes: list = []
    if not templates:
        notes.append("no shape index was given, so a concrete path could not "
                     "be matched to a recorded one. Build it with "
                     "`python tools/jira/shapes.py --dump target/shape-index.json`")

    steps: list = []
    for n, s in enumerate(raw_steps, 1):
        t = templatize(s["raw_path"], templates)
        notes.extend(t["notes"])
        path = t["matched"] or s["raw_path"]
        refs, examples = _as_refs(t["params"])
        if refs:
            notes.append(
                f"step {n}: path parameter(s) {', '.join(sorted(refs))} were "
                f"given as literals and are emitted as refs, because 8,748 of "
                f"9,073 recorded path params are refs and the literal is a "
                f"CSV cell, not structure. The literals are kept as `example`")
        steps.append({
            "step_name": f"step{n}",
            "verb": s["verb"],
            "path": path,
            "media_type": s["media_type"],
            "body": s["body"],
            "path_params": refs,
            "query_params": {q: "${" + str(q) + "}"
                             for q in (s["query_param_names"] or []) if q},
            "expected_status": status,
            "_provenance": s["provenance"],
            "_extractor": s["extractor"],
            "_raw_path": s["raw_path"],
            "_path_param_examples": examples,
            "_path_alternatives": t["alternatives"] if not t["matched"] else [],
        })

    doc = {
        "schema": 1,
        "issue_key": key,
        "steps": steps,
        "expected_status": status,
        "expected_status_evidence": status_evidence,
        "notes": notes,
        "payload_candidates_seen": len(summary.get("payload_candidates") or []),
    }
    if not steps:
        doc["extraction"] = "FAILED"
        doc["why"] = (
            "no request could be READ from the story. Looked for: a curl "
            "command, an explicit `VERB /path` line, and an attached HAR. "
            "Nothing here infers an endpoint from prose -- a guessed verb or "
            "path reads like a decision someone made, and survives review.")
    else:
        doc["extraction"] = "OK"
    return doc


def report(doc: dict) -> str:
    out = [f"{doc.get('issue_key') or '(no key)'}  extraction: "
           f"{doc.get('extraction')}"]
    for s in doc.get("steps") or []:
        out.append(f"  {s['verb']:<6} {s['path']}")
        out.append(f"    from     : {s['_provenance']}")
        if s["_raw_path"] != s["path"]:
            out.append(f"    as given : {s['_raw_path']}")
        if s["path_params"]:
            ex = ", ".join(f"{k}={v}" for k, v in
                           sorted((s["_path_param_examples"] or {}).items()))
            out.append(f"    path     : {', '.join(sorted(s['path_params']))}"
                       f"  (example: {ex})")
        if s["query_params"]:
            out.append(f"    query    : {', '.join(sorted(s['query_params']))}")
        out.append(f"    body     : {len(s['body'])} chars"
                   if s["body"] else "    body     : (none)")
        if s["_path_alternatives"]:
            out.append(f"    ambiguous: {len(s['_path_alternatives'])} recorded "
                       f"paths fit; none chosen")
            for alt in s["_path_alternatives"][:6]:
                out.append(f"      - {alt}")
    if doc.get("expected_status"):
        out.append(f"\n  expected status: {doc['expected_status']}")
        if doc.get("expected_status_evidence"):
            out.append(f"    read from: {doc['expected_status_evidence']}")
    else:
        out.append("\n  expected status: not stated in the story. Without it "
                   "a duplicate cannot be distinguished from a new row.")
    for n in doc.get("notes") or []:
        out.append(f"  - {n}")
    if doc.get("extraction") == "FAILED":
        out.append(f"\nEXTRACTION_FAILED\n  {doc['why']}")
        seen = doc.get("payload_candidates_seen") or 0
        out.append(f"  fetch.py found {seen} payload candidate(s); a payload "
                   f"alone is not a request -- it carries no verb and no path.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--story", required=True,
                    help="story.json from `python tools/jira/fetch.py`")
    ap.add_argument("--index", default="target/shape-index.json",
                    help="from `python tools/jira/shapes.py --dump PATH`; "
                         "used to templatize a concrete path")
    ap.add_argument("--json", metavar="PATH",
                    help="write the candidate (default beside the story, as "
                         "candidate.json)")
    args = ap.parse_args(argv)

    try:
        with io.open(args.story, encoding="utf-8") as fh:
            story = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"could not read {args.story}: {e}")
        return 2

    index_path = args.index if os.path.isabs(args.index) else os.path.join(
        ROOT, args.index.replace("/", os.sep))
    index = {}
    if os.path.isfile(index_path):
        try:
            with io.open(index_path, encoding="utf-8") as fh:
                index = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"could not read the shape index {index_path}: {e}")

    doc = extract(story, index,
                  (story.get("summary") or {}).get("attachments"))
    print(report(doc))

    out = args.json or os.path.join(
        os.path.dirname(os.path.abspath(args.story)), "candidate.json")
    if doc["extraction"] == "OK":
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with io.open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        print(f"\ncandidate : {out}")
        print(f"next      : python tools/jira/shape_match.py --candidate {out}")
        return 0
    print("\nNothing written. Fix the story, or have a person or an agent read "
          "it and write the candidate by hand -- the shape is in this file's "
          "docstring.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
