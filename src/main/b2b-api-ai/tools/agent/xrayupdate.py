"""Bring the job's Xray tests in line with its cases -- after showing what
will change, and never over a change made in Xray since.

    python tools/agent/xrayupdate.py plan  --job J
    python tools/agent/xrayupdate.py apply --job J --choices TC-001=send,TC-002=take

Each case with an Xray key (xray-tests.json) is looked at in three
versions:

* BASE  -- what the workbench last sent to that test, or took from it
  (Create in Xray, Use the story's Xray tests, or an earlier update);
* OURS  -- the case now: the design, with the review's corrections;
* XRAY  -- the test as Xray has it now, read when the preview is made.

    ours vs base   xray vs base   the preview proposes
    same           same           nothing (in step)
    changed        same           send ours (nobody touched Xray)
    same           changed        Xray is newer: take Xray's into the case, or skip
    changed        changed        a conflict: send ours, take Xray's, or skip

`plan` reads Xray and writes xray-update-plan.json; it changes nothing.
`apply` changes only what was chosen, and only while each test is still as
the preview showed it (read again first): a test changed in Xray since is
skipped and said. Before a test is overwritten, what Xray had is kept in
xray-update-backup.json in the job. "take" changes the case (as a review
correction), not Jira.

Xray Server / Data Center only. The step calls -- GET/PUT /rest/raven/1.0/
api/test/<key>/step, POST/DELETE .../step/<id> -- are Xray Server's REST
API; they have not been run against a live Xray. Writing to Jira needs
jira_config.publish.create_tests, as Create in Xray does.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str, path: str):
    import importlib.util
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


diag = _load("workbench_diag", os.path.join(os.path.dirname(HERE), "diag.py"))
xt = _load("agent_xraytests", os.path.join(HERE, "xraytests.py"))
mt = _load("agent_manualtests", os.path.join(HERE, "manualtests.py"))
loop = xt.loop
search = xt.search
publish = xt.publish
casereview = xt.casereview
Refused = xt.Refused

PLAN = "xray-update-plan.json"
RESULT = "xray-update.json"
BACKUP = "xray-update-backup.json"
STEP_API = "/rest/raven/1.0/api"
CHOICES = ("send", "take", "skip")
DEFAULT = {"update": "send", "xray_newer": "skip", "conflict": "skip", "describe": "send"}
_CHOICE_RX = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]{0,39})=(send|take|skip)$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jira_time(value):
    """A Jira timestamp ("2026-10-16T10:20:30.000+0000") as a datetime, or None."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(str(value or ""), fmt)
        except ValueError:
            continue
    return None


def after(updated, synced) -> bool:
    """Did Jira change the issue after our last sync? Both are JIRA's
    timestamps (ours is the `updated` Jira gave right after we wrote), so
    the two clocks are one clock."""
    u, s = jira_time(updated), jira_time(synced)
    return bool(u and s and u > s)


def _norm(value) -> str:
    return " ".join(str(value if value is not None else "").split())


def shape(title, steps) -> dict:
    """A test as compared: its title and steps, whitespace made plain."""
    return {"title": _norm(title),
            "steps": [{"action": _norm(s.get("action")), "data": _norm(s.get("data")),
                       "expected": _norm(s.get("expected"))}
                      for s in steps or [] if isinstance(s, dict)]}


def base_of(entry: dict) -> dict:
    """The base snapshot kept with a key (from this version on); None for a
    key kept before snapshots were."""
    b = entry.get("base") if isinstance(entry.get("base"), dict) else None
    return shape(b.get("title"), b.get("steps")) if b else None


def snapshot(case: dict) -> dict:
    """What is kept as the base: the case as sent or taken."""
    return {"title": case.get("title", ""), "preconditions": case.get("preconditions", ""),
            "steps": [{k: s.get(k, "") for k in ("action", "data", "expected")}
                      for s in case.get("steps") or [] if isinstance(s, dict)]}


def read_xray(conn: dict, key: str, transport=None, mocked: bool = False) -> dict:
    """{title, steps: [{id, action, data, expected}]} as Xray has the test
    now. Step ids come from Xray's v1 step API; without them (an Xray that
    answers only the v2 call) the steps are read, and cannot be edited."""
    issue = search._call(conn, "GET", f"/issue/{key}?fields=summary,updated,description&expand=changelog",
                         None, transport)
    fields = ((issue or {}).get("fields") or {}) if isinstance(issue, dict) else {}
    title = str(fields.get("summary") or "")
    # Who changed it last, and when: the newest entry of its history. (Jira
    # counts every edit -- a comment, a link, a status -- so this is shown,
    # and the steps themselves decide.)
    histories = (((issue or {}).get("changelog") or {}).get("histories") or []) if isinstance(issue, dict) else []
    last = max((h for h in histories if isinstance(h, dict)), key=lambda h: str(h.get("created") or ""),
               default={})
    last_by = str(((last.get("author") or {}) if isinstance(last.get("author"), dict) else {}).get("displayName") or "")
    rows = None
    if not mocked:
        try:
            got = search._call(dict(conn, api=STEP_API), "GET", f"/test/{key}/step", None, transport)
            rows = got.get("steps") if isinstance(got, dict) else got
        except search.JiraError:
            rows = None
    if isinstance(rows, list):
        steps = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            step = {"id": r.get("id"), "action": mt._field(r, "step", "action"),
                    "data": mt._field(r, "data"),
                    "expected": mt._field(r, "result", "expected", "expectedresult")}
            if step["action"] or step["expected"]:
                steps.append(step)
    else:
        steps = [dict(s, id=None) for s in mt.steps_of(conn, key, transport)]
    return {"title": title, "steps": steps, "updated": str(fields.get("updated") or ""),
            "description": str(fields.get("description") or ""),
            "last_by": last_by, "last_at": str(last.get("created") or "")}


def updated_now(conn: dict, key: str, transport=None) -> str:
    """Jira's `updated` of the test, read right after we wrote to it."""
    try:
        got = search._call(conn, "GET", f"/issue/{key}?fields=updated", None, transport)
        return str(((got or {}).get("fields") or {}).get("updated") or "") if isinstance(got, dict) else ""
    except search.JiraError:
        return ""


def _ours(job_dir: str, root: str = "") -> dict:
    """{case id: case} -- the design with the review's corrections, and each
    case's Description (`describe`, casedescribe.py)."""
    result = publish.result_for(job_dir, "design")
    cases = casereview.apply(xt.cases_of(result, "design"), casereview.read(job_dir)["design"])
    xt.described(root or xt.ROOT, job_dir, cases, "design")
    return {c["id"]: c for c in cases}


def _describes(entry: dict, case: dict) -> bool:
    """Whether a send carries the case's Description: always to a test the
    workbench made; to one taken from the story (imported) once its case is
    automated -- as a section after its author's text (with_automation)."""
    if not entry.get("story") or not case.get("describe"):
        return False
    return bool(case.get("describe_automated")) if entry.get("imported") else True


def _description(entry: dict, case: dict, xray: dict) -> str:
    """The description sent: the workbench's whole, or an imported test's
    own with the workbench's section."""
    if entry.get("imported"):
        return xt.with_automation(xray.get("description") or "", case)
    return xt.description_of(case, entry["story"], "design")


def _describe_due(entry: dict, case: dict) -> bool:
    """A test whose Description changed since it was last sent (as automated
    now, say): sent on its own, steps untouched. A key kept before
    Descriptions were sent, or taken from the story, gets one once its case
    is automated -- not a preview full of plans nobody asked to send."""
    if not _describes(entry, case):
        return False
    if "described" not in entry:
        return bool(case.get("describe_automated"))
    return case.get("described", "") != entry.get("described", "")


def classify(entry: dict, ours: dict, xray: dict) -> tuple:
    """(status, ours changed, xray changed)."""
    o, x = shape(ours["title"], ours["steps"]), shape(xray["title"], xray["steps"])
    if o == x:
        return "same", False, False
    base = base_of(entry)
    if base is not None:
        ours_changed, xray_changed = o != base, x != base
    else:
        # A key kept before base snapshots: its fingerprint is of the case
        # as sent. Xray's side is checked with our preconditions (Xray holds
        # them in the description, not the steps); a doubt is a conflict.
        fp = entry.get("fingerprint")
        ours_changed = xt.fingerprint(ours) != fp
        if entry.get("synced_updated") and xray.get("updated")                 and not after(xray["updated"], entry["synced_updated"]):
            # Jira says nobody touched the issue since our last sync.
            xray_changed = False
        else:
            xray_changed = xt.fingerprint({"title": xray["title"], "preconditions": ours.get("preconditions", ""),
                                           "steps": [{k: s.get(k, "") for k in ("action", "data", "expected")}
                                                     for s in xray["steps"]]}) != fp
    if ours_changed and not xray_changed:
        return "update", True, False
    if xray_changed and not ours_changed:
        return "xray_newer", False, True
    return "conflict", ours_changed, xray_changed


def _connect(root: str, conn=None, transport=None, xray_cfg: dict = None):
    conn = conn or search.connect()
    mocked = bool(conn.get("mock"))
    if mocked and transport is None:
        transport = lambda m, u, t, s, b=None: publish._mock().jira_transport(m, u, t, s, b, root)
    if not mocked and xt.flavour(xray_cfg) == "cloud":
        raise Refused("the xray configuration says Xray CLOUD; updating tests speaks Xray Server / "
                      "Data Center only")
    return conn, mocked, transport


def plan(job: str, root: str = "", conn=None, transport=None, xray_cfg: dict = None) -> dict:
    root = root or xt.ROOT
    job_dir = loop.intake.job_dir(job, root)
    conn, mocked, transport = _connect(root, conn, transport, xray_cfg)
    keys = xt.read_keys(job_dir)["tests"]
    ours = _ours(job_dir, root)
    rows, counts = [], {"same": 0, "update": 0, "xray_newer": 0, "conflict": 0, "unreadable": 0,
                        "describe": 0}
    for ident, entry in sorted(keys.items()):
        if not ident.startswith("design:") or not isinstance(entry, dict):
            continue
        if bool(entry.get("mock")) != mocked:
            continue
        case_id, key = ident.split(":", 1)[1], str(entry.get("key") or "").upper()
        case = ours.get(case_id)
        if not case or not xt._KEY_RX.match(key):
            continue
        diag.step(f"Reading {key} from Xray")
        try:
            xray = read_xray(conn, key, transport, mocked)
        except search.JiraError as e:
            counts["unreadable"] += 1
            rows.append({"id": case_id, "key": key, "status": "unreadable",
                         "note": f"Xray did not answer for it (HTTP {e.status})"})
            continue
        status, oc, xc = classify(entry, case, xray)
        if status == "same" and _describe_due(entry, case):
            status = "describe"
        counts[status] += 1
        base = entry.get("base") if isinstance(entry.get("base"), dict) else None
        rows.append({"id": case_id, "key": key, "status": status, "ours_changed": oc,
                     "xray_changed": xc, "imported": bool(entry.get("imported")),
                     "default": DEFAULT.get(status, ""),
                     "ours": shape(case["title"], case["steps"]),
                     "xray": shape(xray["title"], xray["steps"]),
                     "base": shape(base.get("title"), base.get("steps")) if base else None,
                     "editable": all(s.get("id") is not None for s in xray["steps"]) or mocked
                                 or status == "describe",
                     "description": case.get("describe") or [],
                     "xray_updated": xray.get("updated", ""), "last_by": xray.get("last_by", ""),
                     "last_at": xray.get("last_at", ""), "synced_updated": entry.get("synced_updated", ""),
                     "changed_after_sync": after(xray.get("updated"), entry.get("synced_updated"))})
    if not rows:
        raise Refused("no case of this job's design has an Xray test yet: Create in Xray first "
                      "(or take the story's Xray tests)")
    bits = [f"{counts[k]} {w}" for k, w in (("update", "to send"), ("describe", "with a Description to send"),
                                           ("xray_newer", "newer in Xray"),
                                           ("conflict", "changed on both sides"), ("same", "in step"),
                                           ("unreadable", "not readable")) if counts[k]]
    out = {"kind": "xray-update-plan", "at": _now(), "job": job, "mock": mocked, "tests": rows,
           "counts": counts, "ok": True,
           "message": f"{len(rows)} Xray test(s) compared: " + ", ".join(bits) + ". Nothing was sent."}
    with io.open(os.path.join(job_dir, PLAN), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    return out


def parse_choices(text: str) -> dict:
    out = {}
    for part in [p.strip() for p in str(text or "").split(",") if p.strip()]:
        m = _CHOICE_RX.match(part)
        if not m:
            raise Refused(f"{part!r} is not a choice like TC-001=send (send, take or skip)")
        out[m.group(1)] = m.group(2)
    return out


def _keep_backup(job_dir: str, key: str, xray: dict) -> None:
    path = os.path.join(job_dir, BACKUP)
    try:
        with io.open(path, encoding="utf-8") as fh:
            kept = json.load(fh)
    except (OSError, ValueError):
        kept = {"kind": "xray-update-backup", "tests": []}
    kept.setdefault("tests", []).append({"key": key, "at": _now(), "before": xray})
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(kept, fh, indent=1, ensure_ascii=False)


def _send(conn: dict, key: str, case: dict, xray: dict, entry: dict, transport) -> list:
    """Write ours over the test, step by step in place. Returns what was done."""
    done = []
    ours = shape(case["title"], case["steps"])
    fields = {}
    if _norm(xray["title"]) != ours["title"]:
        fields["summary"] = xt._one(case["title"], xt.MAX_SUMMARY)
    if _describes(entry, case) or (not entry.get("imported") and entry.get("story")):
        # A test the workbench made: its description is the workbench's too.
        # One taken from the story: its own, with the automation after it.
        fields["description"] = _description(entry, case, xray)
    if fields:
        search._call(conn, "PUT", f"/issue/{key}", {"fields": fields}, transport)
        done.append("summary" if "summary" in fields else "description")
    raven = dict(conn, api=STEP_API)
    have, want = xray["steps"], case["steps"]
    for i in range(max(len(have), len(want))):
        if i < len(have) and i < len(want):
            if shape("", [have[i]])["steps"] != shape("", [want[i]])["steps"]:
                search._call(raven, "POST", f"/test/{key}/step/{have[i]['id']}",
                             {"step": want[i]["action"], "data": want[i]["data"],
                              "result": want[i]["expected"]}, transport)
                done.append(f"step {i + 1} changed")
        elif i < len(want):
            search._call(raven, "PUT", f"/test/{key}/step",
                         {"step": want[i]["action"], "data": want[i]["data"],
                          "result": want[i]["expected"]}, transport)
            done.append(f"step {i + 1} added")
        else:
            search._call(raven, "DELETE", f"/test/{key}/step/{have[i]['id']}", None, transport)
            done.append(f"step {i + 1} removed")
    return done


def apply(job: str, choices: dict, root: str = "", conn=None, transport=None, cfg: dict = None,
          xray_cfg: dict = None) -> dict:
    root = root or xt.ROOT
    job_dir = loop.intake.job_dir(job, root)
    try:
        with io.open(os.path.join(job_dir, PLAN), encoding="utf-8") as fh:
            preview = json.load(fh)
    except (OSError, ValueError):
        raise Refused("there is no preview: press Update in Xray to see what would change first")
    conn, mocked, transport = _connect(root, conn, transport, xray_cfg)
    if bool(preview.get("mock")) != mocked:
        raise Refused("the preview was made against another Jira (mock or real) than this one: "
                      "preview again")
    shown = {r["id"]: r for r in preview.get("tests") or [] if isinstance(r, dict)}
    sending = [c for c, v in choices.items() if v == "send"]
    if sending and not xt.switch(cfg, mocked)["on"]:
        raise Refused("writing tests to Jira is switched off (Settings: Create in Xray). Taking "
                      "Xray's steps into the cases does not need it.")
    kept = xt.read_keys(job_dir)
    ours = _ours(job_dir, root)
    # What is sent -- steps, and a Description read from the code -- holds no
    # value of the private configuration (as Create in Xray checks).
    private = xt.publish.private_values(root)
    out = {"kind": "xray-update", "at": _now(), "job": job, "mock": mocked,
           "sent": [], "taken": [], "skipped": [], "failed": []}
    for case_id, choice in sorted(choices.items()):
        row = shown.get(case_id)
        ident = f"design:{case_id}"
        entry = kept["tests"].get(ident)
        if not row or not isinstance(entry, dict) or row.get("status") in ("same", "unreadable"):
            out["skipped"].append({"id": case_id, "why": "not in the preview as something to change"})
            continue
        if choice == "skip":
            out["skipped"].append({"id": case_id, "key": row["key"], "why": "skipped"})
            continue
        key, case = row["key"], ours.get(case_id)
        if not case:
            out["skipped"].append({"id": case_id, "key": key, "why": "the case is gone since the preview"})
            continue
        if shape(case["title"], case["steps"]) != row["ours"]:
            out["skipped"].append({"id": case_id, "key": key,
                                   "why": "the case was edited since the preview: preview again"})
            continue
        try:
            xray = read_xray(conn, key, transport, mocked)
        except search.JiraError as e:
            out["failed"].append({"id": case_id, "key": key, "why": f"Xray did not answer (HTTP {e.status})"})
            continue
        if shape(xray["title"], xray["steps"]) != row["xray"]:
            out["skipped"].append({"id": case_id, "key": key,
                                   "why": "the test was changed in Xray since the preview: preview again"})
            continue
        if choice == "take":
            casereview.update(job_dir, "design", [case_id], title=xray["title"] or case["title"],
                              steps=[{k: s[k] for k in ("action", "data", "expected")} for s in xray["steps"]])
            taken = dict(case, title=xray["title"] or case["title"],
                         steps=[{k: s[k] for k in ("action", "data", "expected")} for s in xray["steps"]])
            kept["tests"][ident] = dict(entry, base=snapshot(taken), fingerprint=xt.fingerprint(taken),
                                        synced=_now(), synced_updated=xray.get("updated", ""))
            xt.write_keys(job_dir, kept)
            out["taken"].append({"id": case_id, "key": key})
            continue
        if choice == "send":
            found = xt.publish.leaks([json.dumps(case, ensure_ascii=False)], private)
            if found:
                out["failed"].append({"id": case_id, "key": key,
                                      "why": "it holds a private value and is not sent: " + "; ".join(found[:3])})
                continue
        if row.get("status") == "describe":
            # The Description only: the steps are in step and stay as they are.
            if mocked:
                done = ["description (mock: nothing was contacted)"]
            else:
                diag.step(f"Updating the description of {key} in Xray")
                try:
                    search._call(conn, "PUT", f"/issue/{key}", {"fields": {
                        "description": _description(entry, case, xray)}}, transport)
                except search.JiraError as e:
                    out["failed"].append({"id": case_id, "key": key,
                                          "why": f"Jira refused the description (HTTP {e.status})"})
                    continue
                done = ["description"]
            kept["tests"][ident] = dict(entry, described=case.get("described", ""))
            xt.write_keys(job_dir, kept)
            out["sent"].append({"id": case_id, "key": key, "done": done})
            continue
        # send
        if not mocked and any(s.get("id") is None for s in xray["steps"]):
            out["failed"].append({"id": case_id, "key": key,
                                  "why": "Xray did not give the steps' ids, so they cannot be edited "
                                         "from here; change them in Jira"})
            continue
        _keep_backup(job_dir, key, xray)
        if mocked:
            done = ["(mock: nothing was contacted)"]
        else:
            diag.step(f"Updating {key} in Xray")
            try:
                done = _send(conn, key, case, xray, entry, transport)
            except search.JiraError as e:
                out["failed"].append({"id": case_id, "key": key,
                                      "why": f"Jira refused part of it (HTTP {e.status}); what Xray "
                                             f"had is in {BACKUP}. Preview again to see where it is"})
                continue
        kept["tests"][ident] = dict(entry, base=snapshot(case), fingerprint=xt.fingerprint(case),
                                    synced=_now(),
                                    synced_updated="" if mocked else updated_now(conn, key, transport),
                                    **({"described": case.get("described", "")}
                                       if _describes(entry, case) else {}))
        xt.write_keys(job_dir, kept)
        out["sent"].append({"id": case_id, "key": key, "done": done})
    out["ok"] = not out["failed"]
    out["message"] = (f"{len(out['sent'])} test(s) updated in Xray, {len(out['taken'])} case(s) "
                      f"took Xray's steps, {len(out['skipped'])} skipped"
                      + (f", {len(out['failed'])} failed" if out["failed"] else "") + "."
                      + (" MOCK: no Jira was contacted." if mocked else ""))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("plan", "apply"))
    ap.add_argument("--job", required=True)
    ap.add_argument("--choices", default="", help="apply: TC-001=send,TC-002=take,... (send, take, skip)")
    a = ap.parse_args(argv)
    try:
        job_dir = loop.intake.job_dir(a.job)
    except ValueError as e:
        print(f"REFUSED {e}")
        return 2
    name = PLAN if a.command == "plan" else RESULT
    try:
        if a.command == "plan":
            r = plan(a.job)
        else:
            r = apply(a.job, parse_choices(a.choices))
    except (Refused, search.Refused, search.JiraError) as e:
        print(f"REFUSED {e}")
        with io.open(os.path.join(job_dir, name), "w", encoding="utf-8") as fh:
            json.dump({"kind": "error", "ok": False, "message": str(e), "at": _now()}, fh)
        return 1
    if a.command == "apply":
        with io.open(os.path.join(job_dir, RESULT), "w", encoding="utf-8") as fh:
            json.dump(r, fh, indent=1, ensure_ascii=False)
        for s in r["sent"]:
            print(f" ok   {s['id']} -> {s['key']}: {', '.join(s['done']) or 'nothing differed'}")
        for s in r["taken"]:
            print(f" ok   {s['id']} took the steps of {s['key']}")
        for s in r["skipped"]:
            print(f" ..   {s['id']}: {s['why']}")
        for s in r["failed"]:
            print(f"FAIL  {s['id']} ({s['key']}): {s['why']}")
    else:
        for row in r["tests"]:
            print(f" ..   {row['id']} {row['key']}: {row['status']}")
    print((" ok  " if r.get("ok") else "WARN ") + r["message"])
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(diag.run("xrayupdate", main))
