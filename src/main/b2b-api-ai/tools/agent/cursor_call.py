"""One call to the Cursor agent, done the way that is known to work.

    result = run(prompt, ca, cfg, on_event=print)

`ca` is the converter's `cursor_assist` module and `cfg` its config (key,
model, cwd): there is still one key in one place.

WHY NOT JUST `Agent.prompt`
---------------------------
`Agent.prompt(prompt, options)` is one blocking call: nothing is known
until it returns, and a failure is whatever it raises. The sibling test
case generator (aimanualtc) calls the same SDK through
`Agent.create` -> `send` -> `messages()` -> `wait`, and its history is a
list of things that went wrong first. What is taken from it here:

* **Progress while it runs.** `messages()` yields status changes and tool
  calls as they happen. They are passed to `on_event`, so the Cursor log
  moves during a five-minute run instead of filling in at the end.
* **"Never started" is not "failed half way".** `CursorAgentError` means
  the run did not start -- key, network, bridge. `status == "error"` on
  the result means it started and broke. They need different fixes, so
  they raise different messages.
* **`mode="agent"`.** In plan mode the agent describes what it would do
  and changes nothing.
* **The working directory is ONE string.** A list there fails inside the
  SDK with "expected str, bytes or os.PathLike object, not list".
* **The bridge gets 90 seconds on Windows.** Its first launch is slow
  (antivirus scan of the bundled Node), and the SDK's default gave up.
* **Diagnostics.** Tool errors seen on the way are kept and attached to
  the failure message.

If the installed SDK has no `Agent.create` (an older one), this falls
back to the converter's `Agent.prompt` call rather than failing.

The names used here -- `Agent.create(options)` as a context manager,
`AgentOptions(api_key, model, mode, local)`, `LocalAgentOptions(cwd)`,
`run.messages()`, `run.wait()` with `.status/.result/.id`, messages of
type "status" and "tool_call", `cursor_sdk.CursorAgentError`,
`_client._default_client`, `_bridge._read_discovery` -- were read out of
the cursor-sdk 1.0.37 wheel. The tests use a fake with that surface; no
test here calls the real service.

NOT taken from there: its prompt forbids file edits and asks for JSON,
and it retries with other models. This loop WANTS the files edited, and
checks them afterwards; and it does not silently switch model.
"""
from __future__ import annotations

import os
import sys
import time

WINDOWS_BRIDGE_TIMEOUT = 90.0


class CursorCallError(RuntimeError):
    """The agent did not produce a usable result.

    kind: "setup"   the SDK or the key is missing
          "startup" the run never started (auth, network, bridge)
          "run"     the run started and failed
    """

    def __init__(self, message: str, kind: str, run_id: str = "", details: str = ""):
        super().__init__(message)
        self.kind = kind
        self.run_id = run_id
        self.details = details


def patch_bridge(ca) -> None:
    """The converter's Windows fix for bridge discovery, plus a timeout that
    survives a slow first launch. Idempotent; a no-op off Windows."""
    ca._patch_cursor_sdk_bridge_for_windows()
    if os.name != "nt":
        return
    import cursor_sdk._bridge as bridge
    current = bridge._read_discovery
    if getattr(current, "_ra_patient", False):
        return

    def patient(process, timeout):
        return current(process, max(float(timeout or 0), WINDOWS_BRIDGE_TIMEOUT))

    patient._ra_patient = True                  # type: ignore[attr-defined]
    patient._ra_windows_safe = True             # type: ignore[attr-defined]
    bridge._read_discovery = patient


def warm_up(ca):
    """Start the SDK's bridge once, without calling the agent.

    Returns (True, msg) when it started, (False, msg) when it failed, and
    (None, msg) when this SDK version has no entry point to do it with --
    which is not a failure, only something that could not be checked.
    """
    try:
        patch_bridge(ca)
        from cursor_sdk._client import _default_client
    except (ImportError, AttributeError) as e:
        return None, f"not checked ({type(e).__name__}: {e})"
    try:
        _default_client()
    except Exception as e:                               # noqa: BLE001
        return False, (str(e).strip() or type(e).__name__)
    return True, "started"


def describe(message) -> str:
    """One line for the Cursor log from one streamed message, or ""."""
    kind = getattr(message, "type", "") or ""
    if kind == "status":
        status = getattr(message, "status", "") or ""
        text = getattr(message, "message", "") or ""
        return f"status {status}" + (f": {text}" if text else "")
    if kind == "tool_call":
        name = getattr(message, "name", "") or "?"
        status = getattr(message, "status", "") or ""
        if status == "running":
            return ""               # the same call reports again when it ends
        line = f"tool {name} {status}".rstrip()
        if status == "error":
            line += f": {str(getattr(message, 'result', ''))[:300]}"
        return line
    return ""


def _conversation_text(run) -> str:
    """The assistant's text from the transcript, when the result has none."""
    try:
        # `supports` exists in some SDK versions only (1.0.37 has none).
        supports = getattr(run, "supports", None)
        if callable(supports) and not supports("conversation"):
            return ""
        chunks = []
        for turn in run.conversation():
            turn_obj = getattr(turn, "turn", turn)
            for step in getattr(turn_obj, "steps", ()):
                if getattr(step, "type", "") != "assistantMessage":
                    continue
                msg = getattr(step, "message", None)
                text = getattr(msg, "text", "") if msg is not None else ""
                if text and text.strip():
                    chunks.append(text.strip())
        return "\n\n".join(chunks)
    except Exception:                                    # noqa: BLE001
        return ""


def run(prompt: str, ca, cfg, on_event=None, followup=None, mode: str = "agent") -> dict:
    """Send one prompt; return {status, result, id, model, transport}.

    Raises CursorCallError. `on_event(line)` is called for each status
    change and tool call while the agent works.

    `followup(text)` may return a second prompt to send on the SAME agent
    when the first answer is not usable (for instance "reply with only
    the JSON object"). The agent still has the whole first exchange, so
    this costs one short turn instead of a full re-run. It is asked once.
    If that second turn fails, the first answer is what is returned.

    `mode` is "agent" (it edits and runs things) or "plan" (it proposes
    and changes nothing) -- the two the SDK declares.
    """
    emit = on_event or (lambda line: None)
    if not getattr(cfg, "api_key", ""):
        raise CursorCallError(
            "no Cursor API key. Set CURSOR_API_KEY, or put it in "
            "tools/ra_converter/cursor_agent.json (gitignored).", "setup")
    try:
        import cursor_sdk
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ImportError as e:
        raise CursorCallError(
            f"cursor-sdk is not installed ({e}). Run: "
            f"{sys.executable} -m pip install cursor-sdk", "setup") from e
    patch_bridge(ca)
    cwd = os.fspath(cfg.cwd)                 # ONE string, never a list

    if not hasattr(Agent, "create"):
        emit("this cursor-sdk has no Agent.create; using the single "
             "blocking call (no progress until it returns)")
        try:
            out = ca._sdk_prompt(prompt, cfg)
        except Exception as e:                           # noqa: BLE001
            raise CursorCallError(str(e), "run") from e
        return dict(out, model=cfg.model, transport="prompt")

    startup_error = getattr(cursor_sdk, "CursorAgentError", None)
    diagnostics: list = []
    started = time.time()
    try:
        options = AgentOptions(api_key=cfg.api_key, model=cfg.model, mode=mode,
                               local=LocalAgentOptions(cwd=cwd))
        with Agent.create(options) as agent:
            run_ = agent.send(prompt)
            emit("sent; waiting for the agent")
            for message in run_.messages():
                line = describe(message)
                if not line:
                    continue
                emit(line)
                if " error" in line:
                    diagnostics.append(line)
            result = run_.wait()
            text = getattr(result, "result", None) or ""
            if not str(text).strip():
                text = _conversation_text(run_)
            again = followup(str(text)) if followup else None
            if again and str(getattr(result, "status", "")).lower() != "error":
                emit("the first answer was not usable; asking once more on the same agent")
                try:
                    second = agent.send(again)
                    for message in second.messages():
                        line = describe(message)
                        if line:
                            emit(line)
                    result2 = second.wait()
                    text2 = getattr(result2, "result", None) or _conversation_text(second)
                    if str(text2).strip() and str(getattr(result2, "status", "")).lower() != "error":
                        result, text = result2, text2
                except Exception as e:                   # noqa: BLE001
                    emit(f"the second turn failed ({type(e).__name__}: {e}); "
                         f"keeping the first answer")
    except CursorCallError:
        raise
    except Exception as e:                               # noqa: BLE001
        if startup_error is not None and isinstance(e, startup_error):
            raise CursorCallError(
                f"the agent did not start: {e}. This is the key, the network "
                f"path to Cursor, or the local bridge -- not the prompt.",
                "startup") from e
        if getattr(e, "winerror", None) == 10038:
            raise CursorCallError(
                "the Cursor bridge could not start on Windows (WinError 10038). "
                "The patch for this is applied; if it still fails, upgrade "
                "cursor-sdk.", "startup") from e
        raise CursorCallError(f"unexpected error calling the agent: {e}", "run") from e

    status = str(getattr(result, "status", "") or "")
    run_id = str(getattr(result, "id", "") or "")
    if status.lower() == "error":
        raise CursorCallError(
            f"the agent started but did not finish (run id {run_id}, after "
            f"{time.time() - started:.0f}s). Usual causes: a very large "
            f"prompt, a slow network or VPN to Cursor, or the model timing "
            f"out.", "run", run_id=run_id,
            details="\n".join(diagnostics[:12] + ([str(text)[:2000]] if str(text).strip() else [])))
    return {"status": status, "result": str(text), "id": run_id,
            "model": cfg.model, "transport": "session"}
