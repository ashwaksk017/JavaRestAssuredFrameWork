"""Project-level converter configuration.

One JSON file, generic defaults committed in `converter.config.json`, three
override layers applied in order:

  1. the committed `converter.config.json` next to this module;
  2. a sibling `converter.config.local.json` (gitignored, per machine);
  3. an explicit `--config <path>`.

Only the keys a layer names are replaced (deep merge), so a local file can
flip one switch without repeating the rest. Keys starting with `_` are
documentation and are ignored.

Sections
--------
diagrams   per-case Mermaid flowcharts and their optional image rendering.
project    how ReadyAPI case names are read: the ticket prefix, the
           product-line / partner tokens.
identity   the Properties vocabulary the identity regeneration owns. This is
           the part that made the converter project-shaped; the defaults are
           the suite it was first built on, and every value is read from
           here by the Python emitter (`apply_to_modules`) and, through the
           emitted `converter_identity.json` resource, by the Java runtime
           (`IdentityVocabulary`).
heuristics project-specific validation rules the emitter and runtime honour
           (Salesforce ids, OTP zero-padding).
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "converter.config.json")
LOCAL_PATH = os.path.join(HERE, "converter.config.local.json")

DEFAULTS: dict[str, Any] = {
    # Placeholder spellings that Config.LEGACY_ALIASES resolves to the SAME
    # config key. Folded to the canonical form before a template body is
    # hashed, so two bodies that make an identical request stop being two
    # files. Only add a pair Java already treats as equal --
    # test_project_config checks that against Config.java.
    "placeholder_aliases": {
        "c_id": "client_id",
        "c_sec": "client_secret"
    },
    "diagrams": {
        "png": {
            "enabled": False,
            "format": "png",
            "scale": 2,
            "background": "white",
            "cases": "*",
            "renderer": "auto",
            "output_dir": "_flows/{suite}/png",
        }
    },
    "project": {
        "ticket_regex": r"^([A-Z][A-Z0-9]*[-_]?\d+)[-_ ]+(.+)$",
        "product_line_tokens": ["h4l", "h4b", "h4bl", "lta", "smb", "amex", "silhouette"],
        "partners": [
            {"token": "amex", "partner": "amex"},
            {"token": "silhouette", "partner": "silhouette"},
            {"token": "h4l", "partner": "h4l"},
            {"token": "h4b", "partner": "h4b"},
            {"token": "lta", "partner": "lta"},
            {"token": "_ta_", "partner": "lta"},
            {"token": "smb", "partner": "smb"},
        ],
    },
    "identity": {
        "namespace": "Properties",
        "standard_fields": [
            "Username", "usernamemember", "usernameM",
            "Email", "EmailMember", "guestMemberEmail",
            "Phone", "phoneNumber", "hhonorsNumber",
            "Domain", "websiteDomain",
            "generatedemailAddress", "generatedEmail",
            "guestId", "guestID", "memberGuestID",
            "accountId", "accountID",
            "memberId", "memberID",
            "partnerAccountId", "partnerAccountID",
        ],
        "regen_trigger_keys": [
            "username", "usernamemember",
            "email", "emailaddress", "emailmember", "generatedemail",
            "generatedemailaddress", "guestmemberemail",
            "phone", "phonenumber",
            "domain", "websitedomain", "weburl",
            "hhonorsnumber",
        ],
        "identity_hints": ["username", "email", "name", "phone", "domain", "website",
                           "firstname", "lastname", "address", "city", "state", "postal"],
        "id_hint_fields": ["guestid", "accountid", "memberid", "hhonorsnumber",
                           "hhonors_number", "partneraccountid", "customerid",
                           "userid", "hilton_member_id", "hiltonmemberid"],
        "id_param_names": ["guestId", "guestID", "accountId", "accountID", "memberId", "memberID", "hhonorsNumber", "hHonorsNumber", "partnerAccountId", "partnerAccountID", "customerId", "userId"],
        "frozen_domain_key": "Hardcodeddomain",
        "allowed_domains_config_key": "ALLOWED_DOMAINS",
        "freemail_domains": [
            "yahoo.com", "gmail.com", "hotmail.com", "aol.com", "outlook.com",
            "live.com", "msn.com", "icloud.com", "mail.com", "ymail.com",
            "protonmail.com", "gmx.com", "zoho.com", "me.com", "mac.com",
        ],
        "bindable_emails": [
            "Email", "EmailAddress", "GeneratedEmail", "generatedemailAddress",
            "generatedemailAddress1", "generatedemailAddress2", "generatedemailAddress3",
            "generatedemailAddress_1", "generatedemailAddress_2", "generatedemailAddress_3",
            "Email1", "Email2", "Email3", "Email_1", "Email_2", "Email_3",
            "EmailMember", "guestMemberEmail",
        ],
        "bindable_domains": [
            "Hardcodeddomain", "Domain2", "Domain1", "Domain3", "Domain4",
            "Domain5", "Domain6", "Domain7", "Domain8", "Domain9",
            "Domain_1", "Domain_2", "Domain_3", "Domain_4", "Domain_5",
            "Domain_6", "Domain_7", "Domain_8", "Domain_9",
        ],
        "named_identity_keys": [
            "email", "emailaddress", "generatedemail", "generatedemailaddress",
            "generatedemailaddress1", "generatedemailaddress2", "generatedemailaddress3",
            "emailmember", "guestmemberemail", "email1", "email2", "email3",
            "email_1", "email_2", "email_3", "hardcodedemail", "updatedemail",
            "updatedmailaddress", "phone", "phonenumber", "hhonorsnumber",
        ],
        "member_enroll_step_patterns": ["memberhhonorsenroll", "hhonorsenrollmember",
                                        "member+hhonorsenroll"],
        "fixture_literal_fields": {"memberGuestID": r"\d{6,}"},
    },
    "heuristics": {
        "salesforce": {
            "enabled": True,
            "id_field_regex": "(?i)^(.*(sfdc|salesforce).*)id$",
            "id_shape": "[A-Za-z0-9]{15,18}",
            "session_key_fragment": "sftokenid",
        },
        "otp": {
            "pad_width": 6,
            "code_like": ["otp", "totp", "pin"],
            "code_exclusions": ["prop", "brand", "product", "rate", "room", "zip",
                                "postal", "country", "currency", "iata", "airport",
                                "source", "record", "record_type"],
        },
    },
}


def _strip_docs(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_docs(v) for k, v in node.items() if not str(k).startswith("_")}
    return node


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a JSON object")
    return _strip_docs(data)


def load_config(explicit_path: str | None = None) -> dict:
    """Defaults <- committed file <- local file <- --config file."""
    cfg = copy.deepcopy(DEFAULTS)
    for p in (DEFAULT_PATH, LOCAL_PATH, explicit_path):
        if p:
            cfg = deep_merge(cfg, _read(p))
    return cfg


def validate(cfg: dict) -> list[str]:
    """Human-readable problems; empty list when the config is usable."""
    import re
    problems: list[str] = []
    png = (cfg.get("diagrams") or {}).get("png") or {}
    if png.get("format") not in ("png", "svg"):
        problems.append(f"diagrams.png.format must be png or svg, got {png.get('format')!r}")
    if png.get("renderer") not in ("auto", "mmdc", "playwright"):
        problems.append(f"diagrams.png.renderer must be auto, mmdc or playwright, got {png.get('renderer')!r}")
    try:
        if float(png.get("scale", 2)) <= 0:
            problems.append("diagrams.png.scale must be > 0")
    except (TypeError, ValueError):
        problems.append(f"diagrams.png.scale must be a number, got {png.get('scale')!r}")
    cases = png.get("cases", "*")
    if not isinstance(cases, (str, list)):
        problems.append("diagrams.png.cases must be a string pattern or a list of case names")

    proj = cfg.get("project") or {}
    try:
        rx = re.compile(proj.get("ticket_regex", ""))
        if rx.groups < 2:
            problems.append("project.ticket_regex needs two groups: (ticket)(remainder)")
    except re.error as e:
        problems.append(f"project.ticket_regex is not a valid regex: {e}")
    for p in proj.get("partners") or []:
        if not isinstance(p, dict) or not p.get("token") or not p.get("partner"):
            problems.append(f"project.partners entries need token + partner, got {p!r}")
            break

    ident = cfg.get("identity") or {}
    for key in ("standard_fields", "regen_trigger_keys", "identity_hints", "id_hint_fields",
                "id_param_names",
                "freemail_domains", "bindable_emails", "bindable_domains",
                "named_identity_keys", "member_enroll_step_patterns"):
        v = ident.get(key)
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
            problems.append(f"identity.{key} must be a non-empty list of strings")
    for key in ("namespace", "frozen_domain_key", "allowed_domains_config_key"):
        if not isinstance(ident.get(key), str) or not ident.get(key):
            problems.append(f"identity.{key} must be a non-empty string")
    fx = ident.get("fixture_literal_fields", {})
    if not isinstance(fx, dict):
        problems.append("identity.fixture_literal_fields must be an object of field -> regex")
    else:
        for k, v in fx.items():
            try:
                re.compile(v)
            except re.error as e:
                problems.append(f"identity.fixture_literal_fields.{k} is not a valid regex: {e}")

    sf = (cfg.get("heuristics") or {}).get("salesforce") or {}
    for key in ("id_field_regex", "id_shape"):
        try:
            re.compile(sf.get(key, ""))
        except re.error as e:
            problems.append(f"heuristics.salesforce.{key} is not a valid regex: {e}")
    otp = (cfg.get("heuristics") or {}).get("otp") or {}
    try:
        if int(otp.get("pad_width", 6)) < 1:
            problems.append("heuristics.otp.pad_width must be >= 1")
    except (TypeError, ValueError):
        problems.append(f"heuristics.otp.pad_width must be an integer, got {otp.get('pad_width')!r}")
    return problems


def identity_resource(cfg: dict) -> dict:
    """What the Java runtime reads from `converter_identity.json`: the
    identity and heuristics sections verbatim, plus the project name space."""
    return {
        "_generated": "by tools/ra_converter from converter.config.json -- edit that file, not this one",
        "identity": copy.deepcopy(cfg.get("identity") or {}),
        "heuristics": copy.deepcopy(cfg.get("heuristics") or {}),
    }


def apply_to_modules(cfg: dict) -> None:
    """Point the emitter's module-level tables at the configured values.

    The tables stay module globals (they are read in hot loops), and the
    built-in literals equal the committed defaults, so a run with no config
    changes is byte-identical to one before this hook existed.
    """
    import re
    ident = cfg.get("identity") or {}
    proj = cfg.get("project") or {}
    heur = cfg.get("heuristics") or {}
    try:
        import ra_converter as rc
    except ImportError:
        rc = None
    try:
        import groovy_translator as gt
    except ImportError:
        gt = None
    if rc is not None:
        rc._REGEN_TRIGGER_KEYS = frozenset(s.lower() for s in ident.get("regen_trigger_keys", []))
        rc._ID_HINTS = tuple(s.lower() for s in ident.get("id_hint_fields", []))
        rc._PATH_ID_PARAM_NAMES = frozenset(ident.get("id_param_names", []))
        rc.Emitter._HARDCODED_ID_FIELDS = tuple(ident.get("id_param_names", []))
        rc._MEMBER_ENROLL_PATTERNS = tuple(s.lower() for s in ident.get("member_enroll_step_patterns", []))
        rc._FIXTURE_LITERAL_FIELDS = {k.lower(): v for k, v in (ident.get("fixture_literal_fields") or {}).items()}
        rc._PRODUCT_LINE_FLOW_TOKENS = frozenset(s.lower() for s in proj.get("product_line_tokens", []))
        rc._PARTNER_TOKENS = tuple((p["token"].lower(), p["partner"]) for p in proj.get("partners", []))
        rc._JIRA_TICKET_RX = re.compile(proj.get("ticket_regex") or rc._JIRA_TICKET_RX.pattern)
    if gt is not None:
        gt._IDENTITY_HINTS = tuple(s.lower() for s in ident.get("identity_hints", []))
        gt._STANDARD_FIELDS = list(ident.get("standard_fields", []))
        otp = heur.get("otp") or {}
        gt._OTP_PAD_WIDTH = int(otp.get("pad_width", 6))
        gt._OTP_CODE_LIKE = tuple(s.lower() for s in otp.get("code_like", []))
        gt._OTP_CODE_EXCLUSIONS = tuple(s.lower() for s in otp.get("code_exclusions", []))
