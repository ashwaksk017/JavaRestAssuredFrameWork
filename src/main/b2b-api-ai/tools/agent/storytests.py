"""What a hand-written story test (tests/jira) must look like before a
change that adds one can be reviewed: converter-style parity -- the class
Javadoc DATA_INVENTORY, the enroll -> guestId extract, csv/manual/ sheets,
templates on disk -- and the house shape: the jira group, the inherited
softAssert, ManualBody bodies, jira.* configuration keys, no customer data
or secret in a committed file.

Only the new-test flow of loop.py uses it; the converter has its own
checks (tools/ra_converter, tools/verify_all.py).
"""
from __future__ import annotations

import io
import os
import re


def _read(path: str, cap: int = 60000) -> str:
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(cap)
    except OSError:
        return ""


_JIRA_TESTS_DIR = "src/test/java/com/hi/api/tests/jira/"
_MANUAL_BODY_TEMPLATE_RX = re.compile(r'templates/manual/[A-Za-z0-9_./-]+\.json')
_TEMPLATE_HASH_RX = re.compile(r'#([A-Za-z0-9_]+)#')
_ROW_OR_CONFIG_COL_RX = re.compile(
    r'rowOrConfig(?:Linked)?\(\s*[^,]*,\s*"([A-Za-z0-9_]+)"')
_SPEND_DETAIL_RX = re.compile(r'spendDetail|/spendDetail', re.I)
# The columns StoryTestBase.fillEnrollFields generates for each guest a test enrols.
GENERATED_ENROLL_COLUMNS = ("enroll_username", "enroll_password", "owner_company_email",
                            "account_email_domain", "enroll_source_code", "enroll_prop_code")
_ACTOR_CALL_RX = re.compile(r'\b(?:enrollActor|enrollGuest|fillEnrollFields|createAccountFor)\s*\(')
# A person read from configuration: a login, or a person's own id. Seen live:
# a story test skipped with "no value for 'username': set jira.<story>.
# travel_coordinator.username" -- a key nobody had set. The converted B2B
# suites never do it: they enrol every person (B2B_FLOWS.md).
# Any string literal ENDING in a person's field, in a file that names a jira.*
# key: a whole key ("jira.x.owner.username") and one built by concatenation
# ("jira." + JOB + ".owner.username") alike -- seen in a committed test.
_PERSON_FIELDS = r"(username|password|guest_id|member_id|account_id)"
_PERSON_KEY_RX = re.compile(r'"[^"\n]*\.' + _PERSON_FIELDS + r'"')
# rowOrConfig(row, "<a person's field>", ...) -- the column names one too.
_PERSON_COLUMN_RX = re.compile(
    r'\b(?:optional|truthy)?[rR]owOrConfig\w*\(\s*[^,]*,\s*"' + _PERSON_FIELDS + r'"')
_PERSON_LOGIN_RX = re.compile(r'\b(?:ownerSession|personaSession)\s*\(')
# A credential or identity written into a request body as a fixed value.
_BODY_FIXED_RX = re.compile(r'"(password|username)"\s*:\s*"([^"]*)"')
_TOKEN_GUEST_ID_EXTRACT_RX = re.compile(
    r'ownerSession\s*\(|guestSession\s*\(|requireGuestId\s*\(|\.guestId\b'
    r'|TravelCoordinatorAuth|guestIdFromTokenResponse|guestIdFromAccessTokenClaims')


def _csv_columns_for_jira_class(root: str, cls: str, java_text: str) -> set:
    """Union of CSV header columns for row-driven @Test methods on this class."""
    cols = set(_ROW_OR_CONFIG_COL_RX.findall(java_text or ""))
    cols.update({"description", "test_case_id", "execute", "jira_xray_id", "jira_issue"})
    if _ACTOR_CALL_RX.search(java_text or ""):
        # enrollActor / enrollGuest fill these per guest (StoryTestBase.fillEnrollFields).
        cols.update(GENERATED_ENROLL_COLUMNS)
    if "PerMethodCsvDataProvider" not in (java_text or ""):
        return cols
    for m in re.finditer(
            r"public\s+void\s+(\w+)\s*\(\s*Map\s*<\s*String\s*,\s*String\s*>\s*row\s*\)",
            java_text or ""):
        csv_path = os.path.join(
            root, "src", "test", "resources", "csv", "manual", cls,
            m.group(1) + ".csv")
        if not os.path.isfile(csv_path):
            continue
        head = _read(csv_path, 4000).split("\n", 1)[0]
        delim = ";" if head.count(";") > head.count(",") else ","
        cols.update(h.strip() for h in head.split(delim) if h.strip())
    return cols


def _jira_template_placeholder_issues(root: str, norm: str, text: str, csv_cols: set) -> list:
    """Each #column# in a ManualBody template must appear in CSV or rowOrConfig columns."""
    issues = []
    for tpl in sorted(set(_MANUAL_BODY_TEMPLATE_RX.findall(text or ""))):
        tpl_path = os.path.join(root, "src", "test", "resources", tpl.replace("/", os.sep))
        if not os.path.isfile(tpl_path):
            continue
        tpl_text = _read(tpl_path, 200_000)
        for ph in sorted(set(_TEMPLATE_HASH_RX.findall(tpl_text))):
            if ph in csv_cols:
                continue
            issues.append(
                f"{norm}: template {tpl} uses #{ph}# but no csv/manual/ column or "
                f"rowOrConfig(..., \"{ph}\", ...) on this class (MANUAL_FIXES.md §5)")
    return issues


def _jira_spend_detail_guest_id_issues(norm: str, text: str) -> list:
    """Path guestId on spendDetail (no enroll in class) should come from token, not config-only."""
    if not _SPEND_DETAIL_RX.search(text or ""):
        return []
    if "guests/enroll" in text or "realms/guests/enroll" in text:
        return []
    if not re.search(r'rowOrConfig(?:Linked)?\([^,]+,\s*"guest_id"', text or ""):
        return []
    if _TOKEN_GUEST_ID_EXTRACT_RX.search(text or ""):
        return []
    return [f"{norm}: GET …/spendDetail uses rowOrConfig(..., \"guest_id\", …) for the caller's "
            "guest id: create the person and take the id from the response -- enrollActor(...).guestId "
            "(skill recipe R2, B2B_FLOWS.md)"]


# StoryTestBase, or a project's base built on it (GoalStoryTestBase).
_EXTENDS_BASE_RX = re.compile(r"\bextends\s+(?:[\w.]+\.)?(?:Goal)?StoryTestBase\b")
_GOAL_TEST_RX = re.compile(r"\bextends\s+(?:[\w.]+\.)?GoalStoryTestBase\b")
_DATE_LITERAL_RX = re.compile(r'"(20\d\d-[01]\d-[0-3]\d)"')


def _goal_issues(norm: str, code: str) -> list:
    """A GOAL test finds its property and dates (findAvailability): a date
    written into the source is a frozen one -- seen in the converted CSVs,
    last-run dates that later fail when the date has passed."""
    if not _GOAL_TEST_RX.search(code):
        return []
    dates = sorted(set(_DATE_LITERAL_RX.findall(code)))
    if not dates:
        return []
    return [f"{norm}: a fixed date in a GOAL test ({', '.join(dates[:3])}): dates come from "
            "findAvailability (av.arrivalDate / av.departureDate) or from LocalDate.now() "
            "(GOAL_FLOWS.md section 4)"]


# A date a call is made FOR (a stay, a meeting, a window): written down, it
# passes and the test starts failing. The converted bodies hold such dates
# (arrivalDate, startDate, meetingDate), and the data sheets their last-run
# ones. Not a date that is history: a fiscal year start, a birth date, a
# record's created/modified date.
_DATE_VALUE_RX = re.compile(r"^(20\d\d-[01]\d-[0-3]\d)([T ][0-2]\d:[0-5]\d(:[0-5]\d)?\S*)?$")
_DATED_FIELD_RX = re.compile(r"(?i)(date|start|end|from|until|arrival|departure|check_?in|check_?out)[^.]*$")
_HISTORY_FIELD_RX = re.compile(r"(?i)fiscal|birth|dob|creat|modif|expir|issued|last|since|enrol")
_STALE_AFTER_DAYS = 730          # an older date is history (a birth date), not one a call is made for


def _date_of(value: str):
    """The date `value` is, when it is one a call could be made for; None."""
    from datetime import date, timedelta
    m = _DATE_VALUE_RX.match(str(value or "").strip())
    if not m:
        return None
    try:
        d = date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return d if d >= date.today() - timedelta(days=_STALE_AFTER_DAYS) else None


def _fixed_date_issues(root: str, norm: str, code: str, text: str, cls: str) -> list:
    """A fixed stay/meeting/window date in a story test of any project: in
    its Java (GOAL has its own message, _goal_issues), in a body template it
    renders, or in a dated column of its data sheets."""
    issues = []
    goal = bool(_GOAL_TEST_RX.search(code))
    fix = ("dates come from findAvailability (av.arrivalDate / av.departureDate) or LocalDate.now() "
           "(GOAL_FLOWS.md section 4)" if goal else
           "compute it in the test -- LocalDate.now().plusDays(n) -- and put it in the row (or ctx) "
           "before the body is rendered, so a template sends it as \"#name#\"")
    if not goal:
        dates = sorted({d for d in _DATE_LITERAL_RX.findall(code) if _date_of(d)})
        if dates:
            issues.append(f"{norm}: a fixed date ({', '.join(dates[:3])}): it passes, and the test starts "
                          f"failing then -- {fix}")
    for tpl in sorted(set(_MANUAL_BODY_TEMPLATE_RX.findall(text or ""))):
        body = _read(os.path.join(root, "src", "test", "resources", tpl.replace("/", os.sep)), 200_000)
        fixed = sorted({f"{k} = {v}" for k, v in _body_values(body).items()
                        if isinstance(v, str) and _date_of(v) and _DATED_FIELD_RX.search(k)
                        and not _HISTORY_FIELD_RX.search(k)})
        if fixed:
            issues.append(f"{tpl}: a fixed date ({'; '.join(fixed[:3])}), copied from a converted body: it "
                          f"passes, and every test that sends it starts failing then -- send \"#name#\" and {fix}")
    # A GOAL row's arrival/departure are the search's (render puts av's over the row's).
    searched = {"arrivalDate", "departureDate"} if goal else set()
    for rel, rows in _class_sheets(root, cls):
        cols = sorted({c for row in rows for c, v in row.items()
                       if c and c not in searched and _DATED_FIELD_RX.search(c)
                       and not _HISTORY_FIELD_RX.search(c) and _date_of(v)})
        if cols:
            issues.append(f"{rel}: a fixed date in column(s) {', '.join(cols[:4])}: a data sheet's dates "
                          f"pass, and its rows start failing then -- leave the cell empty and {fix}")
    return issues


def _class_sheets(root: str, cls: str) -> list:
    """[(rel, rows as dicts)] of a story test class's data sheets
    (csv/manual/<cls>/*.csv)."""
    import csv as _csv
    out = []
    if not cls:
        return out
    base = os.path.join(root, "src", "test", "resources", "csv", "manual", cls)
    try:
        names = sorted(n for n in os.listdir(base) if n.endswith(".csv"))
    except OSError:
        return out
    for n in names:
        sheet = _read(os.path.join(base, n), 400_000).lstrip("﻿")
        head = sheet.split("\n", 1)[0]
        delim = ";" if head.count(";") > head.count(",") else ","
        try:
            rows = [r for r in _csv.DictReader(io.StringIO(sheet), delimiter=delim)
                    if any((v or "").strip() for v in r.values() if isinstance(v, str))]
        except _csv.Error:
            continue
        out.append((f"src/test/resources/csv/manual/{cls}/{n}", rows))
    return out


_EXPECTED_STATUS_COL_RX = re.compile(r"(?i)^expected\w*status")


def _row_expects_refusal(row: dict) -> bool:
    """A row whose expected status is a 4xx: it sends a refused value on purpose."""
    return any(_EXPECTED_STATUS_COL_RX.match(k or "") and str(v or "").strip()[:1] == "4"
               for k, v in row.items())


def _cell_value(cell: str):
    """A data-sheet cell as the body sends it: a number, true/false, else text."""
    s = str(cell or "").strip()
    if re.fullmatch(r"-?\d{1,9}", s):
        return int(s)
    if s in ("true", "false"):
        return s == "true"
    return s


def _jira_person_issues(norm: str, code: str) -> list:
    """A person logged in or identified from configuration (see _PERSON_KEY_RX)."""
    issues = []
    if _PERSON_LOGIN_RX.search(code):
        issues.append(f"{norm}: logs in as a person set up in configuration (ownerSession): create the "
                      "person instead -- enrollActor / addMember / setMemberRole, ids from the "
                      "responses (skill section 2, B2B_FLOWS.md)")
    keys = sorted({m.group(0).strip('"') for m in _PERSON_KEY_RX.finditer(code)}) \
        if '"jira.' in code else []
    keys += sorted({f"column {m.group(1)}" for m in _PERSON_COLUMN_RX.finditer(code)})
    if keys:
        issues.append(f"{norm}: reads a person from configuration ({', '.join(keys[:4])}): nobody sets "
                      "these keys, so the test skips -- create the person (enrollActor, addMember, "
                      "setMemberRole) and take its ids from the responses (B2B_FLOWS.md)")
    return issues


def _template_fixed_value_issues(root: str, norm: str, text: str) -> list:
    """A request body with a fixed password or username: an enrolment must
    send the generated #enroll_password# / #enroll_username#, which
    enrollActor then logs in with (a converted body has a fixed password)."""
    issues = []
    for tpl in sorted(set(_MANUAL_BODY_TEMPLATE_RX.findall(text or ""))):
        body = _read(os.path.join(root, "src", "test", "resources", tpl.replace("/", os.sep)), 200_000)
        for field, value in _BODY_FIXED_RX.findall(body):
            v = value.strip()
            if v and not (v.startswith(("#", "${", "<<")) and v.endswith(("#", "}", ">>"))) \
                    and not _FAKE_SECRET_RX.match(v):
                issues.append(f"{tpl}: \"{field}\" is a fixed value -- use \"#enroll_{field}#\" "
                              "(generated per guest by enrollActor, which logs in with it)")
    return issues


_NUMBER_PLACEHOLDER_RX = re.compile(r"[@%][A-Za-z0-9_.]+[@%]")
_CONVERTED_SHAPES: dict = {}


def _json_fields(node, pre: str = "") -> set:
    """Every field path of a JSON body: a.b, list items as a[].b."""
    out = set()
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{pre}.{k}" if pre else k
            out.add(p)
            out |= _json_fields(v, p)
    elif isinstance(node, list):
        for v in node:
            out |= _json_fields(v, pre + "[]")
    return out


def _body_fields(text: str):
    """A template's field paths, or None when it is not a JSON object (an
    unquoted @n@ / %n% number placeholder is read as a number)."""
    import json as _json
    try:
        j = _json.loads(_NUMBER_PLACEHOLDER_RX.sub("0", text))
    except ValueError:
        return None
    return _json_fields(j) - {"_comment"} if isinstance(j, dict) else None


_VALUE_PLACEHOLDER_RX = re.compile(r"#[^#\s]+#|\$\{[^}]+\}|<<[^<>]+>>|@[A-Za-z0-9_.]+@|%[A-Za-z0-9_.]+%")
_MAX_ENUM_VALUES = 6       # a field whose converted bodies send more values than this is free text


def _body_values(text: str) -> dict:
    """{field path: the literal it sends} of a template's leaves -- numbers,
    true/false, text; a placeholder ("#x#", @n@, <<x>>) is not a literal."""
    import json as _json
    try:
        j = _json.loads(_NUMBER_PLACEHOLDER_RX.sub('"#n#"', text))
    except ValueError:
        return {}
    out = {}

    def walk(node, pre):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{pre}.{k}" if pre else k)
        elif isinstance(node, list):
            for v in node:
                walk(v, pre + "[]")
        elif node is not None and pre and pre != "_comment":
            if isinstance(node, str) and (not node.strip() or _VALUE_PLACEHOLDER_RX.search(node)):
                return
            out.setdefault(pre, node)
    walk(j, "")
    return out


def _body_holes(text: str) -> dict:
    """{field path: column} of a template's leaves that are one placeholder
    and nothing else -- "#name#", or @name@ / %name% for a number."""
    import json as _json
    try:
        j = _json.loads(re.sub(r"[@%]([A-Za-z0-9_.]+)[@%]", r'"#\1#"', text))
    except ValueError:
        return {}
    out = {}

    def walk(node, pre):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{pre}.{k}" if pre else k)
        elif isinstance(node, list):
            for v in node:
                walk(v, pre + "[]")
        elif isinstance(node, str) and pre:
            m = re.fullmatch(r"#([A-Za-z0-9_]+)#", node.strip())
            if m:
                out.setdefault(pre, m.group(1))
    walk(j, "")
    return out


def _converted_shapes(root: str) -> list:
    """(path, fields, values) of every converted request body
    (src/main/resources/templates/**), read once a run."""
    base = os.path.join(root, "src", "main", "resources", "templates")
    if root not in _CONVERTED_SHAPES:
        shapes = []
        for d, _dirs, files in os.walk(base):
            for f in files:
                if f.endswith(".json"):
                    p = os.path.join(d, f)
                    text = _read(p, 400_000)
                    fields = _body_fields(text)
                    if fields:
                        shapes.append((os.path.relpath(p, base).replace(os.sep, "/"), fields, _body_values(text)))
        _CONVERTED_SHAPES[root] = shapes
    return _CONVERTED_SHAPES[root]


def _shown(v) -> str:
    import json as _json
    s = _json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 40 else s[:37] + '..."'


# "business", "mobile", "individual", "PROGRAMACCOUNTS" -- not "Memphis" (a
# name), not "TN" / "US" (a place), not "38103" or "a@b.c" (free text).
_ENUM_WORD_RX = re.compile(r"^([a-z][A-Za-z_-]{0,30}|[A-Z][A-Z_]{2,30})$")


def _checked_value(v) -> bool:
    """A value the server checks against a set: a number, true/false, an
    enum word. Free text -- a name, a street, a postal code, an e-mail
    address -- differs from body to body and is not compared."""
    return isinstance(v, (bool, int, float)) or (isinstance(v, str) and bool(_ENUM_WORD_RX.match(v)))


def _converted_values(root: str) -> dict:
    """{field path: every literal any converted body sends there}, as JSON."""
    import json as _json
    out: dict = {}
    for _p, _f, vals in _converted_shapes(root):
        for k, v in vals.items():
            out.setdefault(k, set()).add(_json.dumps(v))
    return out


_ON_PURPOSE = "a test of a refused value on purpose reads it from its data sheet as \"#name#\""
_ON_PURPOSE_ROW = ("a row that sends a refused value on purpose says so: its expected status column "
                   "holds the 4xx the server answers")


def _template_value_issues(root: str, tpl: str, mine: dict, alike: list, on_purpose: str = _ON_PURPOSE) -> list:
    """A field the template sends a fixed value for that no converted body
    sends there, where the converted bodies of the same call agree on a few
    values (an enum, a range code, a flag). Seen live: the story
    create-account body sent businessProfile.employeeCountRange 0, copied
    from a reference example that wrote every number as 0; 79 converted
    bodies send 1, and the server answered 400 (code 31, Invalid JSON
    Parameter Value) to every test. Not said: free text, fields fewer than
    three of them send as a literal, fields they send more than six values
    for, and a value any converted body sends there (a flag the bodies of a
    call set both ways is the scenario's choice)."""
    import json as _json
    issues = []
    anywhere = _converted_values(root)
    for field, value in sorted(mine.items()):
        if not _checked_value(value):
            continue
        theirs = [vals[field] for _p, _f, vals in alike if field in vals]
        if len(theirs) < 3:                  # a range code "501-1000" among them is still an enum
            continue
        key = _json.dumps(value)
        if key in anywhere.get(field, ()):
            continue
        counts: dict = {}
        for v in theirs:
            counts[_json.dumps(v)] = counts.get(_json.dumps(v), 0) + 1
        if len(counts) > _MAX_ENUM_VALUES:
            continue
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        sends = ", ".join(f"{_shown(_json.loads(k))} ({n})" for k, n in ranked[:3])
        example = next(p for p, _f, vals in alike if field in vals and _json.dumps(vals[field]) == ranked[0][0])
        issues.append(f"{tpl}: sends {field} = {_shown(value)}, which no converted body of the same call "
                      f"sends -- {len(theirs)} of them send {sends} (e.g. src/main/resources/templates/{example}); "
                      f"the server may refuse it (400, the field named). Send what they send; {on_purpose}")
    return issues


def _sheet_value_issues(root: str, cls: str, tpl: str, body: str, alike: list) -> list:
    """The values a template's "#name#" fields take from the class's data
    sheets, checked as a literal in the template is: moving a refused value
    into a cell (as the check's own message suggests for a value tested on
    purpose) must not move it out of sight. A row expecting a 4xx is that
    test, and is left alone. One issue per sheet and field."""
    holes = _body_holes(body)
    issues, seen = [], set()
    if not holes:
        return issues
    for rel, rows in _class_sheets(root, cls):
        for line, row in enumerate(rows, start=2):
            if _row_expects_refusal(row):
                continue
            cells = {path: _cell_value(row[col]) for path, col in holes.items()
                     if str(row.get(col) or "").strip()}
            cells = {p: v for p, v in cells.items() if (rel, p) not in seen}
            for issue in _template_value_issues(root, f"{rel} line {line} (into {tpl})", cells, alike,
                                                _ON_PURPOSE_ROW):
                seen.add((rel, issue.split(" sends ", 1)[1].split(" = ", 1)[0]))
                issues.append(issue)
    return issues


# addMember(member, accountId, ROLE, ...), setMemberRole(owner, accountId, member, ROLE, ...):
# the helpers write the body themselves, so no template shows the role.
_ROLE_ARG = {"addMember": 2, "setMemberRole": 3}


def _call_args(code: str, start: int) -> list:
    """The top-level arguments of the call whose "(" is at `start`."""
    args, depth, cur, quote = [], 0, "", ""
    for ch in code[start + 1:start + 2000]:
        if quote:
            cur += ch
            if ch == quote and not cur.endswith("\\" + quote):
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                args.append(cur.strip())
                return args
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
            continue
        cur += ch
    return args


def _member_role_issues(root: str, norm: str, code: str, cls: str) -> list:
    """The role a test hands addMember / setMemberRole -- written in the
    test, or read from a data-sheet column -- checked against the roles the
    converted bodies send (employee, travelAdvisor, admin, owner, ...): an
    invented one ("travel_coordinator") compiles and the server refuses it."""
    import json as _json
    sent = set()
    for k, vals in _converted_values(root).items():
        if k == "role" or k.endswith(".role"):
            sent |= {_json.loads(v) for v in vals}
    sent = {v for v in sent if isinstance(v, str) and _checked_value(v)}
    if len(sent) < 2:
        return []
    # An ADD (the body with an inviteKey) is made as employee or
    # travelAdvisor in every converted suite; admin, owner, travelCoordinator
    # are set after it, with the PUT (setMemberRole).
    added = {v.get("role") for _p, f, v in _converted_shapes(root) if "inviteKey" in f}
    added = {r for r in added if isinstance(r, str) and _checked_value(r)}
    allowed = {"addMember": added if len(added) >= 1 else sent, "setMemberRole": sent}
    found = []                                   # (where, value)
    sheets = None
    for m in re.finditer(r"\b(addMember|setMemberRole)\s*\(", code):
        args = _call_args(code, m.end() - 1)
        i = _ROLE_ARG[m.group(1)]
        if len(args) <= i:
            continue
        arg = args[i]
        lit = re.fullmatch(r'"([^"\\]*)"', arg)
        col = re.search(r'(?:row\.get|[rR]owOrConfig\w*)\(\s*(?:row\s*,\s*)?"([A-Za-z0-9_]+)"', arg)
        if lit:
            found.append((f"{norm}: {m.group(1)}(..., \"{lit.group(1)}\", ...)", lit.group(1), m.group(1)))
        elif col:
            sheets = _class_sheets(root, cls) if sheets is None else sheets
            for rel, rows in sheets:
                for line, row in enumerate(rows, start=2):
                    v = str(row.get(col.group(1)) or "").strip()
                    if v and not _row_expects_refusal(row):
                        found.append((f"{rel} line {line} (column {col.group(1)}, to {m.group(1)})", v,
                                      m.group(1)))
    issues, said = [], set()
    for where, v, helper in found:
        ok = allowed[helper]
        if v in ok or not _checked_value(v) or (helper, v) in said:
            continue
        said.add((helper, v))
        if helper == "addMember" and v in sent:
            issues.append(f"{where}: role \"{v}\" -- the converted suites add a member only as "
                          f"{', '.join(sorted(ok))}, and give {v} after it with setMemberRole (the PUT). "
                          f"Add as {sorted(ok)[0]}, activateMember, then setMemberRole(..., \"{v}\", ...)")
            continue
        issues.append(f"{where}: role \"{v}\" -- no converted body sends it; they send "
                      f"{', '.join(sorted(ok))}. The server may refuse it (400, role named); send one of "
                      f"those, or, to test a refused role on purpose, expect the 4xx")
    return issues


def _template_drift_issues(root: str, norm: str, text: str) -> list:
    """A hand-written body that leaves out a field the converted suites'
    bodies of the same call always send. Seen live: the story enrolment had
    a business address without "company", and every enrolment was answered
    400 (notification 503, fields [company]) -- all 127 converted enrolments
    send it. "The same call": converted bodies sharing at least half the
    template's fields (Jaccard >= 0.5), three or more of them; "always": 90%
    of them. No converted suites here: nothing to compare, nothing said."""
    issues = []
    shapes = _converted_shapes(root)
    if not shapes:
        return issues
    for tpl in sorted(set(_MANUAL_BODY_TEMPLATE_RX.findall(text or ""))):
        body = _read(os.path.join(root, "src", "test", "resources", tpl.replace("/", os.sep)), 200_000)
        mine = _body_fields(body)
        if not mine:
            continue
        alike = [(p, f, v) for p, f, v in shapes if len(mine & f) / max(1, len(mine | f)) >= 0.5]
        if len(alike) < 3:
            continue
        issues.extend(_template_value_issues(root, tpl, _body_values(body), alike))
        issues.extend(_sheet_value_issues(root, os.path.basename(norm)[:-5], tpl, body, alike))
        counts: dict = {}
        for _p, f, _v in alike:
            for k in f:
                counts[k] = counts.get(k, 0) + 1
        missing = sorted(k for k, n in counts.items() if k not in mine and n >= 0.9 * len(alike))
        # a missing object's own children say nothing new
        missing = [k for k in missing if not any(k.startswith(m + ".") or k.startswith(m + "[]")
                                                 for m in missing if m != k)]
        if missing:
            example = next(p for p, f, _v in alike if all(m in f for m in missing[:1]))
            issues.append(f"{tpl}: leaves out {', '.join(missing[:8])}"
                          + (f" (and {len(missing) - 8} more)" if len(missing) > 8 else "")
                          + f" -- {len(alike)} converted bodies of the same call send "
                          + ("it" if len(missing) == 1 else "them")
                          + f" (e.g. src/main/resources/templates/{example}); the server may refuse the body "
                          "without them. Add them: a harmless value as the converted body has it, anything "
                          "per environment or company-specific as \"#name#\" (read from the data sheet, the "
                          "configuration, else what the converted bodies send)")
    return issues


def _csv_cell_count_issues(rel: str, sheet: str) -> list:
    """Every row has as many cells as the header (quotes respected). Seen
    live: a row of 14 cells under a header of 13 -- the data provider
    dropped the extra cell and warned, and nothing failed."""
    import csv as _csv
    reader = _csv.reader(io.StringIO(sheet.lstrip("\ufeff")), delimiter=",")
    width, bad = None, []
    try:
        for cells in reader:
            if not any(c.strip() for c in cells):
                continue
            if width is None:
                width = len(cells)
            elif len(cells) > width:          # a short row is padded; a long one loses cells
                bad.append((reader.line_num, len(cells)))
    except _csv.Error:
        return [f"{rel}: the sheet cannot be read as CSV"]
    if not bad:
        return []
    return [f"{rel}: line {bad[0][0]} has {bad[0][1]} cells, the header {width}"
            + (f" (and {len(bad) - 1} more line(s))" if len(bad) > 1 else "")
            + " -- the data provider drops the extra cells; one cell per header column"]


_TEST_ANNOTATION_RX = re.compile(r"@(?:org\.testng\.annotations\.)?Test\b(\s*\(([^()]|\([^()]*\))*\))?")
_GROUPS_RX = re.compile(r"groups\s*=\s*(\{[^}]*\}|\"[^\"]*\")")
_CONFIG_LITERAL_RX = re.compile(r'rowOrConfig(?:Linked)?\(\s*[^,]+,\s*"[A-Za-z0-9_]+"\s*,\s*"([^"]+)"\s*\)')
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_SAFE_EMAIL_RX = re.compile(r"@(example\.(com|org|net)|[a-z0-9-]+\.(test|invalid|example))$", re.I)
_OTHER_PLACEHOLDER_RX = re.compile(r'"[^"\n]*(@[A-Za-z_][A-Za-z0-9_]*@|%[A-Za-z_][A-Za-z0-9_]*%|\$\{#[^}]+\})[^"\n]*"')
_SECRET_COLUMNS = ("password", "client_secret", "secret", "api_key", "token")
# A negative case sends a deliberately wrong one: not a secret.
_FAKE_SECRET_RX = re.compile(r"(?i)^(invalid|expired|bogus|dummy|fake|wrong|bad|revoked|malformed|"
                             r"none|null|empty|blank|x+)\b")


def _jira_shape_issues(root: str, norm: str, text: str, cls: str) -> list:
    """What a hand-written test must not do here, beyond the converter's
    parity: the group the jira suite runs, the base class's SoftAssert,
    raw body mapping, the config key scheme, ReadyAPI placeholders left in
    a template, and customer data or secrets in the committed files."""
    issues = []
    for m in _TEST_ANNOTATION_RX.finditer(text):
        args = m.group(1) or ""
        g = _GROUPS_RX.search(args)
        if not g or '"jira"' not in g.group(1):
            issues.append(f"{norm}: an @Test without groups = {{\"jira\", ...}}: testng-jira.xml and the "
                          "group suites run by group, so this test is never run")
            break
    if re.search(r"\bsoftAssert\s*\.\s*assertAll\s*\(", text):
        issues.append(f"{norm}: softAssert.assertAll() in the test: BaseApiTest calls it after each test "
                      "(@AfterMethod); calling it here reports failures twice and stops the row early")
    if re.search(r"\bnew\s+SoftAssert\s*\(", text):
        issues.append(f"{norm}: new SoftAssert() hides BaseApiTest's softAssert, whose failures are the "
                      "ones reported: use the inherited softAssert")
    if re.search(r"\bmapJsonValues\s*\(", text):
        issues.append(f"{norm}: a raw mapJsonValues(...) body: <<faker>> and ${{ref}} tokens are not resolved "
                      "and a missing #column# is sent as \"null\"; use ManualBody.render(template, row, ctx)")
    if re.search(r"\bApiRoutes\s*\.\s*fill\s*\(", text):
        issues.append(f"{norm}: ApiRoutes.fill(...) in the test: an empty id is sent as /businesses//... and "
                      "the server's 404 does not say which; use route(ApiRoutes.X, \"name\", value), which "
                      "stops the test naming the empty id")
    for key in _CONFIG_LITERAL_RX.findall(text):
        if not key.startswith("jira."):
            issues.append(f"{norm}: config key \"{key}\" is not jira.<story>.<object>.<field>; a story's "
                          "values live under jira.* in program_configuration.json")
    for email in sorted(set(_EMAIL_RX.findall(text))):
        if not _SAFE_EMAIL_RX.search(email):
            issues.append(f"{norm}: an email address in the source ({email.split('@')[0][:2]}…@…): customer "
                          "data is never committed -- put it in program_configuration.json")
            break
    for tpl in sorted(set(_MANUAL_BODY_TEMPLATE_RX.findall(text))):
        tpl_text = _read(os.path.join(root, "src", "test", "resources", tpl.replace("/", os.sep)), 200_000)
        left = _OTHER_PLACEHOLDER_RX.search(tpl_text)
        if left:
            issues.append(f"{tpl}: {left.group(1)} is not a placeholder ManualBody fills (it fills #column#, "
                          "${ref} and <<faker>>): it would be sent as written")
    base = os.path.join(root, "src", "test", "resources", "csv", "manual", cls)
    for dirpath, _dirs, files in os.walk(base):
        for name in sorted(files):
            if not name.endswith(".csv"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
            sheet = _read(os.path.join(dirpath, name), 400_000)
            rows = [r for r in sheet.splitlines() if r.strip()]
            if not rows:
                continue
            issues.extend(_csv_cell_count_issues(rel, sheet))
            delim = ";" if rows[0].count(";") > rows[0].count(",") else ","
            head = [h.strip().lower() for h in rows[0].split(delim)]
            for email in sorted(set(_EMAIL_RX.findall(sheet))):
                if not _SAFE_EMAIL_RX.search(email):
                    issues.append(f"{rel}: an email address in a committed data sheet: customer data goes in "
                                  "program_configuration.json (leave the cell empty; rowOrConfig falls back)")
                    break
            secret_cols = [i for i, h in enumerate(head) if h in _SECRET_COLUMNS]
            for row in rows[1:]:
                cells = row.split(delim)
                for i in secret_cols:
                    v = cells[i].strip() if i < len(cells) else ""
                    if len(v) >= 8 and not v.startswith(("${", "<<", "#")) \
                            and not _FAKE_SECRET_RX.match(v):
                        issues.append(f"{rel}: column `{head[i]}` holds a value: a secret is never committed "
                                      "-- leave it empty so rowOrConfig reads program_configuration.json")
                        break
                else:
                    continue
                break
    return issues


# Helpers StoryTestBase (src/test/java/com/hi/api/tests/StoryTestBase.java)
# has ONCE. Seen in the generated story tests: each wrote them again -- five
# copies of the token request, three of the guest id from the token.
_SHARED_HELPERS = (
    (r"fetch\w*Token|(?:get|guest\w*|owner\w*|member\w*|application\w*|client\w*|coordinator\w*)AccessToken"
     r"|\w*TokenUrl|memberPathAuth|\w*(?:Coordinator|Owner|Member|Guest|Persona)Auth",
     "a token: serviceToken() / clientToken() / an enrolled actor's accessToken"),
    (r"guestIdFrom\w*", "a guest id: enrollActor(...).guestId, from the enrol response"),
    (r"goalToken\w*|goalBase|findAvailability|createGroup\w*|payGroup|confirmGroup",
     "GOAL's steps: goalToken / findAvailability / createGroup / payGroup / confirmGroup "
     "(GoalStoryTestBase, GOAL_FLOWS.md)"),
    (r"serviceToken\w*|enrollActor|createAccountFor|ownerMemberId|inviteKey|addMember|activateMember"
     r"|activateAccount|setMemberRole|confirmOwnerValidation|requireDb",
     "the people a test needs: enrollActor / createAccountFor / addMember / activateMember / "
     "setMemberRole / confirmOwnerValidation (B2B_FLOWS.md)"),
    (r"optionalRowOrConfig\w*|rowOrConfig\w*|configValueOrAlternate|truthyRowOrConfig",
     "a value from the row or the configuration: rowOrConfig / optionalRowOrConfig "
     "(each with a fallbackKey form) / truthyRowOrConfig"),
    (r"apiHeaders|clientCredentialsHeaders|(?:api|auth|bearer|default|build)\w*Headers",
     "API headers: apiHeaders(token)"),
    (r"fillEnrollFields|enroll(?:Guest|Owner|Member|NewGuest)?|allowedDomain\w*",
     "a guest: enrollActor(row, template, CASE)"),
    (r"call|step", "an Allure step around a call: step(name, () -> ...)"),
)
# A method declaration: modifiers (any, or none), a return type, a name, "(".
# Not a statement: "return x(", "new X(", "throw new X(" are not declarations.
_METHOD_DEF_RX = re.compile(
    r"^[ \t]*((?:(?:private|protected|public|static|final|synchronized)\s+)*)"
    r"(?!return\b|new\b|throw\b|else\b|case\b)[A-Za-z_][\w<>\[\],.? ]*?\s+(\w+)\s*\(", re.M)
_RAW_TOKEN_RX = re.compile(
    r"client_secret|api_config\.client_|token_end_point|token_route|ApiRoutes\.TOKEN|"
    r"realms/applications/token|getUrlDecoder")
# A story about the token end point itself says so, and may call it.
_TOKEN_UNDER_TEST_RX = re.compile(r"workbench:\s*token end point under test", re.I)
_COMMENT_RX = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _strip_comments(text: str) -> str:
    """The code without its comments: a Javadoc that NAMES the token route
    (DATA_INVENTORY) is not a test calling it. String literals holding // (a
    URL) lose their tail, which no check here reads."""
    return _COMMENT_RX.sub("", text or "")


def _shared_helper_issues(norm: str, text: str) -> list:
    """A story test extends StoryTestBase and writes none of its helpers.
    Seen in review: a @Test method named after what it tests
    (rejectsExpiredAccessToken, spendDetail_withoutAuth) is not a helper,
    nor is a public method of another type (Callable.call)."""
    issues = []
    has_test = bool(_TEST_ANNOTATION_RX.search(text))
    if has_test and not _EXTENDS_BASE_RX.search(text):
        issues.append(f"{norm}: a story test extends StoryTestBase (com.hi.api.tests), which has the "
                      "token, guest id, headers, row/config and enrolment helpers -- see the skill, "
                      "section 2")
    code = _strip_comments(text)
    seen = set()
    for m in _METHOD_DEF_RX.finditer(code):
        modifiers, name = m.group(1) or "", m.group(2)
        if "public" in modifiers or name in seen:
            continue
        # A declaration's "(...)" is followed by "{" (or throws ... {), a
        # call's by ";", "," or ")": read on to the matching ")".
        depth, i = 0, m.end() - 1
        while i < len(code):
            depth += {"(": 1, ")": -1}.get(code[i], 0)
            if depth == 0:
                break
            i += 1
        if not re.match(r"\s*(?:throws\s+[\w.,\s]+)?\{", code[i + 1:i + 200]):
            continue
        seen.add(name)
        before = code[max(0, code.rfind("}", 0, m.start()), code.rfind(";", 0, m.start())):m.start()]
        if "@Test" in before or "@org.testng.annotations.Test" in before:
            continue
        for rx, use in _SHARED_HELPERS:
            if re.fullmatch(rx, name):
                issues.append(f"{norm}: defines its own {name}(...); StoryTestBase has it -- use {use}")
                break
    if not _TOKEN_UNDER_TEST_RX.search(text) and _RAW_TOKEN_RX.search(code):
        issues.append(f"{norm}: requests or decodes a token itself; use StoryTestBase's "
                      "serviceToken() / clientToken(), or an enrolled actor's accessToken. "
                      "A story about the token end point itself says so in a comment: "
                      "// workbench: token end point under test")
    return issues


def _head_text(root: str, norm: str) -> str:
    """The file as the last commit has it, or "" when it is new."""
    import subprocess
    try:
        r = subprocess.run(["git", "show", "HEAD:./" + norm], cwd=root, capture_output=True,
                           timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def _new_shared_helper_issues(root: str, norm: str, text: str) -> list:
    """The shared-helper issues of a touched file. A story test from before
    StoryTestBase (its committed version does not extend it) is not failed
    for the helpers it already had -- touching one line of it must not
    demand a migration -- only for what this change adds; and it is told."""
    now = _shared_helper_issues(norm, text)
    base = _head_text(root, norm)
    if not base or _EXTENDS_BASE_RX.search(base):
        return now
    had = set(_shared_helper_issues(norm, base))
    added = [i for i in now if i not in had and "a story test extends StoryTestBase" not in i]
    return [i + " (this test is from before StoryTestBase: what it already had is left; "
                "migrate it to StoryTestBase when it is next reworked)" for i in added]


def _new_person_issues(root: str, norm: str, code: str) -> list:
    """The person issues of a touched test that this change adds: a test
    from before (its committed version already read a person from
    configuration) is not failed for touching one line of it."""
    now = _jira_person_issues(norm, code)
    base = _head_text(root, norm)
    if not base:
        return now
    had = set(_jira_person_issues(norm, _strip_comments(base)))
    return [i for i in now if i not in had]


_HEADERS_FILE_RX = re.compile(r'Headers\.fromJson\(\s*"headers/[^"]*"')


def _new_headers_file_issues(root: str, norm: str, code: str) -> list:
    """A fixed headers file (Headers.fromJson("headers/qa.json")): one
    environment's demo headers sent to every environment. A test that
    already had it when committed is not failed for it."""
    if not _HEADERS_FILE_RX.search(code) or _HEADERS_FILE_RX.search(_strip_comments(_head_text(root, norm))):
        return []
    return [f"{norm}: reads a fixed headers file ({_HEADERS_FILE_RX.search(code).group(0)})): its "
            "headers go to every environment -- use apiHeaders(token) (StoryTestBase), which builds them "
            "for the environment the run is on"]


def _project_base_issues(norm: str, code: str, project: str) -> list:
    """A GOAL job's test extends GoalStoryTestBase; a B2B job's does not --
    each skips under the other project's configuration, so the wrong base
    is a test that never runs."""
    if not project or not _EXTENDS_BASE_RX.search(code):
        return []
    goal = bool(_GOAL_TEST_RX.search(code))
    if project == "goal" and not goal:
        return [f"{norm}: this is a GOAL job: the test extends GoalStoryTestBase (GOAL_FLOWS.md), "
                "not StoryTestBase -- a B2B test skips under GOAL's configuration"]
    if project != "goal" and goal:
        return [f"{norm}: this is a {project.upper()} job: the test extends StoryTestBase, not "
                "GoalStoryTestBase -- a GOAL test skips under this project's configuration"]
    return []


def jira_parity_issues(root: str, rel_paths: list, project: str = "") -> list:
    """Converter-parity checks on touched jira tests (DATA_INVENTORY, enroll extract, CSV).
    `project`: the job's (b2b, goal) -- its tests' base class must be that project's."""
    issues = []
    for rel in sorted(set(rel_paths or [])):
        norm = rel.replace("\\", "/")
        if not norm.startswith(_JIRA_TESTS_DIR) or not norm.endswith(".java"):
            continue
        path = os.path.join(root, norm.replace("/", os.sep))
        text = _read(path, 500_000)
        if not text.strip():
            continue
        if not norm.endswith("Test.java"):
            # A helper class beside the tests: where shared helpers get copied to.
            issues.extend(_new_shared_helper_issues(root, norm, text))
            continue
        if "DATA_INVENTORY" not in text:
            issues.append(f"{norm}: missing class Javadoc DATA_INVENTORY (skill §2b — HTTP steps, "
                          "CONFIG keys, EXTRACTs)")
        code = _strip_comments(text)
        if ("realms/guests/enroll" in code or "/guests/enroll" in code
                or "ApiRoutes.GUEST_ENROLL" in code) and "enrollGuest(" not in code:
            if "safeJsonExtract" not in code or "guestId" not in code:
                issues.append(f"{norm}: enroll setup must EXTRACT guestId from the enroll response "
                              "-- use StoryTestBase's enrollActor(row, template, CASE), which does")
        issues.extend(_jira_spend_detail_guest_id_issues(norm, text))
        cls = os.path.basename(norm)[:-5]
        issues.extend(_new_person_issues(root, norm, code))
        issues.extend(_goal_issues(norm, code))
        issues.extend(_fixed_date_issues(root, norm, code, text, cls))
        issues.extend(_project_base_issues(norm, code, project))
        issues.extend(_template_fixed_value_issues(root, norm, text))
        issues.extend(_template_drift_issues(root, norm, text))
        issues.extend(_member_role_issues(root, norm, code, cls))
        issues.extend(_new_headers_file_issues(root, norm, code))
        issues.extend(_new_shared_helper_issues(root, norm, text))
        issues.extend(_jira_shape_issues(root, norm, text, cls))
        csv_cols = _csv_columns_for_jira_class(root, cls, text)
        # Any template the test names -- handed to a helper (enrollActor,
        # createGroup) as much as rendered here: a misspelt path compiles.
        for tpl in sorted(set(_MANUAL_BODY_TEMPLATE_RX.findall(code))):
            tpl_path = os.path.join(root, "src", "test", "resources", tpl.replace("/", os.sep))
            if not os.path.isfile(tpl_path):
                issues.append(f"{norm}: template missing on disk: src/test/resources/{tpl}")
        if "ManualBody.render" in text:
            issues.extend(_jira_template_placeholder_issues(root, norm, text, csv_cols))
        if "PerMethodCsvDataProvider" not in text:
            continue
        for m in re.finditer(
                r"public\s+void\s+(\w+)\s*\(\s*Map\s*<\s*String\s*,\s*String\s*>\s*row\s*\)",
                text):
            method = m.group(1)
            csv_rel = f"src/test/resources/csv/manual/{cls}/{method}.csv"
            csv_path = os.path.join(root, csv_rel.replace("/", os.sep))
            if not os.path.isfile(csv_path):
                issues.append(f"{norm}: missing data sheet {csv_rel} for @Test {method}")
                continue
            head = _read(csv_path, 4000).split("\n", 1)[0]
            for col in ("test_case_id", "jira_issue", "jira_xray_id"):
                if col not in head:
                    issues.append(f"{csv_rel}: header must include `{col}` (skill group A)")
    return issues
