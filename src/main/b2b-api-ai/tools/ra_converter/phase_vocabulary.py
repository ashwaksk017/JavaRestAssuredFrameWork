"""Canonical business vocabulary for fluent scenario steps.

One place that answers: "what is this HTTP call called, in business
language?" Both the converter (emitting imported suites) and the
hand-authoring DSL resolve names through here, so an imported test and a
hand-written test call the same phase the same thing.

WHY THIS EXISTS
---------------
The previous naming path derived a phase from the first two path
segments, with a catch-all ``/businesses/{`` rule. That rule swallowed 21
distinct endpoints into ``readProgramAccount`` -- including
``GET /businesses/{id}/guests`` (a member LIST) and ``DELETE
/businesses/{id}`` (a DELETE). Disambiguation then fell to a numeric
counter, producing ``readProgramAccount776``: a name that is both
unreadable and, in most cases, wrong.

Names here are derived from the FULL path, so a sub-resource can never be
swallowed by its parent.

DESIGN RULES
------------
1. Most-specific rule wins; RULES is ordered and matched top to bottom.
2. Every name is a business verb + business noun. No HTTP verbs, no URL
   fragments, no counters.
3. An unknown endpoint gets a readable structural name from
   :func:`_structural_name` -- never a catch-all belonging to another
   resource. A wrong-but-plausible name is worse than an ugly one,
   because it hides an endpoint inside an unrelated family.
4. Pure functions, no converter imports, so this is unit-testable on its
   own: ``python tools/ra_converter/phase_vocabulary.py``.

ROLE AND PARTNER
----------------
Role (owner / travel advisor / employee) and partner (H4L / H4B / LTA /
AMEX / SMB) are DATA, not distinct operations: ``POST
/realms/guests/enroll`` is one endpoint whose meaning depends on the row.
The converter therefore emits the neutral phase and lets the CSV row
carry the role -- it never guesses a role from a ReadyAPI step title,
which is the rule ``RETIRED_FLUENT_NAMES`` was created to enforce.

ROLE_ALIASES / PARTNER_ALIASES below drive the hand-authoring DSL, where
the author states the role EXPLICITLY. A human writing ``.enrollOwner()``
is declaring intent, not guessing -- so the "never invent a role" rule is
preserved while the readable chain becomes available.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Path normalisation
# ---------------------------------------------------------------------------

_PARAM = re.compile(r"\{[^}]*\}")
_HASH_PARAM = re.compile(r"#[^#]*#")
_DOLLAR_PARAM = re.compile(r"\$\{[^}]*\}")
# Salesforce sobject paths carry an API version that must not affect the name.
_SF_VERSION = re.compile(r"/data/v[0-9.]+/", re.I)


def normalize_path(path: str) -> str:
    """Collapse every path parameter to ``{}`` so naming is stable.

    ``/guests/{guestId}/businesses/#accountId#`` and
    ``/guests/{gid}/businesses/{aid}`` normalise identically.
    """
    if not path:
        return ""
    p = path.strip()
    p = p.split("?", 1)[0]
    # Salesforce version segment goes FIRST and is dropped outright.
    # Collapsing params first would turn /data/v55.0/ into /data/{}/ and
    # no Salesforce rule could ever match. Caught by _self_test rule 2.
    p = _SF_VERSION.sub("/data/", p)
    p = _DOLLAR_PARAM.sub("{}", p)
    p = _PARAM.sub("{}", p)
    p = _HASH_PARAM.sub("{}", p)
    p = re.sub(r"//+", "/", p)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


# ---------------------------------------------------------------------------
# The vocabulary
#
# Each rule: (verb-or-None, exact-normalised-path, canonical-name).
# verb None matches any verb. Ordered most-specific first.
# ---------------------------------------------------------------------------

RULES: list[tuple[str | None, str, str]] = [
    # --- auth -------------------------------------------------------------
    ("POST", "/realms/applications/token", "fetchApiToken"),
    ("POST", "/oauth2/token", "fetchSalesforceToken"),

    # --- guest ------------------------------------------------------------
    ("POST", "/realms/guests/enroll", "enrollGuest"),
    ("DELETE", "/guests/{}", "deleteGuest"),
    ("GET", "/guests/{}/hhonors", "readHonors"),
    ("POST", "/guests/{}/hhonors", "enrollHonors"),
    ("PUT", "/guests/{}/hhonors", "updateHonors"),
    ("GET", "/guests/{}/personalinfo/emails", "readGuestEmails"),
    ("POST", "/guests/{}/reservations", "createReservation"),
    ("PUT", "/guests/{}/reservations/{}/gnr/{}", "updateReservationStay"),
    ("DELETE", "/guests/{}/reservations/{}/gnr/{}/cancel", "cancelReservationStay"),
    ("GET", "/shop/props/{}", "shopProperty"),

    # --- program account: collection --------------------------------------
    ("GET", "/businesses", "searchProgramAccounts"),
    ("POST", "/businesses/compare", "compareProgramAccounts"),
    ("GET", "/businesses/compare", "compareProgramAccountsByQuery"),
    ("POST", "/businesses/forcedenroll", "forceEnrollProgramAccount"),
    ("POST", "/guests/{}/businesses", "createProgramAccount"),
    ("GET", "/guests/{}/businesses", "listGuestProgramAccounts"),
    ("GET", "/guests/{}/businesses/verify", "verifyProgramAccount"),
    ("GET", "/guests/{}/businesses/memberinvites/{}", "readMemberInviteByKey"),

    # --- program account: instance ----------------------------------------
    # Sub-resources are listed BEFORE the bare instance so they can never be
    # swallowed by it -- this is the bug that produced readProgramAccount776.
    ("POST", "/businesses/{}/activate", "activateProgramAccount"),
    ("POST", "/businesses/{}/reject", "rejectProgramAccount"),
    ("PUT", "/businesses/{}/centralbillsummary", "updateCentralBillSummary"),
    ("GET", "/businesses/{}/guests", "listAccountGuests"),
    ("POST", "/businesses/{}/invitationlink", "sendInvitationLink"),
    ("POST", "/businesses/{}/mergemember", "mergeAccountMember"),
    ("POST", "/businesses/{}/members/{}/activate", "activateAccountMember"),
    ("POST", "/businesses/{}/members/{}/remove", "removeAccountMember"),
    ("POST", "/businesses/{}/members/{}/mergeguest", "mergeAccountMemberGuest"),
    ("GET", "/businesses/{}", "readProgramAccount"),
    ("PUT", "/businesses/{}", "updateProgramAccount"),
    ("PATCH", "/businesses/{}", "patchProgramAccount"),
    ("DELETE", "/businesses/{}", "deleteProgramAccount"),

    # --- program account, guest-scoped ------------------------------------
    ("GET", "/guests/{}/businesses/{}/businessprofile", "readBusinessProfile"),
    ("PUT", "/guests/{}/businesses/{}/businessprofile", "updateBusinessProfile"),
    ("GET", "/guests/{}/businesses/{}/dashboardSummary", "readDashboardSummary"),
    ("GET", "/guests/{}/businesses/{}/dashboardSummary/brandspend", "readBrandSpend"),
    ("GET", "/guests/{}/businesses/{}/dashboardSummary/honors", "readHonorsSummary"),
    ("GET", "/guests/{}/businesses/{}/dashboardSummary/memberbookingmetrics",
     "readMemberBookingMetrics"),
    ("GET", "/guests/{}/businesses/{}/dashboardSummary/spendMonthly", "readSpendMonthly"),
    ("GET", "/guests/{}/businesses/{}/dashboardSummary/spendSummary", "readSpendSummary"),
    ("GET", "/guests/{}/businesses/{}/spendDetail", "readAccountSpendDetail"),
    ("POST", "/guests/{}/businesses/{}/emaildomains", "createEmailDomain"),
    ("DELETE", "/guests/{}/businesses/{}/emaildomains/{}", "deleteEmailDomain"),
    ("POST", "/guests/{}/businesses/{}/honors/transfer/members/{}", "transferMemberPoints"),
    ("POST", "/guests/{}/businesses/{}/invitationLink", "sendInvitationLinkAsGuest"),
    ("GET", "/guests/{}/businesses/{}/memberinvites", "listMemberInvites"),
    ("POST", "/guests/{}/businesses/{}/memberinvites", "sendMemberInvites"),
    ("GET", "/guests/{}/businesses/{}/preferences/rolepermissions",
     "readAccountRolePermissions"),
    ("PUT", "/guests/{}/businesses/{}/preferences/rolepermissions/{}",
     "updateAccountRolePermission"),
    ("GET", "/guests/{}/businesses/{}", "readProgramAccountAsGuest"),
    ("PUT", "/guests/{}/businesses/{}", "updateProgramAccountAsGuest"),
    ("DELETE", "/guests/{}/businesses/{}", "deleteProgramAccountAsGuest"),

    # --- account members --------------------------------------------------
    ("POST", "/guests/{}/businesses/{}/members/{}/confirmValidation", "confirmMemberTotp"),
    ("POST", "/guests/{}/businesses/{}/members/{}/requestValidation",
     "requestMemberValidation"),
    ("PUT", "/guests/{}/businesses/{}/members/{}/email", "updateMemberEmail"),
    ("PUT", "/guests/{}/businesses/{}/members/{}/phone", "updateMemberPhone"),
    ("PUT", "/guests/{}/businesses/{}/members/{}/role", "updateMemberRole"),
    ("GET", "/guests/{}/businesses/{}/members/{}/bookingPolicies",
     "readMemberBookingPolicies"),
    ("GET", "/guests/{}/businesses/{}/members/{}/spendDetail", "readMemberSpendDetail"),
    ("GET", "/guests/{}/businesses/{}/members/{}/stays", "readMemberStays"),
    ("GET", "/guests/{}/businesses/{}/members/{}/preferences/memberpermissions",
     "readMemberPermissions"),
    ("PUT", "/guests/{}/businesses/{}/members/{}/preferences/memberpermissions",
     "updateMemberPermissions"),
    ("PUT", "/guests/{}/businesses/{}/members/{}/preferences/rolepermissions",
     "updateMemberRolePermissions"),
    ("GET", "/guests/{}/businesses/{}/members", "listAccountMembers"),
    ("POST", "/guests/{}/businesses/{}/members", "createAccountMember"),
    ("GET", "/guests/{}/businesses/{}/members/{}", "readAccountMember"),
    ("PUT", "/guests/{}/businesses/{}/members/{}", "updateAccountMember"),
    ("DELETE", "/guests/{}/businesses/{}/members/{}", "deleteAccountMember"),

    # --- travel agency ----------------------------------------------------
    ("GET", "/travelagencies/{}", "readTravelAgency"),
    ("PUT", "/travelagencies/{}", "createTravelAgency"),

    # --- partners ---------------------------------------------------------
    ("POST", "/partners/amex/guestbusinesses", "enrollPartnerGuestBusiness"),

    # --- clients ----------------------------------------------------------
    ("GET", "/clients/{}", "readClients"),

    # --- kafka topics -----------------------------------------------------
    ("GET", "/topics/{}/partitions", "listTopicPartitions"),
    ("GET", "/topics/{}/partitions/{}", "readTopicPartition"),

    # --- salesforce -------------------------------------------------------
    ("POST", "/data/sobjects/Account", "createSalesforceAccount"),
    ("GET", "/data/sobjects/Account/{}", "readSalesforceAccount"),
    ("GET", "/data/sobjects/Contact/{}", "readSalesforceContact"),
    ("PUT", "/data/sobjects/Contact/{}", "updateSalesforceContact"),
    ("POST", "/data/sobjects/Distribution__c", "createSalesforceDistribution"),
    ("GET", "/data/sobjects/Lead/{}", "readSalesforceLead"),
    ("GET", "/data/sobjects/Program_Member__c/{}", "readSalesforceProgramMember"),
    ("PUT", "/data/sobjects/Program_Member__c/{}", "updateSalesforceProgramMember"),
    ("GET", "/data/sobjects/Program_Participation__c/{}",
     "readSalesforceProgramParticipation"),
]

_RULE_INDEX: dict[tuple[str | None, str], str] = {
    (verb, path): name for verb, path, name in RULES
}

_VERB_PREFIX = {
    "GET": "read", "POST": "create", "PUT": "update",
    "PATCH": "patch", "DELETE": "delete",
}

_NOISE_SEGMENTS = frozenset({"{}", "data", "sobjects", "v2", "v1", "api"})


def _pascal(word: str) -> str:
    return word[:1].upper() + word[1:] if word else ""


def _structural_name(verb: str, path: str) -> str:
    """Readable fallback for an endpoint with no explicit rule.

    Deliberately NOT a catch-all onto an existing family: an unmapped
    endpoint gets its own honest name derived from its own path, so it
    shows up as unmapped rather than hiding inside another resource.
    """
    segments = [s for s in path.split("/") if s and s not in _NOISE_SEGMENTS]
    if not segments:
        return "callEndpoint"
    tail = segments[-3:]
    words = []
    for seg in tail:
        words.extend(w for w in re.findall(r"[A-Za-z0-9]+", seg) if w)
    if not words:
        return "callEndpoint"
    prefix = _VERB_PREFIX.get((verb or "").upper(), "call")
    return prefix + "".join(_pascal(w) for w in words[:4])


def canonical_name(verb: str, path: str) -> str:
    """Business name for one HTTP call. Never returns a counter."""
    p = normalize_path(path)
    v = (verb or "").upper()
    hit = _RULE_INDEX.get((v, p))
    if hit:
        return hit
    hit = _RULE_INDEX.get((None, p))
    if hit:
        return hit
    return _structural_name(v, p)


def is_mapped(verb: str, path: str) -> bool:
    """True when an explicit rule exists (not the structural fallback)."""
    p = normalize_path(path)
    v = (verb or "").upper()
    return (v, p) in _RULE_INDEX or (None, p) in _RULE_INDEX


def all_names() -> list[str]:
    return sorted({name for _, _, name in RULES})


def version() -> str:
    """Stable fingerprint of the rule table.

    Stamped into fluent_catalog.json so the catalog self-invalidates the
    moment a rule is added, removed, or renamed. Without this, catalog
    entries keyed by the OLD phase names get re-seeded on the next convert
    and silently undo the vocabulary change.
    """
    import hashlib
    payload = "|".join(f"{v or '*'} {p} {n}" for v, p, n in RULES)
    payload += "||" + "|".join(sorted(ROLE_ALIASES)) + "||" + "|".join(sorted(PARTNER_ALIASES))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Hand-authoring DSL aliases
#
# alias -> (canonical phase, {ctx/row overrides the alias implies})
# The converter NEVER uses these: it emits the canonical phase and lets the
# CSV row supply role/partner. They exist so a human can state intent.
# ---------------------------------------------------------------------------

ROLE_ALIASES: dict[str, tuple[str, dict[str, str]]] = {
    "enrollOwner": ("enrollGuest", {"role": "owner"}),
    "enrollTravelAdvisor": ("enrollGuest", {"role": "travelCoordinator"}),
    "enrollEmployee": ("enrollGuest", {"role": "employee"}),
    "enrollAdmin": ("enrollGuest", {"role": "admin"}),
    "confirmOwner": ("confirmMemberTotp", {"role": "owner"}),
    "confirmTravelAdvisor": ("confirmMemberTotp", {"role": "travelCoordinator"}),
    "confirmEmployee": ("confirmMemberTotp", {"role": "employee"}),
    "addTravelAdvisor": ("createAccountMember", {"role": "travelCoordinator"}),
    "addEmployee": ("createAccountMember", {"role": "employee"}),
    "addAdmin": ("createAccountMember", {"role": "admin"}),
}

PARTNER_ALIASES: dict[str, tuple[str, dict[str, str]]] = {
    "createH4LAccount": ("createProgramAccount", {"partner": "h4l"}),
    "createH4BAccount": ("createProgramAccount", {"partner": "h4b"}),
    "createLTAAccount": ("createProgramAccount", {"partner": "lta"}),
    "createSmbAccount": ("createProgramAccount", {"partner": "smb"}),
    "createAmexAccount": ("createProgramAccount", {"partner": "amex"}),
}

# Composite phases: not one endpoint, but a named sequence the suites
# repeat. Kept explicit so a chain can read at business altitude.
COMPOSITE_ALIASES: dict[str, tuple[str, ...]] = {
    "prepareSalesforceAccount": (
        "fetchSalesforceToken", "createSalesforceAccount",
        "createSalesforceDistribution",
    ),
    "activateThroughHws": ("readSalesforceLead", "activateProgramAccount"),
    "verifySynchronization": (
        "readSalesforceAccount", "readSalesforceProgramMember",
        "readSalesforceProgramParticipation",
    ),
}


def resolve_alias(name: str) -> tuple[str, dict[str, str]] | None:
    """Alias -> (canonical phase, implied row overrides), or None."""
    if name in ROLE_ALIASES:
        return ROLE_ALIASES[name]
    if name in PARTNER_ALIASES:
        return PARTNER_ALIASES[name]
    return None


def _self_test() -> int:
    """Validate the table against the real corpus invariants."""
    failures: list[str] = []

    # 1. No two (verb, path) rules collide.
    seen: dict[tuple[str | None, str], str] = {}
    for verb, path, name in RULES:
        key = (verb, path)
        if key in seen:
            failures.append(f"duplicate rule {key}: {seen[key]} vs {name}")
        seen[key] = name

    # 2. Rule paths must already be normalised.
    for verb, path, name in RULES:
        if normalize_path(path) != path:
            failures.append(f"rule path not normalised: {path} ({name})")

    # 3. The exact bug this module exists to fix.
    cases = [
        ("GET", "/businesses/{accountId}", "readProgramAccount"),
        ("GET", "/businesses/{accountId}/guests", "listAccountGuests"),
        ("DELETE", "/businesses/{accountId}", "deleteProgramAccount"),
        ("GET", "/guests/{g}/businesses/{a}/dashboardSummary/spendSummary",
         "readSpendSummary"),
        ("GET", "/guests/{g}/businesses/{a}/members", "listAccountMembers"),
        ("GET", "/guests/{g}/businesses/{a}/members/{m}", "readAccountMember"),
        ("POST", "/realms/guests/enroll", "enrollGuest"),
        ("POST", "/guests/{g}/businesses/{a}/members/{m}/confirmValidation",
         "confirmMemberTotp"),
        # hash-style params must normalise identically
        ("GET", "/businesses/#accountId#", "readProgramAccount"),
    ]
    for verb, path, expected in cases:
        got = canonical_name(verb, path)
        if got != expected:
            failures.append(f"{verb} {path}: expected {expected}, got {got}")

    # 4. No canonical name may end in a digit (that is the old counter shape).
    for name in all_names():
        if name and name[-1].isdigit():
            failures.append(f"canonical name ends in a digit: {name}")

    # 5. Aliases must resolve to real canonical names.
    known = set(all_names())
    for alias, (target, _) in {**ROLE_ALIASES, **PARTNER_ALIASES}.items():
        if target not in known:
            failures.append(f"alias {alias} -> unknown phase {target}")
    for alias, seq in COMPOSITE_ALIASES.items():
        for target in seq:
            if target not in known:
                failures.append(f"composite {alias} -> unknown phase {target}")

    # 6. An unmapped endpoint must NOT borrow another family's name.
    stray = canonical_name("GET", "/totally/unknown/thing")
    if stray in known:
        failures.append(f"unmapped endpoint borrowed a real name: {stray}")

    if failures:
        print("phase_vocabulary SELF-TEST FAILED")
        for f in failures:
            print("  -", f)
        return 1
    print(f"phase_vocabulary self-test OK "
          f"({len(RULES)} rules, {len(all_names())} distinct phases, "
          f"{len(ROLE_ALIASES)} role aliases, {len(PARTNER_ALIASES)} partner aliases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
