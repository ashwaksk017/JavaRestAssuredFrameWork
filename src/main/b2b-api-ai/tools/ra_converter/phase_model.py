"""Phase specs and call shapes.

WHY THIS EXISTS
---------------
The converter emits one fluent method per REST step *as rendered text*,
and de-duplicates by comparing that text. Measured over all 18 ReadyAPI
projects: 11,555 REST steps, 103 distinct verb+path, 508 distinct
(verb, path, body template) -- yet 1,304 step methods, because the text
also bakes in the ReadyAPI step name, the ctx key an id is read from, the
expected status and the extracts. Two calls to ``GET /businesses/{id}``
that differ only in *where the id comes from* render as two methods.

This module names the thing the text was hiding:

* :class:`PhaseSpec` -- everything one rendered REST step was built from,
  captured by ``Emitter._render_rest_step_body`` as it renders. It is a
  record of what was emitted, not a second implementation of it, so it
  cannot drift from the Java.
* :class:`CallShape` -- the part of a spec that is the *same call*:
  verb, path template, query/header key sets, whether a body goes. Step
  name, template, status, id sources and extracts are *data* and stay out.
* :class:`ShapeRegistry` -- shapes seen across converter runs, persisted
  under ``fluent_catalog.json["shapes"]`` so a suite converted next week
  finds the shapes a suite converted today already produced.

Stage 1a of the refactor: capture + register + report. Emission is
unchanged. Stage 1b makes the engine methods and thin phases real.

Pure functions; no converter import at module level so this is unit-
testable alone (``python tools/ra_converter/test_phase_model.py``).
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_PARAM = re.compile(r"\{[^}]*\}")
_DOLLAR = re.compile(r"\$\{[^}]*\}")
_HASH = re.compile(r"#[^#/]*#")
_NUMERIC_SEG = re.compile(r"/\d{4,}(?=/|$)")
_SF_VERSION = re.compile(r"/data/v[0-9.]+/", re.I)


def normalize_path(path: str) -> str:
    """Path template with every parameter spelled ``{}``.

    ``/guests/{guestId}/businesses`` and ``/guests/{id}/businesses`` are one
    call. A literal numeric segment an author baked in is a parameter too.
    """
    p = (path or "").strip()
    p = _SF_VERSION.sub("/data/vN/", p)
    p = _DOLLAR.sub("{}", p)
    p = _HASH.sub("{}", p)
    p = _PARAM.sub("{}", p)
    p = _NUMERIC_SEG.sub("/{}", p)
    p = re.sub(r"/+", "/", p)
    return p.rstrip("/") or "/"


def bucket_of(path: str) -> str:
    """First static path token -- the resource family a shape belongs to."""
    for tok in normalize_path(path).split("/"):
        if not tok or tok == "{}" or re.fullmatch(r"v\d+[a-z]?", tok, re.I):
            continue  # empty, a parameter, or an API version segment
        return re.sub(r"[^A-Za-z0-9]", "", tok).lower() or "root"
    return "root"


@dataclass(frozen=True)
class CallShape:
    """The reusable part of a REST step: what is called, not what it is called."""
    verb: str
    path: str                    # normalised, see normalize_path
    query_keys: tuple = ()
    header_keys: tuple = ()
    takes_body: bool = False

    @property
    def id(self) -> str:
        raw = "|".join([
            self.verb.upper(), self.path,
            ",".join(sorted(self.query_keys)),
            ",".join(sorted(k.lower() for k in self.header_keys)),
            "B" if self.takes_body else "-",
        ])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]

    @property
    def engine_name(self) -> str:
        """Business name from the vocabulary -- the same one the fluent
        chain uses, so ``readProgramAccount()`` and its engine agree."""
        try:
            import phase_vocabulary
            return phase_vocabulary.canonical_name(self.verb, self.path)
        except Exception:  # vocabulary unavailable: structural fallback
            toks = [t for t in self.path.split("/") if t and t != "{}"]
            return (self.verb.lower() + "".join(t[:1].upper() + t[1:] for t in toks)) or "call"

    @property
    def bucket(self) -> str:
        return bucket_of(self.path)


@dataclass(frozen=True)
class PhaseSpec:
    """Everything ``_render_rest_step_body`` built one REST step from."""
    suite: str
    case: str
    step_name: str
    sid: str                     # sanitized step name (CSV column prefix)
    verb: str
    path: str                    # raw resource path with {names}
    client_method: str
    receiver: str
    template_expr: str | None
    regen: bool
    expected_status: int
    query: tuple = ()            # ((key, placeholder-or-literal), ...)
    headers: tuple = ()          # ((key, placeholder-or-literal), ...)
    path_args: tuple = ()        # Java expressions, in {param} order
    token_expr: str = ""
    poll: tuple | None = None    # (jsonPath, configKey, defaultMs)
    extracts: tuple = ()         # ((ctxKey, kind, path), ...)
    assertion_types: tuple = ()
    setup: bool = False          # rendered inside SetupHelper
    # Stage 1b: the call as the engine sees it
    engine_id: str = ""          # phase_emit.engine_id(...) of the typed client op
    path_refs: tuple = ()        # phase_emit.parse_ref(...) per path parameter
    token_ref: tuple = ("ctx", "tokenId.GeneratedTokenID")

    def shape(self) -> CallShape:
        return CallShape(
            verb=(self.verb or "POST").upper(),
            path=normalize_path(self.path),
            query_keys=tuple(sorted(k for k, _ in self.query)),
            header_keys=tuple(sorted(k for k, _ in self.headers)),
            takes_body=self.template_expr is not None,
        )


# Specs captured during THIS process, by suite. A module-level accumulator
# because the converter renders through several Emitter instances in a
# multi-XML run and saves the catalog from only one of them.
RUN_SPECS: dict[str, dict] = {}


def record(spec: PhaseSpec, key) -> None:
    RUN_SPECS.setdefault(spec.suite or "imported", {})[key] = spec


def reset_run() -> None:
    RUN_SPECS.clear()


class ShapeRegistry:
    """Shapes across runs. One entry per :attr:`CallShape.id`."""

    def __init__(self, data: dict | None = None):
        self.data: dict[str, dict] = {}
        for sid, e in (data or {}).items():
            if isinstance(e, dict) and e.get("verb") and e.get("path"):
                self.data[sid] = dict(e)
                self.data[sid]["suites"] = dict(e.get("suites") or {})
                self.data[sid]["clients"] = sorted(set(e.get("clients") or []))

    def merge(self, specs: Iterable[PhaseSpec], suite: str) -> None:
        """Replace ``suite``'s counts with what this run saw; keep others."""
        suite = suite or "imported"
        for e in self.data.values():
            e["suites"].pop(suite, None)
        for s in specs:
            # SetupHelper bodies count too: a case with an assigned flow
            # SKIPS those steps and calls the helper, so this is the only
            # rendering of that call. (Once per flow, not per case.)
            sh = s.shape()
            e = self.data.setdefault(sh.id, {
                "verb": sh.verb, "path": sh.path,
                "queryKeys": list(sh.query_keys),
                "headerKeys": list(sh.header_keys),
                "takesBody": sh.takes_body,
                "engine": sh.engine_name, "bucket": sh.bucket,
                "suites": {}, "clients": [],
                "sample": {"suite": s.suite, "case": s.case, "step": s.step_name},
            })
            e["suites"][suite] = e["suites"].get(suite, 0) + 1
            if s.client_method and s.client_method not in e["clients"]:
                e["clients"] = sorted(set(e["clients"]) | {s.client_method})
        # An entry no suite uses any more is residue from a suite that was
        # re-converted without that call; drop it so the report is honest.
        self.data = {k: v for k, v in self.data.items() if v["suites"]}

    def to_dict(self) -> dict:
        return {k: self.data[k] for k in sorted(self.data)}

    # ---- queries -------------------------------------------------------
    def stats(self) -> dict:
        total_steps = sum(sum(e["suites"].values()) for e in self.data.values())
        shared = [e for e in self.data.values() if len(e["suites"]) >= 2]
        return {
            "shapes": len(self.data),
            "shared": len(shared),
            "steps": total_steps,
            "stepsOnShared": sum(sum(e["suites"].values()) for e in shared),
            "suites": len({s for e in self.data.values() for s in e["suites"]}),
            "buckets": len({e["bucket"] for e in self.data.values()}),
        }


def render_report(reg: ShapeRegistry, phases_today: int | None = None) -> str:
    """Human-readable shape report; safe to paste (no hosts, no ids)."""
    st = reg.stats()
    out = []
    out.append("CALL SHAPE REPORT")
    out.append(f"suites: {st['suites']}   REST steps: {st['steps']}   "
               f"distinct call shapes: {st['shapes']}   shared by >=2 suites: {st['shared']}"
               f" (covering {st['stepsOnShared']} steps"
               f"{', ' + str(100 * st['stepsOnShared'] // st['steps']) + '%' if st['steps'] else ''})")
    if phases_today is not None:
        out.append(f"cross-suite phase bodies cached today: {phases_today}   "
                   f"engine methods after Stage 1b: {st['shapes']}")
    out.append("")
    by_bucket: dict[str, list] = {}
    for e in reg.data.values():
        by_bucket.setdefault(e["bucket"], []).append(e)
    out.append("== shapes by resource (engine class after Stage 1b) ==")
    for b, es in sorted(by_bucket.items(), key=lambda kv: -len(kv[1])):
        steps = sum(sum(e["suites"].values()) for e in es)
        out.append(f"  {b:28s} {len(es):4d} shapes  {steps:6d} steps")
    out.append("")
    out.append("== most shared shapes ==")
    top = sorted(reg.data.values(), key=lambda e: (-len(e["suites"]), -sum(e["suites"].values())))[:15]
    for e in top:
        n = sum(e["suites"].values())
        q = ("?" + ",".join(e["queryKeys"])) if e["queryKeys"] else ""
        out.append(f"  {len(e['suites']):2d} suites {n:6d} steps  {e['verb']:6s} {e['path']}{q}   -> {e['engine']}")
    out.append("")
    out.append("== shapes used by ONE suite only (candidates for suite-local engines) ==")
    solo = [e for e in reg.data.values() if len(e["suites"]) == 1]
    out.append(f"  {len(solo)} shapes, {sum(sum(e['suites'].values()) for e in solo)} steps")
    return "\n".join(out) + "\n"
