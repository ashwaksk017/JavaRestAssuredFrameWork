"""Guards for the config-driven Mermaid image rendering.

Offline by design: the real renderer (Playwright + CDN) runs only when
RA_PNG_INTEGRATION=1 is set, so verify_all stays deterministic on a machine
without a browser or network.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import converter_config as cc  # noqa: E402
import mermaid_png as mp  # noqa: E402


# ------------------------------------------------------------- config file
def test_defaults_and_layers_merge_deeply():
    cfg = cc.load_config(None)
    assert cfg["diagrams"]["png"]["enabled"] is False
    assert cfg["diagrams"]["png"]["format"] == "png"
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "c.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"diagrams": {"png": {"enabled": True, "cases": "B2B-5264*"}}}, f)
        cfg2 = cc.load_config(p)
    assert cfg2["diagrams"]["png"]["enabled"] is True
    assert cfg2["diagrams"]["png"]["cases"] == "B2B-5264*"
    assert cfg2["diagrams"]["png"]["scale"] == cfg["diagrams"]["png"]["scale"], "untouched keys keep defaults"


def test_doc_keys_are_ignored_and_validation_catches_bad_values():
    assert "_comment" not in cc.load_config(None)
    bad = cc.deep_merge(cc.DEFAULTS, {"diagrams": {"png": {"format": "gif", "renderer": "paint", "scale": "x"}}})
    problems = cc.validate(bad)
    assert any("format" in p for p in problems)
    assert any("renderer" in p for p in problems)
    assert any("scale" in p for p in problems)
    assert cc.validate(cc.load_config(None)) == []


# --------------------------------------------------------------- selection
def test_extract_mermaid_block():
    md = "# T\n\n- a\n\n```mermaid\nflowchart TD\n    a --> b\n```\n\ntrailing"
    assert mp.extract_mermaid(md) == "flowchart TD\n    a --> b"
    assert mp.extract_mermaid("no fence here") is None
    assert mp.extract_mermaid("```mermaid\r\nflowchart TD\r\n  x\r\n```") == "flowchart TD\r\n  x"


def test_case_selector_forms():
    assert mp.case_selected("B2B-5264_post_create_account", "*")
    assert mp.case_selected("B2B-5264_post_create_account", "b2b-5264*")
    assert not mp.case_selected("B2B-7754_x", "B2B-5264*")
    assert mp.case_selected("B2B-7754_H4B_account", "re:H4B_account$")
    assert mp.case_selected("B2B-7754_H4B_account", ["b2b-7754_h4b_account"])
    assert not mp.case_selected("B2B-7754_H4B_account", ["other"])
    assert mp.case_selected("B2B-5264_post_create", ["B2B-7754_x", "b2b-5264*"]), "list entries may be globs"


# ---------------------------------------------------------------- backends
def test_backend_pick_reports_missing_renderer_clearly(monkeypatch):
    monkeypatch.setattr(mp, "_has_mmdc", lambda: None)
    monkeypatch.setattr(mp, "_has_playwright", lambda: False)
    b, reason = mp.pick_backend("auto")
    assert b is None and "mermaid-cli" in reason and "playwright" in reason
    b, reason = mp.pick_backend("mmdc")
    assert b is None and "mmdc" in reason
    monkeypatch.setattr(mp, "_has_mmdc", lambda: "C:/x/mmdc.cmd")
    assert mp.pick_backend("auto")[0] == "mmdc"
    monkeypatch.setattr(mp, "_has_playwright", lambda: True)
    assert mp.pick_backend("playwright")[0] == "playwright"


def test_render_driver_skips_without_backend_and_filters_cases(monkeypatch):
    monkeypatch.setattr(mp, "_has_mmdc", lambda: None)
    monkeypatch.setattr(mp, "_has_playwright", lambda: False)
    msgs: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        got = mp.render_case_images(td, "s", [("A", "cases/a.md")],
                                    {"enabled": True}, log=msgs.append)
    assert got == {} and any("skipped" in m for m in msgs)

    # with a fake backend: selection, extraction and the returned mapping
    calls: list = []

    def fake_render(jobs, fmt, scale, background, log):
        calls.extend(jobs)
        for _n, _s, out in jobs:
            with open(out, "wb") as f:
                f.write(b"png")
        return len(jobs), []

    monkeypatch.setattr(mp, "_has_mmdc", lambda: "mmdc")
    monkeypatch.setattr(mp, "_render_with_mmdc", fake_render)
    with tempfile.TemporaryDirectory() as td:
        cases = os.path.join(td, "_flows", "s", "cases")
        os.makedirs(cases)
        for stem in ("a", "b"):
            with open(os.path.join(cases, stem + ".md"), "w", encoding="utf-8") as f:
                f.write(f"# {stem}\n```mermaid\nflowchart TD\n  {stem} --> x\n```\n")
        with open(os.path.join(cases, "c.md"), "w", encoding="utf-8") as f:
            f.write("# c\nno diagram\n")
        got = mp.render_case_images(
            td, "s", [("Case-A", "cases/a.md"), ("Case-B", "cases/b.md"), ("Case-C", "cases/c.md")],
            {"cases": "case-a", "format": "png", "scale": 2, "renderer": "mmdc"}, log=lambda m: None)
        assert list(got) == ["Case-A"]
        assert got["Case-A"] == "png/a.png"
        assert os.path.isfile(os.path.join(td, "_flows", "s", "png", "a.png"))
    assert [j[0] for j in calls] == ["Case-A"]
    assert calls[0][1].startswith("flowchart TD")


def test_real_render_when_opted_in():
    """Set RA_PNG_INTEGRATION=1 to exercise Playwright + the CDN for real."""
    if os.environ.get("RA_PNG_INTEGRATION") != "1":
        return
    if not mp._has_playwright():
        return
    with tempfile.TemporaryDirectory() as td:
        cases = os.path.join(td, "_flows", "s", "cases")
        os.makedirs(cases)
        with open(os.path.join(cases, "a.md"), "w", encoding="utf-8") as f:
            f.write("```mermaid\nflowchart TD\n  a([\"start\"]) --> b[\"POST /x\"]\n```\n")
        got = mp.render_case_images(td, "s", [("A", "cases/a.md")],
                                    {"renderer": "playwright", "format": "png", "scale": 1},
                                    log=lambda m: None)
        assert got == {"A": "png/a.png"}
        assert os.path.getsize(os.path.join(td, "_flows", "s", "png", "a.png")) > 1000


if __name__ == "__main__":
    import inspect
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                if "monkeypatch" in inspect.signature(fn).parameters:
                    class _MP:
                        def __init__(self): self._undo = []
                        def setattr(self, obj, attr, val):
                            self._undo.append((obj, attr, getattr(obj, attr)))
                            setattr(obj, attr, val)
                        def undo(self):
                            for o, a, v in reversed(self._undo):
                                setattr(o, a, v)
                    mp_ = _MP()
                    try:
                        fn(mp_)
                    finally:
                        mp_.undo()
                else:
                    fn()
                print("ok ", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, "->", e)
    print(f"{len([n for n in globals() if n.startswith('test_')]) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
