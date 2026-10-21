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
_TOKEN_GUEST_ID_EXTRACT_RX = re.compile(
    r'ownerSession\s*\(|guestSession\s*\(|requireGuestId\s*\(|\.guestId\b'
    r'|TravelCoordinatorAuth|guestIdFromTokenResponse|guestIdFromAccessTokenClaims')


def _csv_columns_for_jira_class(root: str, cls: str, java_text: str) -> set:
    """Union of CSV header columns for row-driven @Test methods on this class."""
    cols = set(_ROW_OR_CONFIG_COL_RX.findall(java_text or ""))
    cols.update({"description", "test_case_id", "execute", "jira_xray_id", "jira_issue"})
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
            "guest id: a token EXTRACT, not config -- ownerSession(row, prefix).requireGuestId() "
            "(skill recipe R2)"]


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
    for key in _CONFIG_LITERAL_RX.findall(text):
        if not key.startswith("jira."):
            issues.append(f"{norm}: config key \"{key}\" is not jira.<story>.<role>.<field>; a story's "
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
     "a token: ownerSession(row, prefix) / guestSession(user, password) / clientToken()"),
    (r"guestIdFrom\w*", "the guest id of a token: ownerSession(...).requireGuestId()"),
    (r"optionalRowOrConfig\w*|rowOrConfig\w*|configValueOrAlternate|truthyRowOrConfig",
     "a value from the row or the configuration: rowOrConfig / optionalRowOrConfig "
     "(each with a fallbackKey form) / truthyRowOrConfig"),
    (r"apiHeaders|clientCredentialsHeaders|(?:api|auth|bearer|default|build)\w*Headers",
     "API headers: apiHeaders(token)"),
    (r"fillEnrollFields|enroll(?:Guest|Owner|Member|NewGuest)?|allowedDomain\w*",
     "a guest: enrollGuest(row, template, CASE) / fillEnrollFields(row)"),
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
    if has_test and not re.search(r"\bextends\s+(?:[\w.]+\.)?StoryTestBase\b", text):
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
                      "ownerSession / guestSession / clientToken (the guest id comes with the token). "
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
    if not base or re.search(r"\bextends\s+(?:[\w.]+\.)?StoryTestBase\b", base):
        return now
    had = set(_shared_helper_issues(norm, base))
    added = [i for i in now if i not in had and "a story test extends StoryTestBase" not in i]
    return [i + " (this test is from before StoryTestBase: what it already had is left; "
                "migrate it to StoryTestBase when it is next reworked)" for i in added]


def jira_parity_issues(root: str, rel_paths: list) -> list:
    """Converter-parity checks on touched jira tests (DATA_INVENTORY, enroll extract, CSV)."""
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
                              "-- use StoryTestBase's enrollGuest(row, template, CASE), which does")
        issues.extend(_jira_spend_detail_guest_id_issues(norm, text))
        issues.extend(_new_shared_helper_issues(root, norm, text))
        cls = os.path.basename(norm)[:-5]
        issues.extend(_jira_shape_issues(root, norm, text, cls))
        csv_cols = _csv_columns_for_jira_class(root, cls, text)
        if "ManualBody.render" in text:
            for tpl in _MANUAL_BODY_TEMPLATE_RX.findall(text):
                tpl_path = os.path.join(root, "src", "test", "resources", tpl.replace("/", os.sep))
                if not os.path.isfile(tpl_path):
                    issues.append(f"{norm}: template missing on disk: src/test/resources/{tpl}")
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
