"""Render the per-case Mermaid flowcharts to PNG / SVG.

Input is what `Emitter.emit_flow_diagram` already writes: one markdown file
per ReadyAPI case with a single ```mermaid fenced block. This module reads
that block back and renders it with whichever backend the machine has:

  mmdc        the official mermaid CLI (npm i -g @mermaid-js/mermaid-cli);
  playwright  Python Playwright driving headless Chromium. The Mermaid
              LIBRARY is fetched from jsDelivr; the diagram text never
              leaves the machine (rendered in-page, screenshotted locally).

With neither available the convert still succeeds: the .md files stay, and
one message says how to install a renderer. Never a hard failure -- the
images are a convenience, the markdown is the record.
"""
from __future__ import annotations

import fnmatch
import html
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Iterable

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"
_FENCE_RX = re.compile(r"```mermaid[ \t]*\r?\n(.*?)\r?\n```", re.S)


# --------------------------------------------------------------- selection
def extract_mermaid(md_text: str) -> str | None:
    """The first ```mermaid block of a case file, or None."""
    m = _FENCE_RX.search(md_text or "")
    return m.group(1).strip() if m else None


def case_selected(case_name: str, selector) -> bool:
    """`*` / glob / `re:<regex>` / list of names, matched on the ReadyAPI
    case name (case-insensitive for glob and list)."""
    if selector is None or selector == "*" or selector == "":
        return True
    if isinstance(selector, list):
        # each entry is a name or a glob: ["B2B-5264*", "B2B-7754_H4B_account_active_204"]
        return any(case_selected(case_name, str(s).strip()) for s in selector if str(s).strip())
    sel = str(selector).strip()
    if sel.startswith("re:"):
        return re.search(sel[3:], case_name) is not None
    return fnmatch.fnmatch(case_name.lower(), sel.lower())


# ---------------------------------------------------------------- backends
def _has_mmdc() -> str | None:
    return shutil.which("mmdc")


def _has_playwright() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def pick_backend(preferred: str = "auto") -> tuple[str | None, str]:
    """(backend, reason). backend is 'mmdc', 'playwright' or None."""
    pref = (preferred or "auto").lower()
    if pref in ("auto", "mmdc") and _has_mmdc():
        return "mmdc", "mermaid-cli on PATH"
    if pref in ("auto", "playwright") and _has_playwright():
        return "playwright", "Python Playwright + Chromium"
    if pref == "mmdc":
        return None, "renderer=mmdc but `mmdc` is not on PATH (npm i -g @mermaid-js/mermaid-cli)"
    if pref == "playwright":
        return None, ("renderer=playwright but the package is missing "
                      "(pip install playwright && python -m playwright install chromium)")
    return None, ("no Mermaid renderer found: install `npm i -g @mermaid-js/mermaid-cli` "
                  "or `pip install playwright && python -m playwright install chromium`")


def _render_with_mmdc(jobs: list[tuple[str, str, str]], fmt: str, scale: float,
                      background: str, log: Callable[[str], None]) -> tuple[int, list[str]]:
    """jobs: (case_name, mermaid_text, out_path). One mmdc call per diagram."""
    ok, failed = 0, []
    mmdc = _has_mmdc()
    with tempfile.TemporaryDirectory() as td:
        for i, (name, src, out) in enumerate(jobs):
            inp = os.path.join(td, f"d{i}.mmd")
            with open(inp, "w", encoding="utf-8") as f:
                f.write(src)
            cmd = [mmdc, "-i", inp, "-o", out, "-b", background, "-q"]
            if fmt == "png":
                cmd += ["-s", str(scale)]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if r.returncode == 0 and os.path.isfile(out):
                    ok += 1
                else:
                    failed.append(f"{name}: {(r.stderr or r.stdout or '').strip()[:160]}")
            except Exception as e:  # noqa: BLE001
                failed.append(f"{name}: {e}")
    return ok, failed


_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:%s"><div id="out"></div>
<script src="%s"></script>
<script>mermaid.initialize({startOnLoad:false, securityLevel:'strict', theme:'default'});</script>
</body></html>"""


def _render_with_playwright(jobs: list[tuple[str, str, str]], fmt: str, scale: float,
                            background: str, log: Callable[[str], None]) -> tuple[int, list[str]]:
    from playwright.sync_api import sync_playwright
    ok, failed = 0, []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=float(scale) if fmt == "png" else 1)
        page.set_content(_PAGE % (html.escape(background, quote=True), MERMAID_CDN),
                         wait_until="load")
        try:
            page.wait_for_function("typeof mermaid !== 'undefined'", timeout=45000)
        except Exception:  # noqa: BLE001
            browser.close()
            return 0, [f"mermaid library did not load from {MERMAID_CDN} (offline?)"]
        for i, (name, src, out) in enumerate(jobs):
            try:
                svg = page.evaluate(
                    "async ([id, src]) => { const r = await mermaid.render(id, src);"
                    " document.getElementById('out').innerHTML = r.svg; return r.svg; }",
                    [f"g{i}", src])
                if fmt == "svg":
                    with open(out, "w", encoding="utf-8") as f:
                        f.write(svg)
                else:
                    el = page.query_selector("#out svg")
                    if el is None:
                        raise RuntimeError("no svg element after render")
                    el.screenshot(path=out, type="png")
                ok += 1
            except Exception as e:  # noqa: BLE001
                failed.append(f"{name}: {str(e).splitlines()[0][:160]}")
        browser.close()
    return ok, failed


# ------------------------------------------------------------------ driver
def render_case_images(output_dir: str, suite_name: str,
                       case_files: Iterable[tuple[str, str]], png_cfg: dict,
                       log: Callable[[str], None] = print) -> dict[str, str]:
    """Render every selected case diagram.

    case_files: (readyapi_case_name, path of its .md relative to
    `_flows/<suite>/`). Returns {case_name: image path relative to
    `_flows/<suite>/`} for the images that were written.
    """
    fmt = (png_cfg.get("format") or "png").lower()
    scale = float(png_cfg.get("scale") or 2)
    background = png_cfg.get("background") or "white"
    selector = png_cfg.get("cases", "*")
    out_rel_tpl = png_cfg.get("output_dir") or "_flows/{suite}/png"
    out_dir = os.path.join(output_dir, out_rel_tpl.replace("{suite}", suite_name))
    suite_dir = os.path.join(output_dir, "_flows", suite_name)

    backend, reason = pick_backend(png_cfg.get("renderer", "auto"))
    if backend is None:
        log(f"[ra_converter] diagrams.png: skipped -- {reason}")
        return {}

    jobs: list[tuple[str, str, str]] = []
    mapping: dict[str, str] = {}
    skipped_no_block = 0
    for case_name, md_rel in case_files:
        if not case_selected(case_name, selector):
            continue
        md_path = os.path.join(suite_dir, md_rel)
        try:
            with open(md_path, encoding="utf-8") as f:
                src = extract_mermaid(f.read())
        except OSError:
            src = None
        if not src:
            skipped_no_block += 1
            continue
        stem = os.path.splitext(os.path.basename(md_rel))[0]
        out_path = os.path.join(out_dir, f"{stem}.{fmt}")
        jobs.append((case_name, src, out_path))
        mapping[case_name] = os.path.relpath(out_path, suite_dir).replace(os.sep, "/")
    if not jobs:
        log("[ra_converter] diagrams.png: no case matched "
            f"{selector!r} (skipped without a mermaid block: {skipped_no_block})")
        return {}
    os.makedirs(out_dir, exist_ok=True)
    log(f"[ra_converter] diagrams.png: rendering {len(jobs)} diagram(s) as {fmt} "
        f"via {reason} -> {os.path.relpath(out_dir, output_dir)}")
    if backend == "mmdc":
        ok, failed = _render_with_mmdc(jobs, fmt, scale, background, log)
    else:
        ok, failed = _render_with_playwright(jobs, fmt, scale, background, log)
    for f in failed[:10]:
        log(f"[ra_converter] diagrams.png: FAILED {f}")
    if len(failed) > 10:
        log(f"[ra_converter] diagrams.png: ... and {len(failed) - 10} more")
    log(f"[ra_converter] diagrams.png: {ok} written, {len(failed)} failed")
    failed_names = {f.split(':', 1)[0] for f in failed}
    return {k: v for k, v in mapping.items() if k not in failed_names}
