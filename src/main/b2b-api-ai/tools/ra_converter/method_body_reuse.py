"""Reuse a fluent method when a ReadyAPI-mapped body already exists.

ReadyAPI steps are first mapped to a preferred fluent name (URL / PHASE).
The converter used to suffix ``createTravelAgency2`` whenever that name
already appeared in the same case, even when the rendered Java body was
identical to ``createTravelAgency`` in this case or another.

This utility is the single gate: look up the fingerprint of the body
first; only allocate a numbered name when the body is actually new.
"""
from __future__ import annotations

import re
from typing import Iterable

from fluent_scenario import RETIRED_FLUENT_NAMES, java_ident, phase_body_key

_TRAILING_COUNT = re.compile(r"^(.*?)(\d+)$")


def preferred_fluent_name(name: str, fallback: str = "runPhase") -> str:
    """Strip converter ``2``/``3`` disambiguators; keep the ReadyAPI mapping."""
    raw = (name or "").strip() or fallback
    m = _TRAILING_COUNT.match(raw)
    if m and m.group(1):
        raw = m.group(1)
    return java_ident(raw, fallback)


def name_rank(name: str) -> tuple:
    """Lower is better: unsuffixed names beat ``foo2`` / ``foo3``."""
    m = _TRAILING_COUNT.match(name or "")
    if m and m.group(1):
        return (1, int(m.group(2)), name or "")
    return (0, 0, name or "")


class MethodBodyIndex:
    """Map ``phase_body_key(body)`` → one canonical Java method name."""

    def __init__(self, fallback: str = "runPhase"):
        self.fallback = fallback
        self._by_body: dict[str, str] = {}
        self._by_name: dict[str, str] = {}
        self.reused = 0
        self.allocated = 0

    def lookup(self, body_lines: list[str] | None) -> str | None:
        return self._by_body.get(phase_body_key(body_lines or []))

    def reuse_or_allocate(self, preferred: str, body_lines: list[str] | None) -> str:
        body_lines = list(body_lines or [])
        key = phase_body_key(body_lines)
        existing = self._by_body.get(key)
        if existing:
            self.reused += 1
            return existing
        base = preferred_fluent_name(preferred, self.fallback)
        name = base
        n = 2
        while name in self._by_name:
            name = f"{base}{n}"
            n += 1
        self._by_body[key] = name
        self._by_name[name] = key
        self.allocated += 1
        return name

    def seed(self, name: str, body_lines: list[str] | None) -> str:
        """Register a catalog/shared method; body-first, unsuffixed preferred."""
        return self.reuse_or_allocate(name, body_lines)


class FluentMethodReuse:
    """Flow + verify indexes used by ``ra_converter`` emit."""

    def __init__(self):
        self.flow = MethodBodyIndex("runPhase")
        self.verify = MethodBodyIndex("verifyStep")

    def seed_catalog(self, catalog: dict | None) -> None:
        phases = (catalog or {}).get("phases") or {}
        for name, entry in sorted(phases.items(), key=lambda kv: name_rank(kv[0])):
            if name in RETIRED_FLUENT_NAMES or not isinstance(entry, dict):
                continue
            self.flow.seed(name, entry.get("body") or [])
        verifies = (catalog or {}).get("verifies") or {}
        for vkey, entry in sorted(verifies.items(), key=lambda kv: name_rank(kv[0])):
            if not isinstance(entry, dict):
                continue
            cls, _, meth = vkey.partition(".")
            if not meth:
                continue
            self.verify.seed(f"{cls}__{meth}", entry.get("body") or [])

    def assign_flow(
            self, flow_java: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
        return [(self.flow.reuse_or_allocate(name, body), body)
                for name, body in flow_java]

    def assign_verifies(
            self, verify_java: list[tuple[str, str, list[str]]],
    ) -> list[tuple[str, str, list[str]]]:
        out: list[tuple[str, str, list[str]]] = []
        for cls, meth, body in verify_java:
            allocated = self.verify.reuse_or_allocate(f"{cls}__{meth}", body)
            vmeth = allocated.split("__", 1)[-1]
            out.append((cls, vmeth, body))
        return out


def unique_method_defs(
        flow_java: Iterable[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    """Emit one Java method per name; the fluent chain may still call it twice."""
    seen: set[str] = set()
    out: list[tuple[str, list[str]]] = []
    for name, body in flow_java:
        if name in seen:
            continue
        seen.add(name)
        out.append((name, body))
    return out


def unique_verifies(
        verify_java: Iterable[tuple[str, str, list[str]]],
) -> list[tuple[str, str, list[str]]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, list[str]]] = []
    for cls, meth, body in verify_java:
        key = (cls, meth)
        if key in seen:
            continue
        seen.add(key)
        out.append((cls, meth, body))
    return out


def collapse_named_entries(entries: dict | None) -> dict:
    """One catalog entry per body key; keep the unsuffixed name when possible."""
    by_key: dict[str, tuple[str, dict]] = {}
    leftover: dict = {}
    for name, entry in (entries or {}).items():
        if not isinstance(entry, dict):
            leftover[name] = entry
            continue
        key = entry.get("key") or ""
        if not key:
            leftover[name] = entry
            continue
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = (name, dict(entry))
            continue
        cases = set(prev[1].get("cases") or []) | set(entry.get("cases") or [])
        if name_rank(name) < name_rank(prev[0]):
            merged = dict(entry)
            merged["cases"] = sorted(cases)
            by_key[key] = (name, merged)
        else:
            prev[1]["cases"] = sorted(cases)
    out = {name: entry for name, entry in by_key.values()}
    out.update(leftover)
    return out


def collapse_shared_phases(shared: dict) -> dict:
    """Drop duplicate ``_shared_phases`` names that share a fingerprint."""
    by_key: dict[str, str] = {}
    out: dict = {}
    for fname, info in shared.items():
        key = phase_body_key((info or {}).get("body") or [])
        if key in by_key:
            canon = by_key[key]
            out[canon]["cases"] = set(out[canon].get("cases") or []) | set(
                (info or {}).get("cases") or [])
            continue
        by_key[key] = fname
        out[fname] = info
    return out
