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
* **A time limit.** `run.wait()` has no timeout of its own, and a bridge
  that has lost its connection waits for ever: the job shows "running"
  until somebody kills it. With `deadline_seconds` the call is made on a
  worker thread and given that long. Past it, the run is asked to
  cancel, the agent to close, and the caller gets a failure of kind
  "timeout" -- stuck is reported as stuck, not as slow. The worker is a
  daemon thread on purpose: a pool used as a context manager would wait
  for the very call that is not returning. (The pattern is from a
  sibling tool that learnt it the same way.)

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

import logging
import os
import sys
import threading
import types
import time

WINDOWS_BRIDGE_TIMEOUT = 90.0


class CursorCallError(RuntimeError):
    """The agent did not produce a usable result.

    kind: "setup"   the SDK or the key is missing
          "startup" the run never started (auth, network, bridge)
          "run"     the run started and failed
          "timeout" the run did not finish inside its time limit
    """

    def __init__(self, message: str, kind: str, run_id: str = "", details: str = "",
                 cancelled=None):
        super().__init__(message)
        self.kind = kind
        self.run_id = run_id
        self.details = details
        # For a timeout: True when the run confirmed it was cancelled,
        # False when it could not be. A caller that lets the agent edit
        # files needs to know which.
        self.cancelled = cancelled


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


_ARG_KEYS = ("command", "cmd", "path", "file_path", "target_file", "file", "pattern",
             "query", "glob", "url", "relative_workspace_path")
QUIET_FOR = 60                               # seconds without a word before the log says so
PULSE_EVERY = 5                              # how often that is looked at
# Seen live (cursor-sdk 1.0.37): a run's `usage` line comes once, when its
# turn has ended -- after every tool call of a code-writing run, after the
# answer of a design. The SDK then never says the run has ended, and ~35 s
# later its bridge may run the whole request again. Nothing after the usage
# line for this long is the end of the run.
AFTER_USAGE_QUIET = 20


def _one_line(value, cap: int) -> str:
    s = " ".join(str(value if value is not None else "").split())
    return s[:cap] + ("..." if len(s) > cap else "")


def _args_line(args) -> str:
    """What a tool was asked to do, in a few words: the command of a shell
    call, the path of a read or an edit. Not the content of an edit."""
    if isinstance(args, dict):
        parts = [f"{k}={_one_line(args[k], 160)}" for k in _ARG_KEYS if args.get(k)]
        if parts:
            return " ".join(parts[:2])
        return _one_line({k: v for k, v in args.items() if k not in ("content", "contents",
                                                                      "new_string", "old_string",
                                                                      "code_edit")}, 160)
    return _one_line(args, 160)


def _assistant_text(message) -> str:
    content = getattr(getattr(message, "message", None), "content", None)
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, (list, tuple)) else []:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return " ".join(parts)


def describe(message, timing: dict = None) -> str:
    """One line for the Cursor log from one streamed message, or "".

    A tool call says what it was asked (`tool shell started: command=mvn
    -q compile`) and, when it ends, how long it took and -- for a shell --
    the start of what came back. What the agent says in words is logged
    as `cursor says:`. `timing` remembers when each call started, across
    the calls of one run.
    """
    kind = getattr(message, "type", "") or ""
    if kind == "status":
        status = getattr(message, "status", "") or ""
        text = getattr(message, "message", "") or ""
        return f"status {status}" + (f": {text}" if text else "")
    if kind == "tool_call":
        name = getattr(message, "name", "") or "?"
        status = getattr(message, "status", "") or ""
        key = (name, getattr(message, "call_id", "") or "")
        if status == "running":
            if timing is not None:
                if key in timing:
                    return ""          # the SDK says "running" twice; once is the news
                timing[key] = time.monotonic()
            asked = _args_line(getattr(message, "args", None))
            return f"tool {name} started" + (f": {asked}" if asked else "")
        line = f"tool {name} {status}".rstrip()
        began = (timing or {}).pop(key, None)
        if began is not None:
            line += f" in {time.monotonic() - began:.0f}s"
        if status == "error":
            line += f": {str(getattr(message, 'result', ''))[:300]}"
        elif status == "completed" and any(w in name.lower() for w in ("shell", "terminal", "run", "exec")):
            shown = _one_line(getattr(message, "result", None), 160)
            if shown:
                line += f" -> {shown}"
        return line
    if kind == "assistant":
        said = _one_line(_assistant_text(message), 300)
        return f"cursor says: {said}" if said else ""
    if kind == "task":
        text = _one_line(getattr(message, "text", ""), 200)
        status = getattr(message, "status", "") or ""
        return f"task {status}: {text}".rstrip(": ") if text or status else ""
    if kind == "request":
        # One line per request the bridge makes to Cursor's backend. One
        # turn, one request -- so a second one in the same turn is a RETRY:
        # the bridge backs off (seconds, then minutes) and runs the turn
        # again. This is the line that shows where a slow run's time went.
        n = (timing or {}).get("_requests", 0) + 1
        if timing is not None:
            timing["_requests"] = n
        rid = _one_line(getattr(message, "request_id", ""), 60)
        return f"backend request #{n}" + (f" {rid}" if rid else "") + (
            "  (a request after the first in one run is a retry after a failure)" if n > 1 else "")
    if kind == "usage":
        u = getattr(message, "usage", None)
        if u is not None:
            return (f"usage: {getattr(u, 'input_tokens', '?')} in, {getattr(u, 'output_tokens', '?')} out, "
                    f"{getattr(u, 'total_tokens', '?')} in all")
        return "usage"
    if kind == "system":
        sub = _one_line(getattr(message, "subtype", ""), 60)
        model = getattr(message, "model", None)
        shown = _one_line(getattr(model, "id", None) or getattr(model, "name", None) or model or "", 60)
        return f"system {sub}".rstrip() + (f" model={shown}" if shown else "")
    if kind in ("thinking", "user"):
        return ""                        # thinking is private; the user message is the prompt
    if kind:
        return f"cursor {kind}"          # a kind this log did not know: named, not dropped
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


def _best_effort(obj, method: str, seconds: float = 5.0) -> bool:
    """Call obj.method() without letting it hang this thread too."""
    fn = getattr(obj, method, None)
    if not callable(fn):
        return False
    done = []

    def go():
        try:
            fn()
            done.append(True)
        except Exception:                                # noqa: BLE001
            pass

    th = threading.Thread(target=go, name=f"cursor-{method}", daemon=True)
    th.start()
    th.join(seconds)
    return bool(done)


class WentQuiet(TimeoutError):
    """The run was given up on because nothing had come from it for a
    while, not because the wall-clock limit ran out."""


def bounded(work, seconds, on_timeout=None, stop=None, on_stop=None):
    """work() with a wall-clock limit. Its value or its exception; or
    TimeoutError, after `on_timeout()` has been called. With `stop` (an
    Event) set by somebody else, the wait ends early: `on_stop()` is
    called and WentQuiet is raised."""
    if (not seconds or seconds <= 0) and stop is None:
        return work()
    box = {}

    def target():
        try:
            box["value"] = work()
        except BaseException as e:                       # noqa: BLE001
            box["error"] = e

    th = threading.Thread(target=target, name="cursor-call", daemon=True)
    th.start()
    # In slices: on Windows one long join() is not interrupted by Ctrl+C
    # until it expires, which here is up to 45 minutes.
    end = time.monotonic() + (seconds if seconds and seconds > 0 else float("inf"))
    try:
        while th.is_alive():
            if stop is not None and stop.is_set():
                break
            left = end - time.monotonic()
            if left <= 0:
                break
            th.join(min(0.5, left))
    except BaseException:                   # Ctrl+C while waiting: still cancel
        if on_timeout:
            on_timeout()
        raise
    if th.is_alive():
        if stop is not None and stop.is_set():
            if on_stop:
                on_stop()
            raise WentQuiet("nothing more came from the run")
        if on_timeout:
            on_timeout()
        raise TimeoutError(f"no result after {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _bridge_stderr(agent, sdk):
    """The stderr pipe of the bridge process the SDK launched, when it can
    be found. After startup nothing in the SDK reads it (1.0.37): the
    bridge's account of retries and backoff goes into a pipe nobody
    drains. Best effort; None when the shape is not the one known."""
    for owner in (getattr(getattr(agent, "client", None), "_owned_bridge", None),
                  getattr(getattr(sdk, "_client", None), "_DEFAULT_BRIDGE", None)):
        process = getattr(owner, "process", None)
        stream = getattr(process, "stderr", None)
        if stream is not None and hasattr(stream, "readline"):
            return stream
    return None


class _ForwardToLog(logging.Handler):
    def __init__(self, emit):
        super().__init__(level=logging.INFO)
        self._emit = emit

    def emit(self, record):                              # noqa: A003
        try:
            self._emit(f"sdk: {record.getMessage()}"[:600])
        except Exception:                                # noqa: BLE001
            pass


def run(prompt: str, ca, cfg, on_event=None, followup=None, mode: str = "agent",
        deadline_seconds: float = 0, quiet_seconds: float = 0, answer_complete=None) -> dict:
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

    `deadline_seconds`: the whole call, both turns, gets this long. 0 is
    no limit. See the module docstring.

    `quiet_seconds`: once something has come from Cursor, this long with
    nothing more is taken as the end of the run: it is cancelled and what
    it said so far is the answer, with status "quiet". A run that writes
    its files, sums up, and then never signals the end -- seen live, 18
    minutes of silence up to the hard limit, after which everything it
    had written was undone -- is finished work, and is treated as such.
    0 is off.

    `answer_complete(text)`: for a call that wants ONE answer (a design, a
    review). Each time a model call ends (its `usage` line), the answer it
    just wrote is given to it; True ends the run there, with that answer,
    status "answered". Seen live with cursor-sdk 1.0.37: the answer and its
    usage line came, the connection then sat idle, the bridge called it
    "Connection stalled" (a retryable error) about 35 s later and ran the
    SAME request again -- the same input tokens -- again and again until the
    time limit. Each round is paid for.
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
    started = time.time()
    live = {}                                # what to cancel if time runs out
    stopped = threading.Event()              # a late event is not logged as live

    def gave_up():
        stopped.set()
        live["cancelled"] = _best_effort(live.get("run"), "cancel")
        _best_effort(live.get("agent"), "close")
        emit(f"time limit of {deadline_seconds:.0f}s reached; "
             + ("the run was cancelled" if live["cancelled"] else
                "the run could not be cancelled and may still be working"))

    def went_quiet():
        stopped.set()
        live["cancelled"] = _best_effort(live.get("run"), "cancel")
        _best_effort(live.get("agent"), "close")
        if answered.get("text") is not None:
            return                          # ended because the answer was complete
        if turn["closed"]:
            emit(f"Cursor's turn ended (its usage line) and nothing came for {AFTER_USAGE_QUIET} s: "
                 f"the run is taken as finished"
                 + ("" if live["cancelled"] else " (it could not be cancelled and may still be working)")
                 + "; what it wrote is checked now")
            return
        emit(f"nothing came from Cursor for {quiet_seconds / 60:.0f} min after its last word: "
             f"the run is taken as finished"
             + ("" if live["cancelled"] else " (it could not be cancelled and may still be working)")
             + "; what it wrote is checked now")

    def timed_out(e) -> CursorCallError:
        return CursorCallError(
            f"Cursor did not finish within {deadline_seconds:.0f}s and was "
            f"stopped waiting for. This is a stuck or very slow run, not a "
            f"refusal: check the network path to Cursor, make the request "
            f"smaller, or raise the limit."
            + ("" if live.get("cancelled") else
               " The run could NOT be confirmed cancelled and may still be "
               "working."), "timeout", cancelled=bool(live.get("cancelled")))

    class _Abandoned(Exception):
        """Raised inside the worker once nobody is waiting for it."""

    def still_wanted(agent=None, run_=None) -> None:
        # The limit can pass while the worker is inside create() or send(),
        # before there was anything to cancel. Without this it would carry
        # on -- in agent mode, editing files -- with nobody watching.
        if stopped.is_set():
            _best_effort(run_, "cancel")
            _best_effort(agent, "close")
            raise _Abandoned()

    if not hasattr(Agent, "create"):
        emit("this cursor-sdk has no Agent.create; using the single "
             "blocking call (no progress until it returns)")
        try:
            out = bounded(lambda: ca._sdk_prompt(prompt, cfg), deadline_seconds, gave_up)
        except TimeoutError as e:
            raise timed_out(e) from None
        except Exception as e:                           # noqa: BLE001
            raise CursorCallError(str(e), "run") from e
        return dict(out, model=cfg.model, transport="prompt")

    startup_error = getattr(cursor_sdk, "CursorAgentError", None)
    diagnostics: list = []

    last = {"at": time.monotonic(), "line": ""}
    heard = {"any": False}          # has anything at all come from the run?
    quiet_stop = threading.Event()
    transcript: list = []           # every `cursor says:` whole, for the log
    # What Cursor wrote, exactly as it came: the answer of a run that is
    # ended for going quiet or for the time limit. The log's pieces are cut
    # at paragraphs and at 1,500 characters; joined again they would put a
    # blank line inside a JSON string, and a design reply is JSON.
    raw = {"text": ""}
    # The answers one by one: a stretch of assistant text with nothing else
    # between its pieces. A request run again writes its answer again from
    # the start; the last complete one is the answer.
    segments: list = []
    seg = {"open": False}
    answered = {"text": None}
    usages: list = []
    # `ended`: the last thing Cursor sent was a usage line (its turn ended);
    # `closed`: the run was ended for that.
    turn = {"ended": False, "closed": False}

    def best_answer() -> str:
        """The answer to keep when the run is ended from outside: the last
        complete one when `answer_complete` says which; else the last one
        written (a request run again starts its answer again)."""
        for s in reversed(segments):
            if answer_complete is not None:
                try:
                    if answer_complete(s):
                        return s.strip()
                except Exception:                        # noqa: BLE001
                    pass
        return (segments[-1].strip() if segments else "") or raw["text"].strip()

    def say(line: str) -> None:
        if not stopped.is_set():
            last["at"], last["line"] = time.monotonic(), line
            emit(line)

    def pulse(done: threading.Event) -> None:
        """While nothing is heard from Cursor, say so once a minute: a log
        that stops at `tool shell started` reads as a hang, and most often
        it is a long compile.

        The silence is counted from the last thing Cursor sent, not from
        the last heartbeat line: the heartbeat used to reset it, so the
        silence never passed a minute and `quiet_seconds` (600 for a
        code-writing run) could never end a finished run -- it waited for
        the 45-minute limit instead."""
        last_pulse = time.monotonic()
        while not done.wait(PULSE_EVERY):
            now = time.monotonic()
            quiet = now - last["at"]
            if stopped.is_set():
                continue
            if quiet_seconds and quiet_seconds > 0 and heard["any"] and quiet >= quiet_seconds:
                quiet_stop.set()
                return
            if turn["ended"] and answer_complete is None and quiet >= AFTER_USAGE_QUIET:
                turn["closed"] = True
                quiet_stop.set()
                return
            if quiet >= QUIET_FOR and now - last_pulse >= QUIET_FOR:
                emit(f"still working: nothing from Cursor for {quiet / 60:.0f} min"
                     + (f" (last: {last['line'][:100]})" if last["line"] else "")
                     + f", {(now - t0) / 60:.0f} min in all")
                last_pulse = now
    t0 = time.monotonic()
    timing: dict = {}
    # What Cursor says arrives in pieces -- a word, a letter at a time --
    # and a log line a letter is no log line. The pieces are gathered and
    # said whole: when Cursor turns to a tool, pauses at a paragraph, or
    # the run ends. Meanwhile each piece counts as a sign of life, and the
    # heartbeat shows the beginning of what is being written.
    said = {"text": ""}

    def flush_said() -> None:
        whole = _one_line(said["text"], 300)
        if said["text"].strip():
            transcript.append(said["text"].strip())
        said["text"] = ""
        if whole:
            say(f"cursor says: {whole}")

    # The model reasoning before it answers arrives as `thinking` messages.
    # Their text is private and is not logged, but they ARE Cursor working:
    # each one counts as a sign of life (the heartbeat does not say
    # "nothing from Cursor", and a long think after the first words is not
    # taken for a run gone quiet), and the log says when thinking starts
    # and how long it took.
    thought = {"on": False, "t": 0.0, "ms": 0, "chars": 0}

    def end_thinking() -> None:
        if not thought["on"]:
            return
        thought["on"] = False
        secs = (thought["ms"] / 1000.0) if thought["ms"] else (time.monotonic() - thought["t"])
        say(f"thought for {secs:.0f}s ({thought['chars']:,} characters of reasoning, not shown)")

    plans = {}                      # call id -> its segment, for a createPlan call
    pending = {"again": None}       # the follow-up prompt, when a turn ended unusable

    def consume(messages) -> str:
        """Log each message. Returns "incomplete" when a one-answer call's
        turn ends (its usage line) without a whole answer, so that the
        session can ask once more instead of waiting for an end the SDK
        never signals; else ""."""
        for message in messages:
            heard["any"] = True
            turn["ended"] = (getattr(message, "type", "") or "") == "usage"
            if (getattr(message, "type", "") or "") == "thinking":
                if not thought["on"]:
                    flush_said()
                    thought.update(on=True, t=time.monotonic(), ms=0, chars=0)
                    say("cursor is thinking")
                thought["chars"] += len(str(getattr(message, "text", "") or ""))
                ms = getattr(message, "thinking_duration_ms", None)
                if isinstance(ms, int) and ms > 0:
                    thought["ms"] = ms
                last["at"] = time.monotonic()
                last["line"] = "cursor is thinking"
                seg["open"] = False
                continue
            end_thinking()
            if (getattr(message, "type", "") or "") != "assistant":
                seg["open"] = False
            if (getattr(message, "type", "") or "") == "assistant":
                piece = _assistant_text(message)
                if piece:
                    if not seg["open"]:
                        segments.append("")
                        seg["open"] = True
                    segments[-1] += piece
                    raw["text"] += piece
                    said["text"] += piece
                    last["at"] = time.monotonic()
                    last["line"] = f"cursor is writing: {_one_line(said['text'], 80)}"
                    if said["text"].rstrip(" ").endswith("\n\n") or len(said["text"]) > 1500:
                        flush_said()
                continue
            flush_said()
            line = describe(message, timing)
            # Seen live (composer-2.5, plan mode): the answer went into a
            # createPlan tool call, not into the reply. Its `plan` is taken
            # as an answer like any other.
            if (getattr(message, "type", "") or "") == "tool_call" and \
                    "plan" in str(getattr(message, "name", "") or "").lower():
                args = getattr(message, "args", None)
                plan = (args.get("plan") if isinstance(args, dict) else getattr(args, "plan", None)) or ""
                if str(plan).strip():
                    cid = str(getattr(message, "call_id", "") or id(message))
                    if cid in plans:
                        segments[plans[cid]] = str(plan)
                    else:
                        plans[cid] = len(segments)
                        segments.append(str(plan))
                    seg["open"] = False
                    say(f"cursor's plan: {len(str(plan)):,} characters, taken as its answer")
            if (getattr(message, "type", "") or "") == "usage":
                u = getattr(message, "usage", None)
                tokens_in = getattr(u, "input_tokens", None)
                if tokens_in is not None and tokens_in in usages:
                    say(f"the same request ran again ({tokens_in} tokens in, as before): the SDK's "
                        f"bridge re-ran the turn after the connection sat idle. Each run is paid for.")
                if tokens_in is not None:
                    usages.append(tokens_in)
                if answer_complete is not None and segments and answered["text"] is None:
                    try:
                        whole = bool(answer_complete(segments[-1]))
                    except Exception:                    # noqa: BLE001
                        whole = False
                    if whole:
                        answered["text"] = segments[-1]
                        if line:
                            say(line)
                        say("the answer is complete: the run is ended here, with it")
                        quiet_stop.set()
                        return ""
                # A usage line without a whole answer is NOT the end: seen live
                # (composer-2.5, plan mode), a createPlan call's model step ended
                # with one, and the JSON answer was still to come in the same
                # run -- asking again then failed ("already has active run").
                # The run is read on; the answer ends it when it comes whole.
            if not line:
                continue
            say(line)
            if " error" in line:
                diagnostics.append(line)
        end_thinking()
        flush_said()
        return ""

    def drain_bridge(agent) -> None:
        """The bridge's stderr, line by line, into the log as `bridge:`."""
        stream = _bridge_stderr(agent, cursor_sdk)
        if stream is None:
            return

        def pump():
            try:
                for raw in iter(stream.readline, ""):
                    line = raw.rstrip() if isinstance(raw, str) else raw.decode("utf-8", "replace").rstrip()
                    if line and not stopped.is_set():
                        emit(f"bridge: {line[:600]}")
            except Exception:                            # noqa: BLE001
                pass
        threading.Thread(target=pump, daemon=True, name="cursor-bridge-stderr").start()

    def session():
        options = AgentOptions(api_key=cfg.api_key, model=cfg.model, mode=mode,
                               local=LocalAgentOptions(cwd=cwd))
        with Agent.create(options) as agent:
            live["agent"] = agent
            still_wanted(agent)
            drain_bridge(agent)
            run_ = agent.send(prompt)
            live["run"] = run_
            still_wanted(agent, run_)
            say("sent; waiting for the agent")
            if consume(run_.messages()) == "incomplete":
                # The turn ended (its usage line) without a whole answer.
                # run_.wait() would wait for an end the SDK does not signal:
                # ask once more on the same agent now, or end with what came.
                end_thinking()
                flush_said()
                again = pending["again"]
                if again and not stopped.is_set():
                    say("the answer was not usable; asking once more on the same agent")
                    try:
                        second = agent.send(again)
                        live["run"] = second
                        raw["text"] = ""
                        turn["ended"] = False
                        last["at"] = time.monotonic()
                        consume(second.messages())
                    except Exception as e:               # noqa: BLE001
                        say(f"the second turn failed ({type(e).__name__}: {e})")
                if answered["text"] is None:
                    say("no whole answer came; the run is ended with what Cursor wrote")
                    turn["closed"] = True
                return (types.SimpleNamespace(status="quiet", id=str(getattr(run_, "id", "") or "")),
                        answered["text"] if answered["text"] is not None else best_answer())
            if answered["text"] is not None:
                # The whole answer came: nothing more is waited for or asked.
                return (types.SimpleNamespace(status="answered", id=str(getattr(run_, "id", "") or "")),
                        answered["text"])
            result = run_.wait()
            text = getattr(result, "result", None) or ""
            if not str(text).strip():
                text = _conversation_text(run_)
            still_wanted(agent, run_)       # no second, paid turn for nobody
            again = followup(str(text)) if followup else None
            if again and str(getattr(result, "status", "")).lower() != "error":
                say("the first answer was not usable; asking once more on the same agent")
                try:
                    second = agent.send(again)
                    live["run"] = second
                    raw["text"] = ""            # the answer is now this turn's
                    consume(second.messages())
                    result2 = second.wait()
                    text2 = getattr(result2, "result", None) or _conversation_text(second)
                    if str(text2).strip() and str(getattr(result2, "status", "")).lower() != "error":
                        result, text = result2, text2
                except Exception as e:                   # noqa: BLE001
                    say(f"the second turn failed ({type(e).__name__}: {e}); "
                        f"keeping the first answer")
            return result, text

    done = threading.Event()
    threading.Thread(target=pulse, args=(done,), daemon=True, name="cursor-pulse").start()
    # What the SDK says to its own logger (connects, retries, errors) is
    # part of this run's story: it goes into the same log while it runs.
    sdk_log = logging.getLogger("cursor_sdk")
    forward = _ForwardToLog(emit)
    sdk_log.addHandler(forward)
    if sdk_log.level == logging.NOTSET or sdk_log.level > logging.INFO:
        sdk_log.setLevel(logging.INFO)
    try:
        result, text = bounded(session, deadline_seconds, gave_up, stop=quiet_stop, on_stop=went_quiet)
        if answered["text"] is not None:
            # The whole answer came, and the run ended on its own before the
            # stop was noticed: the answer is still the one kept.
            done.set()
            return {"status": "answered", "result": answered["text"].strip(),
                    "id": str(getattr(result, "id", "") or ""), "model": cfg.model,
                    "transport": "session", "answered": True}
    except CursorCallError:
        raise
    except WentQuiet:
        done.set()
        run_id = str(getattr(live.get("run"), "id", "") or "")
        # The last words are usually still in the gather buffer: nothing
        # came after them to flush it.
        parts = transcript + ([said["text"].strip()] if said["text"].strip() else [])
        if answered["text"] is not None:
            return {"status": "answered", "result": answered["text"].strip(), "id": run_id,
                    "model": cfg.model, "transport": "session", "answered": True}
        return {"status": "quiet", "result": best_answer() or "\n\n".join(parts), "id": run_id,
                "model": cfg.model, "transport": "session", "quiet": True}
    except TimeoutError as e:
        # The hard limit, after Cursor had answered (seen live: the summary
        # at minute 42, the limit at 45). The answer is kept and what was
        # written is checked -- when the run is known to have stopped. A run
        # that could not be cancelled may still be writing: that stays a
        # failure, with the putting-back and the waiting the loop does.
        parts = transcript + ([said["text"].strip()] if said["text"].strip() else [])
        if parts and live.get("cancelled"):
            done.set()
            run_id = str(getattr(live.get("run"), "id", "") or "")
            emit("the time limit came after Cursor had answered; the answer is kept and what it "
                 "wrote is checked")
            return {"status": "timeout", "result": best_answer() or "\n\n".join(parts), "id": run_id,
                    "model": cfg.model, "transport": "session", "ended_in_timeout": True,
                    "cancelled": True}
        raise timed_out(e) from None
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
    finally:
        done.set()
        sdk_log.removeHandler(forward)

    status = str(getattr(result, "status", "") or "")
    run_id = str(getattr(result, "id", "") or "")
    if status.lower() == "error":
        if str(text).strip():
            # The SDK's last word on the run was "error", yet the agent had
            # answered -- seen live with the whole summary of the files it
            # wrote. The answer is kept; what was written is for the verify
            # step to judge, not a status word. The error is on the record.
            emit(f"the run ended with status 'error' (run id {run_id}, after "
                 f"{time.time() - started:.0f}s) but had answered; the answer is kept and what "
                 f"it wrote is checked" + (": " + "; ".join(diagnostics[:3]) if diagnostics else ""))
            # One answer, not every round of a request the bridge ran again
            # (the transcript joins them all).
            return {"status": status, "result": best_answer() or str(text), "id": run_id, "model": cfg.model,
                    "transport": "session", "ended_in_error": True,
                    "diagnostics": diagnostics[:12]}
        raise CursorCallError(
            f"the agent started but did not finish (run id {run_id}, after "
            f"{time.time() - started:.0f}s). Usual causes: a very large "
            f"prompt, a slow network or VPN to Cursor, or the model timing "
            f"out.", "run", run_id=run_id,
            details="\n".join(diagnostics[:12]))
    return {"status": status, "result": str(text), "id": run_id,
            "model": cfg.model, "transport": "session"}
