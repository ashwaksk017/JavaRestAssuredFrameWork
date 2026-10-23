"""Answers to a design's open questions -- and what is kept of them for
later designs.

A design asks what neither the material nor the project shows (how to put
an account in a given status, which vendor result to expect...). A person
answers on the Automate Test Case tab. Each answer is the job's
(`answers.json` in the job): Design again takes them as the tester's
answers. An answer ticked "keep" is also written into the project's
knowledge file, committed with the job's change and seen in its diff before
the push:

    .cursor/skills/jira-to-restassured/KNOWN_B2B.md   (KNOWN_GOAL.md, ...)

one entry a question, updated in place (never twice), naming the endpoints
it is about. Later designs and Automate runs are handed the kept entries
for their endpoints, and the dependency map shows them under the endpoint.
The file is public: an answer holding a value of the private configuration
is refused.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ANSWERS = "answers.json"
KNOWN_DIR = ".cursor/skills/jira-to-restassured"
MAX_ANSWER = 3000
MAX_QUESTION = 1000
MAX_BLOCK = 8000
# a path is letters, digits, {params}, - _ . / -- and ends on a letter, digit or }
_ENDPOINT_RX = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_{}./-]*[A-Za-z0-9_}])")
_ENTRY_RX = re.compile(r"<!-- known:([0-9a-f]{12}) -->\n(.*?)(?=\n<!-- known:|\Z)", re.S)
HEAD = ("# Known answers -- {label}\n\n"
        "Answers testers gave to questions a design could not answer from the API\n"
        "specification, the story or this project's code. Kept by the test workbench\n"
        "(Automate Test Case tab, Open questions); every design and Automate run of\n"
        "{label} reads the entries for its endpoints. One entry a question. Edit an\n"
        "answer on the page, not here: the page rewrites its entry.\n")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(path: str) -> dict:
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _one(v, cap: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:cap]


def qid(question: str) -> str:
    """A question's id: the same question, the same entry."""
    return hashlib.sha1(_one(question, MAX_QUESTION).lower().encode("utf-8")).hexdigest()[:12]


def known_rel(project: str) -> str:
    return f"{KNOWN_DIR}/KNOWN_{(project or 'b2b').upper()}.md"


def _sha(path: str) -> str:
    try:
        with io.open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return ""


def endpoints_in(text: str) -> list:
    return list(dict.fromkeys(f"{m.group(1).upper()} {m.group(2)}" for m in _ENDPOINT_RX.finditer(text or "")))


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path).rstrip("/")


# ---- the job's questions and answers ------------------------------------------------

def questions(job_dir: str) -> list:
    """[{id, question, answer, keep, at}] -- the design's open questions with
    what was answered; an answered question the design no longer asks stays."""
    asked = _json(os.path.join(job_dir, "design.json")).get("open_questions") or []
    saved = _json(os.path.join(job_dir, ANSWERS)).get("answers") or {}
    out, seen = [], set()
    for q in asked:
        q = _one(q, MAX_QUESTION)
        if not q or qid(q) in seen:
            continue
        seen.add(qid(q))
        a = saved.get(qid(q)) or {}
        out.append({"id": qid(q), "question": q, "answer": a.get("answer", ""),
                    "keep": bool(a.get("keep")), "at": a.get("at", ""), "asked": True})
    for i, a in saved.items():
        if i not in seen and isinstance(a, dict) and a.get("answer"):
            out.append({"id": i, "question": a.get("question", ""), "answer": a["answer"],
                        "keep": bool(a.get("keep")), "at": a.get("at", ""), "asked": False})
    return out


def save(job_dir: str, given: list, root: str = "", project: str = "b2b", private: dict = None,
         job: str = "") -> dict:
    """Keep the answers of a job; write each one ticked "keep" into the
    project's knowledge file. `given`: [{question, answer, keep}]."""
    root = root or ROOT
    if not isinstance(given, list):
        raise ValueError("send the answers as a list")
    data = _json(os.path.join(job_dir, ANSWERS))
    saved = data.get("answers") or {}
    kept_now = []
    for item in given:
        if not isinstance(item, dict):
            raise ValueError("each answer is an object: {question, answer, keep}")
        q = _one(item.get("question"), MAX_QUESTION)
        a = str(item.get("answer") or "").strip()
        if not q:
            raise ValueError("an answer names its question")
        if len(a) > MAX_ANSWER:
            raise ValueError(f"an answer is at most {MAX_ANSWER} characters")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", a):
            raise ValueError("an answer has a control character in it")
        keep = bool(item.get("keep")) and bool(a)
        if keep:
            leaked = [v for v in (private or {}) if len(v) >= 6 and v.lower() in (q + "\n" + a).lower()]
            if leaked:
                raise ValueError("an answer to keep holds a value of the private configuration "
                                 "(a host or a credential): the knowledge file is public. Write it "
                                 "without that value.")
        prev = saved.get(qid(q)) or {}
        if not a and not prev:
            continue
        saved[qid(q)] = {"question": q, "answer": a, "keep": keep, "at": _now()
                         if (a, keep) != (prev.get("answer"), prev.get("keep")) else prev.get("at", _now())}
        if keep or prev.get("keep"):
            kept_now.append(qid(q))
    data.update(kind="answers", answers=saved)
    if kept_now:
        rel = known_rel(project)
        _write_known(root, rel, project, saved, kept_now, job)
        data["knowledge"] = {"path": rel, "sha": _sha(os.path.join(root, *rel.split("/")))}
    os.makedirs(job_dir, exist_ok=True)
    with io.open(os.path.join(job_dir, ANSWERS), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    return {"saved": len(given), "kept": [i for i in kept_now if (saved.get(i) or {}).get("keep")],
            "file": data.get("knowledge", {}).get("path", "")}


def _write_known(root: str, rel: str, project: str, saved: dict, ids: list, job: str) -> None:
    path = os.path.join(root, *rel.split("/"))
    try:
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        text = HEAD.format(label=(project or "b2b").upper())
    entries = {m.group(1): m.group(0) for m in _ENTRY_RX.finditer(text)}
    head = text[:text.index("<!-- known:")] if "<!-- known:" in text else text
    for i in ids:
        a = saved.get(i) or {}
        if not a.get("keep"):
            entries.pop(i, None)                         # un-kept: its entry goes
            continue
        eps = endpoints_in(a["question"] + "\n" + a["answer"])
        body = (f"<!-- known:{i} -->\n### {a['question']}\n\n"
                + (f"Endpoints: {', '.join(eps)}\n\n" if eps else "")
                + a["answer"].strip() + "\n\n"
                + f"_Kept {a['at'][:10]}" + (f" (job {job})" if job else "") + "._\n")
        entries[i] = body
    out = head.rstrip() + "\n\n" + "\n".join(e.rstrip() + "\n" for e in entries.values())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out.rstrip() + "\n")


# ---- what later runs are handed ---------------------------------------------------

def known(root: str, project: str) -> list:
    """[{id, question, answer, endpoints}] of the project's knowledge file."""
    text = ""
    try:
        with io.open(os.path.join(root or ROOT, *known_rel(project).split("/")), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    out = []
    for m in _ENTRY_RX.finditer(text):
        body = m.group(2).strip()
        q = re.match(r"###\s*(.+)", body)
        eps = re.search(r"^Endpoints:\s*(.+)$", body, re.M)
        answer = re.sub(r"^###.*\n|^Endpoints:.*\n|^_Kept .*$", "", body, flags=re.M).strip()
        out.append({"id": m.group(1), "question": q.group(1).strip() if q else "",
                    "answer": answer,
                    "endpoints": [e.strip() for e in eps.group(1).split(",")] if eps else []})
    return out


def for_endpoints(entries: list, endpoints: list) -> list:
    """The entries about these endpoints (and those that name none)."""
    want = {(e.split(" ", 1)[0].upper(), _norm(e.split(" ", 1)[-1])) for e in endpoints if " " in e}
    out = []
    for e in entries:
        mine = {(x.split(" ", 1)[0].upper(), _norm(x.split(" ", 1)[-1])) for x in e["endpoints"] if " " in x}
        if not mine or mine & want:
            out.append(e)
    return out


def block(root: str, job_dir: str, project: str, endpoints: list) -> str:
    """ANSWERS FROM THE TESTER: this job's answers, and what was kept before
    about these endpoints."""
    mine = [q for q in questions(job_dir) if q["answer"]]
    kept = [e for e in for_endpoints(known(root, project), endpoints)
            if e["id"] not in {q["id"] for q in mine}]
    if not mine and not kept:
        return ""
    lines = ["===== ANSWERS FROM THE TESTER (material: what the specification and the code do "
             "not say) ====="]
    for q in mine:
        lines += [f"Q: {q['question']}", f"A: {q['answer']}", ""]
    if kept:
        lines.append(f"Answered before, kept in {known_rel(project)}:")
        for e in kept:
            lines += [f"Q: {e['question']}", f"A: {e['answer']}", ""]
    text = "\n".join(lines).rstrip() + "\n"
    return text if len(text) <= MAX_BLOCK else text[:MAX_BLOCK] + "\n[... more answers than fit ...]\n"


# ---- the push -------------------------------------------------------------------------

def diff(job_dir: str, root: str = "") -> dict:
    """{path, diff} of the knowledge file this job wrote, against the last
    commit -- what its push will add, shown before it is approved."""
    import subprocess
    root = root or ROOT
    rel = str((_json(os.path.join(job_dir, ANSWERS)).get("knowledge") or {}).get("path") or "")
    if not rel or not os.path.isfile(os.path.join(root, *rel.split("/"))):
        return {"path": "", "diff": ""}
    def git(*args):
        try:
            p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            return p.returncode, p.stdout
        except (OSError, subprocess.SubprocessError):
            return 1, ""
    rc, out = git("ls-files", "--error-unmatch", "--", rel)
    if rc == 0:
        return {"path": rel, "diff": git("diff", "--", rel)[1]}
    with io.open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return {"path": rel, "diff": f"new file {rel}\n" + "".join("+" + ln for ln in fh.readlines())}

def to_push(job_dir: str, root: str = "") -> tuple:
    """(paths to add to the job's commit, why one cannot be). The knowledge
    file this job wrote -- only as the job left it."""
    k = _json(os.path.join(job_dir, ANSWERS)).get("knowledge") or {}
    rel = str(k.get("path") or "")
    if not rel or not rel.startswith(KNOWN_DIR + "/KNOWN_") or ".." in rel:
        return [], ""
    now = _sha(os.path.join(root or ROOT, *rel.split("/")))
    if not now:
        return [], ""
    if now != k.get("sha"):
        return [], (f"{rel} changed since this job kept its answers: save the answers again on the "
                    f"Automate Test Case tab (the page rewrites its entries), then approve")
    return [rel], ""
