"""Project-level converter configuration.

One JSON file, generic defaults committed in `converter.config.json`, three
override layers applied in order:

  1. the committed `converter.config.json` next to this module;
  2. a sibling `converter.config.local.json` (gitignored, per machine);
  3. an explicit `--config <path>`.

Only the keys a layer names are replaced (deep merge), so a local file can
flip one switch without repeating the rest. Keys starting with `_` are
documentation and are ignored.
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
    }
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
    return problems
