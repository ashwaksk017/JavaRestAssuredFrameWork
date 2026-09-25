"""Data-driven fluent @Test emit helpers.

Every imported ReadyAPI case gets:
  - a typed record whose fields are the IDs/emails that case extracts
  - fluent chain methods named from that case's phase groups
  - verify helpers for trailing assertion-heavy GET steps
  - a short @Test that calls start → chain → complete → verify

Identical phase bodies are extracted onto framework-level
`com.hi.api.support.scenario.ScenarioSteps` so reuse spans every
imported suite, not just one XML. Fingerprints ignore suite folder
and template-hash suffixes. Per-case Support keeps only methods
whose choreography differs.

Nothing here is ticket-specific. B2B-7816 is one input shape, not a template.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Iterable

import phase_vocabulary

# Helpers that exist BOTH on the per-suite TestSupport and on the framework
# ImportedScenario. A phase body is written against TestSupport; when it is
# hoisted into framework ScenarioSteps the receiver is swapped (and swapped
# back for body-fingerprinting). Anything emitted as `TestSupport.<name>(`
# MUST be listed here or the hoisted copy will not compile -- that is how
# `putEnvScoped` broke the build when it was first added.
# Set by ra_converter from --phase-specs; gates naming changes that would
# otherwise move the default tree.
PHASE_SPECS = True

_DUAL_HOME_HELPERS = (
    "ctxGet",
    "putExtracted",
    "putIfNonEmpty",
    "mergedRow",
    "putEnvScoped",
    "envMap",
)



_SKIP_EXTRACT_SUBSTR = (
    "rawrequest", "dburl", "db_user", "dbuser", "dbpassword", "db_password",
    "db_driver", "driver", "alloweddomains", "generatedtoken", "chromedriver",
    "hws_username", "hws_password", "sql_query", "sqlquery",
)

_ID_LEAF = re.compile(
    r"(guestid|accountid|memberid|travelagentid|hilton.?member.?id)$", re.I)

_VERIFY_STATUS_ONLY = frozenset({
    "Valid HTTP Status Codes", "Invalid HTTP Status Codes", "Response SLA",
})


_JAVA_KEYWORDS = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new",
    "package", "private", "protected", "public", "return", "short", "static",
    "strictfp", "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while", "record", "var", "yield",
})


def java_ident(raw: str, fallback: str = "value") -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "", raw or "")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "s" + s
    if s in _JAVA_KEYWORDS:
        s = s + "Value"
    return s


def support_type_name(method_name: str) -> str:
    stem = method_name[:-4] if method_name.endswith("Test") else method_name
    ident = java_ident(stem, "ImportedFlow")
    ident = ident[0].upper() + ident[1:]
    return ident + "Support"


def record_name_for_case(case_name: str, method_name: str) -> str:
    """Derive a record type from the ReadyAPI case / method stem."""
    n = (case_name or "").lower()
    if "manually_attested" in n or "manually attested" in n:
        return "ManuallyAttestedH4LAccount"
    stem = method_name[:-4] if method_name.endswith("Test") else method_name
    ident = java_ident(stem, "ImportedScenario")
    ident = ident[0].upper() + ident[1:]
    if not ident.endswith("Scenario"):
        ident += "Scenario"
    return ident


def phase_to_fluent(phase: str | None) -> str | None:
    """Turn a PHASE banner into a camelCase fluent method, or _start."""
    if not phase:
        return None
    pl = phase.strip()
    low = pl.lower()
    if low.startswith("auth") or low == "identities":
        return "_start"

    known = {
        # POST /realms/guests/enroll -- guest enroll, not a role (owner / TA).
        "enroll honors guests": "enrollGuest",
        "enroll guest": "enrollGuest",
        "travel agency": "createTravelAgency",
        "create program account + confirm owner totp": "createProgramAccount",
        "create program account": "createProgramAccount",
        "confirm member totp": "confirmMemberTotp",
        "salesforce account + distribution (hws trigger)": "prepareSalesforceAccount",
        "salesforce account": "prepareSalesforceAccount",
        "salesforce token": "prepareSalesforceAccount",
        "salesforce distribution": "prepareSalesforceAccount",
        "read program account (pre-attest)": "readProgramAccountBeforeAttest",
        "read program account": "readProgramAccount",
        "activate program account": "activateProgramAccount",
        "update program account": "updateProgramAccount",
        "delete program account": "deleteProgramAccount",
        "manual attest in hws (readyapi launchhwssearchlead)": "activateThroughHws",
        "owner synced to salesforce": "readOwnerAfterSync",
        "account is active; invite lta; assert sf": "addAndActivateTravelAdvisor",
        "create account member": "createAccountMember",
        "list account members": "listAccountMembers",
        "read account member": "readAccountMember",
        "update account member": "updateAccountMember",
        "delete account member": "deleteAccountMember",
        "remove account member": "removeAccountMember",
        "activate account member": "activateAccountMember",
        "create email domain": "createEmailDomain",
        "list email domains": "listEmailDomains",
        "delete email domain": "deleteEmailDomain",
        "update member role": "updateMemberRole",
        "merge account member guest": "mergeAccountMemberGuest",
        "salesforce contact": "readSalesforceContact",
        "salesforce program member": "readSalesforceProgramMember",
    }
    stripped = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", low)).strip()
    if low in known:
        return known[low]
    if stripped in known:
        return known[stripped]

    words = [w for w in re.findall(r"[a-z0-9]+", stripped)
             if w not in {"the", "a", "an", "to", "in", "and", "or", "of",
                          "readyapi", "plus"}]
    if not words:
        return "runPhase"
    return words[0] + "".join(w[:1].upper() + w[1:] for w in words[1:8])


# Last-path-segment words that name the fluent method from the URL.
# Keep this list to operations whose ReadyAPI step titles invent a role
# the URL does not have. POST /realms/guests/enroll -> enrollGuest.
_PATH_ACTIONS = frozenset({
    "enroll",
})

# ReadyAPI step-name aliases that invented roles (MemberHHonorsEnroll ->
# travel advisor). Kept so a stale fluent_catalog.json cannot re-seed them.
RETIRED_FLUENT_NAMES = frozenset({
    "enrollOwner",
    "enrollHonorsGuests", "enrollHonorsGuests2", "enrollHonorsGuests3",
    "enrollTravelAdvisor", "enrollTravelAdvisor2",
    "enrollTravelAdvisor3", "enrollTravelAdvisor4",
})

# ImportedRestClient methods that belong on GuestApi / ProgramAccountApi /
# MemberApi. Converter emit uses these receivers; fingerprints treat
# guests.foo / accounts.foo / members.foo as client.foo so catalog votes
# stay stable across the rewrite.
_GUEST_CLIENT_METHODS = frozenset({
    "cancelGuestReservationStay", "createGuestReservation", "deleteGuest",
    "getPackages", "hHonorsEnroll", "httpRequest200EnrollGuest",
    "httpRequest200EnrollNewguest", "httpRequest200VerifyPackagesDontExist",
    "partnerEnrollGuestBusiness", "readAllEmails", "readHhonors",
    "readMemberProgramAccounts", "shopSingleProp",
    "updateGuestReservationStay", "updateHhonors",
})
_ACCOUNT_CLIENT_METHODS = frozenset({
    "activateProgramAccount", "attestProgramAccount", "compareProgramAccounts",
    "createEmailDomain", "createInviteLink", "createProgramAccount",
    "deleteEmailDomain", "deleteProgramAccount", "forceEnrollProgramAccount",
    "guestDeleteProgramAccount", "guestReadProgramAccount",
    "guestUpdateProgramAccount", "mergeProgramAccount", "patchProgramAccount",
    "readAccountBookerSpendMetrics", "readAccountRolePermissions",
    "readEmailDomains", "readProgramAccount", "rejectProgramAccount",
    "retrieveAccountBusinessProfile", "retrieveAccountMemberInvites",
    "retrieveClients", "searchAccountByInviteKey", "searchBookingPolicies",
    "searchProgramAccounts", "sendInvitationLink", "sendMemberInvites",
    "updateAccountCentralBillSummary", "updateAccountRolePermission",
    "updateBusinessProfile", "updateProgramAccount", "verifyProgramAccount",
})
_MEMBER_CLIENT_METHODS = frozenset({
    "activateProgramAccountMember", "createPartnerAccount",
    "createProgramAccountMember", "deleteMemberPermission",
    "deletePartnerAccount", "deleteProgramAccountMember",
    "guestSearchProgramAccountMembers", "mergeProgramAccountMember",
    "mergeProgramAccountMemberGuestProfile", "patchProgramAccountMember",
    "readAccountMemberPreferences", "readPartnerAccounts",
    "readProgramAccountMemberSpendDetail", "readProgramAccountMember",
    "readStayFromConfirmationNumber", "removeProgramAccountMember",
    "requestMemberValidation", "retrieveAccountMemberBusinessProfile",
    "retrieveAccountMemberPermissions", "retrieveAccountMemberRolePermissions",
    "retrieveGrantedAccessToOtherMembers", "retrieveOtherMembersGrantedAccess",
    "searchProgramAccountMembers", "updateAccountMemberPermission",
    "updateAccountMemberPermissions", "updateAccountMemberRolePermission",
    "updateEmployeeProfile", "updateMemberCentralBillSummary",
    "updateMemberEmailAddress", "updateMemberPhone", "updateMemberRole",
    "updateProgramAccountMember", "validateMemberTOTP",
})


def quote_catalog_delay_reason(text: str) -> str:
    """Catalog bodies may predate quoted Poller.delay reasons (10Sec)."""
    if not text or "Poller.delay(" not in text:
        return text

    def _repl(m: re.Match[str]) -> str:
        prefix, reason, suffix = m.group(1), m.group(2).strip(), m.group(3)
        if reason.startswith('"'):
            return m.group(0)
        escaped = reason.replace("\\", "\\\\").replace('"', '\\"')
        return f'{prefix}"{escaped}"{suffix}'

    return re.sub(
        r'(Poller\.delay\(\d+L,\s*)([^)]+)(\))',
        _repl,
        text,
    )


def domain_receiver(client_method: str) -> str:
    """Return guests / accounts / members / client for a generated client method."""
    n = client_method or ""
    if n in _GUEST_CLIENT_METHODS:
        return "guests"
    if n in _ACCOUNT_CLIENT_METHODS:
        return "accounts"
    if n in _MEMBER_CLIENT_METHODS:
        return "members"
    return "client"


def rewrite_client_to_domain(text: str) -> str:
    """client.foo( → guests.foo( / accounts.foo( / members.foo( when mapped."""
    if not text:
        return text

    def _repl(m: re.Match[str]) -> str:
        name = m.group(1)
        recv = domain_receiver(name)
        if recv == "client":
            return m.group(0)
        return f"{recv}.{name}("

    return re.sub(r"\bclient\.([A-Za-z_]\w*)\(", _repl, text)


def normalize_domain_calls(text: str) -> str:
    """guests.foo( / accounts.foo( / members.foo( → client.foo( for fingerprints."""
    if not text:
        return text
    return re.sub(
        r"\b(?:guests|accounts|members)\.([A-Za-z_]\w*)\(",
        r"client.\1(",
        text,
    )


def _path_tokens(path: str) -> list[str]:
    out: list[str] = []
    for s in (path or "").strip("/").split("/"):
        if not s:
            continue
        if (s.startswith("{") and s.endswith("}")) or (
                s.startswith("#") and s.endswith("#")):
            tok = re.sub(r"[^A-Za-z0-9]", "", s.strip("{}#")).lower()
        elif re.fullmatch(r"\d{6,}", s):
            continue
        else:
            tok = re.sub(r"[^A-Za-z0-9]+", "", s).lower()
        if tok:
            out.append(tok)
    return out


def _singular_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 1:
        return token[:-1]
    return token


def fluent_name_from_url(step) -> str | None:
    """Fluent method from HTTP verb + resource path, ignoring ReadyAPI step names.

    Action URLs use verb+noun (POST /realms/guests/enroll -> enrollGuest).
    Other URLs return None so PHASE banners (createProgramAccount) still apply;
    the converter's ``METHOD path`` fallback then becomes postBusinessesForcedenroll.
    """
    if type(step).__name__ != "RestStep":
        return None
    method = (getattr(step, "http_method", None) or "").strip().lower()
    path = getattr(step, "resource_path", None) or ""
    if not method or not path.strip():
        return None
    tokens = _path_tokens(path)
    if not tokens:
        return None
    last = tokens[-1]
    if last in _PATH_ACTIONS and len(tokens) >= 2:
        noun = _singular_token(tokens[-2])
        return last + noun[:1].upper() + noun[1:]
    return None


# A Groovy step that only talks to the database. Named from the SQL it
# runs, never from the ReadyAPI step title: this suite calls the same
# UPDATE "DBupdate", "DB update" and "Update account status_Limited".
_DB_SQL_RX = re.compile(
    r"(?is)\b(SELECT|UPDATE|INSERT\s+INTO|DELETE\s+FROM|MERGE\s+INTO)\b")
# Reading a REST response keeps the step fused: the response variable is
# a local of the REST step's method, so the SQL cannot move away from it.
_DB_RESPONSE_RX = re.compile(
    r"(?i)(#response#|messageExchange|responseContent|getResponseContent)")
_DB_UPDATE_RX = re.compile(
    r"(?is)\bUPDATE\s+([A-Za-z0-9_.]+)\s+SET\s+([A-Za-z0-9_]+)")
_DB_SELECT_RX = re.compile(r"(?is)\bSELECT\s+(.+?)\s+FROM\s+([A-Za-z0-9_.]+)")
_DB_DELETE_RX = re.compile(r"(?is)\bDELETE\s+FROM\s+([A-Za-z0-9_.]+)")
_DB_INSERT_RX = re.compile(r"(?is)\bINSERT\s+INTO\s+([A-Za-z0-9_.]+)")
# Long enough to stay readable, short enough that the @Test name cap does
# not have to truncate it afterwards.
_DB_NAME_MAX = 42


def _db_ident(raw: str) -> str:
    """`segment.account_member` -> `AccountMember`, `a.account_id` -> `AAccountId`.

    Everything that is not a letter or digit becomes a word boundary, so
    an aliased column cannot carry a dot into a Java identifier.
    """
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", raw or "") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def db_phase_name(script: str) -> str | None:
    """Fluent name for a DB-only Groovy script, or None to leave it fused.

    None means "not mine": no SQL, a response reference, or a statement
    shape this does not recognise. Every None keeps today's behaviour.
    """
    text = script or ""
    if not _DB_SQL_RX.search(text) or _DB_RESPONSE_RX.search(text):
        return None
    m = _DB_UPDATE_RX.search(text)
    if m:
        name = "db" + _db_ident(m.group(1)) + "Set" + _db_ident(m.group(2))
    else:
        m = _DB_SELECT_RX.search(text)
        if m:
            col = re.split(r"[,\s]", m.group(1).strip())[0]
            col = "All" if col.strip() == "*" else _db_ident(col)
            name = "dbRead" + col + "From" + _db_ident(m.group(2))
        else:
            m = _DB_DELETE_RX.search(text)
            if m:
                name = "dbDelete" + _db_ident(m.group(1))
            else:
                m = _DB_INSERT_RX.search(text)
                if not m:
                    return None
                name = "dbInsert" + _db_ident(m.group(1))
    if len(name) > _DB_NAME_MAX:
        # Trim on a word boundary so the tail stays a whole word.
        cut = name[:_DB_NAME_MAX]
        for i in range(len(cut) - 1, int(_DB_NAME_MAX * 0.6), -1):
            if cut[i].isupper():
                cut = cut[:i]
                break
        name = cut
    return name if re.fullmatch(r"[a-z][A-Za-z0-9]*", name) else None


def step_fluent_override(step) -> str | None:
    """Per-step fluent name for the cases a URL cannot describe.

    Deliberately NOT from ReadyAPI step titles: ``HHonorsEnroll`` and
    ``MemberHHonorsEnroll`` are both POST ``/realms/guests/enroll``, and
    mapping the latter to enrollTravelAdvisor was a one-suite role guess.

    A DB-only Groovy step is named from the SQL it runs instead, which is
    content, not an author's title. That name differs from the preceding
    REST phase, and a group breaks on a name change, so the database work
    lands in its own method rather than inside whichever call came first.
    """
    if type(step).__name__ != "GroovyStep":
        return None
    return db_phase_name(getattr(step, "script", None))


def has_payload_asserts(step) -> bool:
    if type(step).__name__ != "RestStep":
        return False
    for a in (getattr(step, "assertions", None) or []):
        if getattr(a, "disabled", False):
            continue
        if (getattr(a, "type", "") or "") not in _VERIFY_STATUS_ONLY:
            return True
    return False


def is_get(step) -> bool:
    return (getattr(step, "http_method", "") or "").upper() == "GET"


def is_mutating_rest(step) -> bool:
    if type(step).__name__ != "RestStep":
        return False
    return (getattr(step, "http_method", "") or "").upper() in {
        "POST", "PUT", "PATCH", "DELETE"}


def verify_bucket(step) -> str | None:
    """Return Insights / PartnerRelationships method id, or None."""
    if type(step).__name__ != "RestStep" or not is_get(step):
        return None
    if not has_payload_asserts(step):
        return None
    path = getattr(step, "resource_path", "") or ""
    nlow = (getattr(step, "step_name", "") or "").lower()
    if "/sobjects/Contact" in path:
        return ("PartnerRelationships",
                "verifySalesforceTravelAdvisor" if "member" in nlow
                else "verifySalesforceOwner")
    if "/sobjects/Program_Member" in path:
        return ("PartnerRelationships",
                "verifySalesforceTravelAdvisor" if "member" in nlow
                else "verifySalesforceOwner")
    if re.search(r"/businesses/\{", path) and "/members" not in path:
        return "Insights", "verifyProgramAccount"
    if "/members" in path:
        return "Insights", "verifyAccountMember"
    # The vocabulary names the endpoint from its FULL path; the old leaf
    # rule produced `verifyVerify` for GET /guests/{}/businesses/verify.
    # Under --phase-specs only, so the default tree does not move.
    if PHASE_SPECS:
        canon = phase_vocabulary.canonical_name("GET", path) or ""
        m = re.match(r"^[a-z]+(.*)$", canon)
        noun = m.group(1) if m and m.group(1) else ""
        if canon.startswith("verify"):
            return "Insights", canon
        if noun:
            return "Insights", "verify" + noun
    leaf = re.sub(r"[^A-Za-z0-9]+", " ", path.split("/")[-1] or "resource")
    words = [w for w in leaf.split() if w]
    meth = "verify" + "".join(w[:1].upper() + w[1:] for w in words[:4] or ["Response"])
    return "Insights", meth


def canonical_from_step(step) -> str | None:
    """Canonical business name from the step's FULL path, or None.

    Delegates to phase_vocabulary so the converter and the hand-authoring
    DSL agree on what each endpoint is called.
    """
    if type(step).__name__ != "RestStep":
        return None
    path = (getattr(step, "resource_path", None) or "").strip()
    if not path:
        return None
    verb = (getattr(step, "http_method", None) or "").strip()
    if not verb:
        return None
    return phase_vocabulary.canonical_name(verb, path)


def assign_fluent_name(step, phase: str | None) -> str | None:
    phase_name = phase_to_fluent(phase)
    if phase_name == "_start":
        return "_start"
    # Canonical vocabulary wins for REST steps. It reads the WHOLE path,
    # so a sub-resource cannot be swallowed by its parent -- the bug that
    # named 53 of 155 readProgramAccount* methods after the wrong
    # operation (13 of them POST /reject calls called "read").
    # PHASE banners and step titles remain the fallback for non-REST
    # steps and for anything the vocabulary has no rule for.
    canonical = canonical_from_step(step)
    if canonical:
        return canonical
    url_name = fluent_name_from_url(step)
    if url_name:
        return url_name
    override = step_fluent_override(step)
    if override:
        return override
    return phase_name


def group_flow_and_verify(steps: list, phase_of) -> tuple[list, list]:
    """Split steps into fluent groups; peel trailing GET+assert groups as verifies.

    phase_of(step) -> phase label or None (keep previous).
    HTTP order is preserved: verifies are only groups that are trailing
    (no mutating REST after them).
    """
    tagged: list[tuple[str, Any]] = []
    last_phase: str | None = None
    last_fluent = "_start"
    for step in steps:
        phase = phase_of(step)
        if phase:
            last_phase = phase
        name = assign_fluent_name(step, last_phase)
        if name is None:
            name = last_fluent
        elif (type(step).__name__ not in ("RestStep", "GroovyStep")
              and not step_fluent_override(step)):
            name = last_fluent
        last_fluent = name
        tagged.append((name, step))

    groups: list[tuple[str, list]] = []
    for name, step in tagged:
        # Split consecutive REST only when the name came from the URL
        # action (enrollGuest). PHASE-banner groups such as
        # prepareSalesforceAccount still keep every REST in that banner.
        is_action_rest = (
            type(step).__name__ == "RestStep"
            and fluent_name_from_url(step) is not None
        )
        already_has_action_rest = (
            groups
            and any(
                type(s).__name__ == "RestStep" and fluent_name_from_url(s)
                for s in groups[-1][1]
            )
        )
        if (not groups or groups[-1][0] != name
                or (is_action_rest and already_has_action_rest)):
            groups.append((name, [step]))
        else:
            groups[-1][1].append(step)

    verify_groups: list[tuple[str, str, list]] = []
    while groups:
        gname, gsteps = groups[-1]
        rest = [s for s in gsteps if type(s).__name__ == "RestStep"]
        if not rest:
            break
        if any(is_mutating_rest(s) for s in rest):
            break
        buckets = {verify_bucket(s) for s in rest if verify_bucket(s)}
        if len(buckets) != 1 or None in buckets:
            if not all(verify_bucket(s) for s in rest):
                break
            # multiple verify kinds in one group — don't peel
            break
        cls, meth = next(iter(buckets))
        verify_groups.append((cls, meth, gsteps))
        groups.pop()

    verify_groups.reverse()

    # Merge peeled verifies that share (class, method)
    merged_v: list[tuple[str, str, list]] = []
    for cls, meth, gsteps in verify_groups:
        if merged_v and merged_v[-1][0] == cls and merged_v[-1][1] == meth:
            merged_v[-1][2].extend(gsteps)
        else:
            merged_v.append((cls, meth, gsteps))

    flow = [(n, s) for n, s in groups if n != "_start" or s]
    start_steps = []
    flow_out: list[tuple[str, list]] = []
    for n, s in flow:
        if n == "_start":
            start_steps.extend(s)
            continue
        # Do not suffix here. Numbered names are allocated only when the
        # rendered body is new (method_body_reuse.FluentMethodReuse).
        flow_out.append((java_ident(n, "runPhase"), s))
    uniq_v: list[tuple[str, str, list]] = []
    for cls, meth, gsteps in merged_v:
        uniq_v.append((cls, java_ident(meth, "verifyStep"), gsteps))
    return (start_steps, flow_out, uniq_v)


def collect_extract_keys(case, steps: Iterable) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add(key: str):
        if not key or key in seen:
            return
        if not keep_extract_key(key):
            return
        seen.add(key)
        keys.append(key)

    for step in list(getattr(case, "steps", []) or []) + list(steps or []):
        if type(step).__name__ == "TransferStep":
            for t in (getattr(step, "transfers", None) or []):
                tgt_step = t.get("target_step", "") or ""
                tgt_path = t.get("target_path", "") or ""
                add(f"{tgt_step}.{tgt_path}" if tgt_path else tgt_step)
        if type(step).__name__ == "RestStep":
            sid = re.sub(r"[^A-Za-z0-9_]", "_", getattr(step, "step_name", "") or "step")
            for a in (getattr(step, "assertions", None) or []):
                path = ""
                cfg = getattr(a, "config", None) or {}
                path = cfg.get("path", "") or cfg.get("jsonPath", "") or ""
                if path and re.search(r"(guestId|accountId|memberId|salesforceId|email)", path, re.I):
                    leaf = path.split(".")[-1].replace("['", "").replace("']", "")
                    add(f"{sid}_Response_{leaf}")

    return keys


def field_name_from_key(key: str) -> str:
    if "_Response_" in key:
        leaf = key.split("_Response_", 1)[-1]
    else:
        leaf = key.split(".")[-1]
    leaf = leaf.replace("-", "_")
    parts = [p for p in re.split(r"[._]", leaf) if p]
    if not parts:
        return "value"
    name = parts[0][:1].lower() + parts[0][1:]
    for p in parts[1:]:
        name += p[:1].upper() + p[1:]
    name = re.sub(r"ID$", "Id", name)
    if name.endswith("id") and not name.endswith("Id"):
        name = name[:-2] + "Id"
    return java_ident(name, "value")


def java_type_for_key(key: str) -> str:
    low = key.lower()
    leaf = key.split(".")[-1] if "." in key else key
    if "email" in low:
        return "String"
    if "salesforce" in low or "sfid" in low or "leadid" in low:
        return "String"
    if "website" in low or "domain" in low or "token" in low:
        return "String"
    if _ID_LEAF.search(leaf.replace("_", "")) or _ID_LEAF.search(low.replace("_", "")):
        return "Long"
    if leaf.lower().endswith("id") or leaf.lower().endswith("_id"):
        if "salesforce" in low or "contact" in low or "program" in low:
            return "String"
        return "Long"
    return "String"


def fields_from_keys(keys: list[str]) -> list[tuple[str, str, list[str]]]:
    """Merge extract keys into unique (field, javaType, sourceKeys)."""
    by_field: dict[str, tuple[str, list[str]]] = {}
    used: set[str] = set()
    for key in keys:
        fname = field_name_from_key(key)
        jtype = java_type_for_key(key)
        if fname in by_field:
            existing_type, existing_keys = by_field[fname]
            if key not in existing_keys:
                existing_keys.append(key)
            continue
        # disambiguate if we already used this name from a different leaf
        candidate = fname
        n = 2
        while candidate in used:
            prefix = java_ident(key.split(".")[0].split("_")[0], "alt")
            candidate = prefix[:1].lower() + prefix[1:] + fname[:1].upper() + fname[1:]
            if candidate in used:
                candidate = fname + str(n)
                n += 1
        used.add(candidate)
        by_field[candidate] = (jtype, [key])
    return [(n, t, ks) for n, (t, ks) in by_field.items()]


def story_for_case(case) -> str:
    desc = (getattr(case, "description", None) or "").strip()
    first = desc.split("\n")[0].strip() if desc else ""
    if ":" in first and first.lower().startswith("scenario"):
        return first.split(":", 1)[1].strip()[:90]
    n = (getattr(case, "name", None) or "Imported scenario").replace("_", " ")
    return n[:90]


def description_lines(case) -> list[str]:
    raw = (getattr(case, "description", None) or "").strip()
    if not raw:
        return ["Imported from ReadyAPI."]
    lines = [ln.strip() for ln in raw.replace("\r", "").split("\n") if ln.strip()]
    out = []
    for ln in lines[:8]:
        out.append(ln.replace("\\", "\\\\")[:140])
    return out or ["Imported from ReadyAPI."]


def rewrite_response_to_fields(lines: list[str]) -> list[str]:
    return [re.sub(r"^Response (\w+) = ", r"this.\1 = ", ln) for ln in lines]


def collect_response_fields(lines: list[str]) -> list[str]:
    names: list[str] = []
    for ln in lines:
        for rx in (r"^this\.(\w+) = ", r"^Response (\w+) = "):
            m = re.match(rx, ln)
            if m and m.group(1) not in names:
                names.append(m.group(1))
    return names


def collect_response_refs(lines: list[str]) -> list[str]:
    """Assignments plus any *Res identifier a shared body reads."""
    names = collect_response_fields(lines)
    for ln in lines:
        for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*Res)\b", ln):
            if m.group(1) not in names:
                names.append(m.group(1))
    return names


def phase_body_key(lines: list[str]) -> str:
    """Stable fingerprint of a rendered fluent-method body.

    Suite folder names and content-hash suffixes on template paths are
    stripped so every imported suite votes as one codebase when the
    HTTP choreography matches.
    """
    text = "\n".join((ln or "").rstrip() for ln in lines)
    text = re.sub(r"templates/[A-Za-z0-9_]+/", "templates/*/", text)
    text = re.sub(r"_[a-f0-9]{8,12}\.json", "_*.json", text)
    text = re.sub(r"\bImportedTemplates\.get\(\"([A-Z0-9_]+)\"\)",
                  r"Templates.\1", text)
    for _h in _DUAL_HOME_HELPERS:
        text = text.replace("ImportedScenario." + _h, "TestSupport." + _h)
    text = re.sub(
        r'ImportedScenario\.runSetup\("([^"]+)",\s*',
        r"SetupHelper.\1(",
        text)
    text = normalize_domain_calls(text)
    return text


def suite_agnostic_body(lines: list[str]) -> list[str]:
    """Rewrite a phase body so it can live in framework ScenarioSteps."""
    out: list[str] = []
    for ln in lines:
        ln = re.sub(
            r"(?<!Imported)\bTemplates\.([A-Z0-9_]+)\b",
            r'ImportedTemplates.get("\1")',
            ln)
        for _h in _DUAL_HOME_HELPERS:
            ln = ln.replace("TestSupport." + _h, "ImportedScenario." + _h)
        ln = re.sub(
            r"\bSetupHelper\.(\w+)\(",
            r'ImportedScenario.runSetup("\1", ',
            ln)
        ln = rewrite_client_to_domain(ln)
        ln = quote_catalog_delay_reason(ln)
        out.append(ln)
    return out


def catalog_path(output_dir: str = ".") -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "fluent_catalog.json")


def emitter_version() -> str:
    """Fingerprint of the code that GENERATES phase bodies.

    The catalog does not merely record which phases exist -- it caches their
    rendered Java. So a bug fixed in the translator does not reach the output
    until the cached body is discarded, and the user sees their fix silently
    fail to take effect.

    That happened: a faulty date recognizer stored Java declaring a
    DateTimeFormatter as String. Removing the recognizer changed nothing,
    because three catalog phases kept replaying the broken body -- same
    compile errors, no explanation in the log.

    `vocabularyVersion` could not catch it: the vocabulary was unchanged.
    This hashes the emit sources instead, so ANY converter change rebuilds
    the bodies it could have altered.
    """
    import hashlib
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha1()
    for name in ("groovy_translator.py", "ra_converter.py", "fluent_scenario.py"):
        p = os.path.join(here, name)
        try:
            with open(p, "rb") as fh:
                h.update(fh.read())
        except OSError:
            # Missing source: fall back to the name so the hash still changes
            # if a file appears or disappears.
            h.update(name.encode("utf-8"))
    return h.hexdigest()[:12]


def _empty_catalog() -> dict:
    return {"phases": {}, "verifies": {}, "bootstrap": None, "suites": {},
            "clientMethods": [], "shapes": {},
            "vocabularyVersion": phase_vocabulary.version(),
            "emitterVersion": emitter_version()}


def load_fluent_catalog() -> dict:
    path = catalog_path()
    if not os.path.isfile(path):
        return _empty_catalog()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty_catalog()

    # Vocabulary-versioned invalidation.
    #
    # `phases` and `verifies` are keyed BY PHASE NAME, so a catalog written
    # under an older vocabulary re-seeds the names that vocabulary produced
    # -- 751 of 798 entries were counter-suffixed (readProgramAccount776),
    # and reusing them would silently undo a vocabulary change. The stamp is
    # a hash of the rule table, so it bumps ITSELF whenever the vocabulary
    # is edited; nobody has to remember to purge the file.
    #
    # `clientMethods` is keyed by client signature, not phase name, so it
    # survives -- discarding it would needlessly churn generated clients.
    # The emit code changed -> cached Java bodies may be stale. Rebuild them
    # rather than replay Java produced by a converter that no longer exists.
    current_emitter = emitter_version()
    if data.get("emitterVersion") != current_emitter:
        kept = data.get("clientMethods", [])
        kept_shapes = data.get("shapes", {}) or {}
        stale = len(data.get("phases", {}) or {})
        data = _empty_catalog()
        data["clientMethods"] = kept
        data["shapes"] = kept_shapes
        print(f"[fluent_catalog] converter changed -- discarded {stale} cached "
              f"phase bodies so the new emit takes effect")
        return data

    current = phase_vocabulary.version()
    # A catalog written by a run that had failing suites holds votes computed
    # from partial data. Inheriting those makes the next convert's output
    # neither correct nor reproducible, and nothing in the log explains the
    # difference -- so treat it exactly like a vocabulary bump and rebuild.
    if data.get("incomplete"):
        kept = data.get("clientMethods", [])
        kept_shapes = data.get("shapes", {}) or {}
        stale = len(data.get("phases", {}) or {})
        reason = data.get("incompleteReason") or []
        data = _empty_catalog()
        data["clientMethods"] = kept
        data["shapes"] = kept_shapes
        print(f"[fluent_catalog] previous run did not complete "
              f"({', '.join(str(r) for r in reason[:3])}) -- discarded {stale} "
              f"phase entries computed from partial data; rebuilding")
        return data
    if data.get("vocabularyVersion") != current:
        kept = data.get("clientMethods", [])
        stale = len(data.get("phases", {}) or {})
        data = _empty_catalog()
        data["clientMethods"] = kept
        print(f"[fluent_catalog] vocabulary changed -- discarded {stale} stale "
              f"phase entries (clientMethods kept); rebuilding from this run")
        return data

    data.setdefault("phases", {})
    data.setdefault("verifies", {})
    data.setdefault("bootstrap", None)
    data.setdefault("suites", {})
    data.setdefault("clientMethods", [])
    data.setdefault("shapes", {})
    data["vocabularyVersion"] = current
    return data


def save_fluent_catalog(data: dict) -> str:
    path = catalog_path()
    tmp = path + ".tmp"
    # The catalog is saved from several places in one run, each from its
    # own in-memory copy. Merging the call-shape registry HERE means every
    # save carries it; merging in one caller let a later save that had
    # loaded the file earlier write the key back empty (which is exactly
    # what happened the first time).
    try:
        from phase_model import RUN_SPECS, ShapeRegistry
        if RUN_SPECS:
            reg = ShapeRegistry(data.get("shapes"))
            for suite, specs in RUN_SPECS.items():
                reg.merge(specs.values(), suite)
            data["shapes"] = reg.to_dict()
            n_specs = sum(len(v) for v in RUN_SPECS.values())
            n_setup = sum(1 for v in RUN_SPECS.values() for s in v.values() if s.setup)
            print(f"[fluent_catalog] call shapes: {len(data['shapes'])} "
                  f"(from {n_specs} captured REST step(s), {n_setup} in SetupHelper)")
        else:
            print("[fluent_catalog] call shapes: no REST step captured in this process")
    except Exception as exc:  # the catalog must still be written
        print(f"[fluent_catalog] shape registry not merged: {exc}")
    payload = json.dumps(data, indent=2) + "\n"
    last_err = None
    for attempt in range(5):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)
            return path
        except OSError as exc:
            last_err = exc
            time.sleep(0.2 * (attempt + 1))
    raise last_err


def keep_extract_key(key: str) -> bool:
    if not key:
        return False
    low = key.lower()
    if any(s in low for s in _SKIP_EXTRACT_SUBSTR):
        return False
    return True


def extract_keys_from_java(lines: list[str]) -> list[str]:
    keys = []
    for ln in lines:
        for m in re.finditer(r'putExtracted\(\s*ctx,\s*"([^"]+)"', ln):
            if keep_extract_key(m.group(1)):
                keys.append(m.group(1))
    return keys
