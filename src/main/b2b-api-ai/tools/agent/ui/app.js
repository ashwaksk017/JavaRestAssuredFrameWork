"use strict";
// The page holds no state. Everything it shows comes from the job
// directory, so a refresh loses nothing and every run is reproducible
// from the CLI with the same arguments.

const $ = (id) => document.getElementById(id);
const job = () => $("job").value.trim();
// `?job=name` in the address opens the page on that job, so a result can
// be linked to: http://127.0.0.1:8787/?job=release-6#jira
(function jobFromAddress() {
  const j = new URLSearchParams(location.search).get("job") || "";
  if (/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(j)) $("job").value = j;
})();

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({ error: "not JSON" }));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}
const post = (path, payload) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload) });

// ---- mock mode -------------------------------------------------------
// When Jira or Cursor is a stand-in (tools/agent/mock.json), the page says
// so on every tab, for as long as it is so. Asked again every few seconds:
// the file can be edited while the page is open, and a banner that
// outlives the switch is as wrong as one that is missing.
async function showMockBanner() {
  const bar = $("mock-banner");
  let m;
  try { m = await api("/api/mock"); }
  catch (e) { m = { jira: true, cursor: true, error: `the server did not say (${e.message}).` }; }
  const which = [m.jira ? "Jira" : "", m.cursor ? "Cursor" : ""].filter(Boolean);
  bar.className = "mockbar" + (m.error ? " broken" : "");
  if (m.error) {
    bar.replaceChildren(el("strong", { text: "MOCK SETTINGS CANNOT BE READ" }),
      ` — ${m.error} Until it is fixed, treat everything here as not real.`);
  } else if (which.length) {
    // Two words. Which service only when it is not both; the rest is in
    // the tooltip and the README.
    bar.replaceChildren(el("strong", { text: "MOCK MODE" }),
      which.length === 1 ? ` — ${which[0]} only` : "");
    bar.title = `${which.join(" and ")} ${which.length > 1 ? "are" : "is"} simulated on this ` +
      "machine. To use the real one, set it to false in tools/agent/mock.json. Confluence " +
      "links, the converter (and its own Cursor assist) and git are never simulated.";
  }
  if (m.error || !which.length) bar.removeAttribute("title");
  bar.hidden = !(m.error || which.length);
}
setInterval(showMockBanner, 5000);

// ---- tabs ------------------------------------------------------------
// The tab is also in the address (#jira, #failures), so a tab can be
// linked to and a refresh stays where it was.
const onTab = {};                 // tab name -> what to do when it is opened
document.querySelectorAll(".tabs button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((x) =>
      x.setAttribute("aria-selected", String(x === b)));
    document.querySelectorAll(".tab").forEach((s) =>
      s.hidden = s.id !== `tab-${b.dataset.tab}`);
    try { history.replaceState(null, "", `#${b.dataset.tab}`); } catch { /* file:// */ }
    if (onTab[b.dataset.tab]) onTab[b.dataset.tab]();
  };
});

// ---- following a run -------------------------------------------------
// One poller per (job, runnable). It appends only what is new, so a long
// convert does not re-render its whole log every second.
function follow(runnable, logEl, stateEl, onDone) {
  // The job is read ONCE. Re-reading the text box every tick would switch
  // this poller to another job's log if the box is edited mid-run.
  const j = job();
  let offset = 0;
  let misses = 0;
  logEl.textContent = "";
  setState(stateEl, "run", "running…");
  const tick = async () => {
    try {
      const chunk = await api(
        `/api/log?job=${encodeURIComponent(j)}&runnable=${runnable}&offset=${offset}`);
      if (chunk.text) {
        offset = chunk.offset;
        logEl.textContent += chunk.text;
        logEl.scrollTop = logEl.scrollHeight;
      }
      const st = await api(
        `/api/status?job=${encodeURIComponent(j)}&runnable=${runnable}`);
      misses = 0;
      if (st.state === "running") { setTimeout(tick, 900); return; }
      if (st.state === "done") setState(stateEl, "ok", "finished");
      else setState(stateEl, "bad", `exit ${st.exit_code}`);
      if (onDone) onDone(st);
    } catch (e) {
      // One failed request is not the end of the run. Giving up at once
      // re-enabled buttons while the job was still going.
      misses += 1;
      if (misses <= 8) {
        setState(stateEl, "run", `running… (not reachable just now, retry ${misses})`);
        setTimeout(tick, 1500);
        return;
      }
      setState(stateEl, "bad", `${e.message} — the run may still be going; refresh to see`);
      // Whoever started this is waiting to tidy up (a timer, a disabled
      // button). The run's own state is unknown; say so, do not hang.
      if (onDone) onDone({ state: "unknown" });
    }
  };
  tick();
}
function setState(el, cls, text) {
  el.className = `state ${cls}`;
  el.textContent = text;
}

async function showArtifact(name, el) {
  try {
    const r = await api(
      `/api/artifact?job=${encodeURIComponent(job())}&name=${name}`);
    el.textContent = r.text || "—";
  } catch (e) { el.textContent = e.message; }
}

// ---- tab 1 -----------------------------------------------------------
$("run-intake").onclick = async () => {
  try {
    await post("/api/intake", {
      job: job(),
      text: $("text").value,
      links: $("links").value.split("\n").map((s) => s.trim()).filter(Boolean),
    });
    follow("intake", $("new-log"), $("new-state"),
           () => showArtifact("brief.md", $("brief")));
  } catch (e) { setState($("new-state"), "bad", e.message); }
};

$("run-locate").onclick = async () => {
  try {
    await post("/api/start",
               { job: job(), runnable: "locate", options: { "--job": job() } });
    follow("locate", $("new-log"), $("new-state"), async (st) => {
      await showArtifact("plan.md", $("plan"));
      // locate exits 1 when a request STOPS -- upstream, or needs a
      // human. That is the stage working, not failing, and painting it
      // red teaches people to ignore the colour.
      if (st.exit_code === 1) {
        try {
          const r = await api(
            `/api/artifact?job=${encodeURIComponent(job())}&name=locate.json`);
          const n = (JSON.parse(r.text || "{}").stops || []).length;
          if (n) setState($("new-state"), "warn",
                          `${n} request(s) need a decision — see the plan`);
        } catch { /* leave the generic state */ }
      }
    });
  } catch (e) { setState($("new-state"), "bad", e.message); }
};

$("run-jira-verify").onclick = async () => {
  try {
    await post("/api/start", { job: job(), runnable: "jira-verify", options: {} });
    follow("jira-verify", $("new-log"), $("new-state"));
  } catch (e) { setState($("new-state"), "bad", e.message); }
};

// ---- tab 1, second half: design the API tests ------------------------
// A chosen file is read here, in the browser, into its box: what is sent
// is what is on screen, and there is no upload route to guard.
for (const [picker, box] of [["design-swagger-file", "design-swagger"],
                             ["design-requirements-file", "design-requirements"]]) {
  $(picker).onchange = async () => {
    const f = $(picker).files[0];
    if (f) $(box).value = await f.text();
  };
}

// `j` is the job the run was started for, not whatever the box holds now.
async function showDesign(j) {
  j = j || job();
  let have = false;
  try {
    const r = await api(`/api/artifact?job=${encodeURIComponent(j)}&name=design.md`);
    $("design-doc").textContent = r.text || "—";
    have = Boolean(r.text);
  } catch (e) { $("design-doc").textContent = e.message; }
  for (const [id, name] of [["dl-design", "design.md"], ["dl-cases", "test-cases.csv"],
                            ["dl-xray", "xray.csv"]]) {
    $(id).hidden = !have;
    $(id).href = `/api/download?job=${encodeURIComponent(j)}&name=${name}`;
  }
}

let designCursorTimer = null;
// cursor.log is appended to across runs. This pane shows THIS run: it
// starts at the end the file has now, not at the beginning.
async function followDesignCursorLog(j) {
  let offset = 0;
  const el = $("design-cursor-log");
  el.textContent = "";
  clearInterval(designCursorTimer);
  try {
    offset = (await api(
      `/api/log?job=${encodeURIComponent(j)}&runnable=cursor&offset=0`)).offset || 0;
  } catch { /* no cursor log yet: start at 0 */ }
  const read = async () => {
    try {
      const chunk = await api(
        `/api/log?job=${encodeURIComponent(j)}&runnable=cursor&offset=${offset}`);
      if (chunk.text) {
        offset = chunk.offset;
        el.textContent += chunk.text;
        el.scrollTop = el.scrollHeight;
      }
    } catch { /* no cursor log yet */ }
  };
  designCursorTimer = setInterval(read, 1500);
  return () => { clearInterval(designCursorTimer); read(); };
}

$("run-design").onclick = async () => {
  const button = $("run-design");
  // One at a time: a second click used to wipe the Cursor pane of the run
  // already going, fail with "already running", and stop the pane's timer.
  button.disabled = true;
  let stopCursorLog = () => {};
  try {
    const j = job();
    // Where the Cursor log ends NOW, read before the run starts, so the
    // pane shows this run and nothing is lost between start and first poll.
    stopCursorLog = await followDesignCursorLog(j);
    await post("/api/design", {
      job: j,
      service: $("design-service").value.trim(),
      speed: $("design-speed").value,
      mode: $("design-mode").value,
      fresh: $("design-fresh").checked,
      swagger: $("design-swagger").value,
      requirements: $("design-requirements").value,
      notes: $("design-notes").value,
    });
    follow("agent-design", $("design-log"), $("design-state"), () => {
      stopCursorLog();
      showDesign(j);
      // One run. Left ticked, every later click would pay again in silence.
      $("design-fresh").checked = false;
      button.disabled = false;
    });
  } catch (e) {
    stopCursorLog();
    button.disabled = false;
    setState($("design-state"), "bad", e.message);
  }
};
// A design made earlier is still this job's design after a refresh.
showDesign();
$("job").addEventListener("change", () => showDesign());
$("stop-design").onclick = async () => {
  try { await post("/api/stop", { job: job(), runnable: "agent-design" }); }
  catch (e) { setState($("design-state"), "bad", e.message); }
};

// ---- tab 2: a form built from what the runnable DECLARES -------------
// Not a hand-written list: the server is the source of truth for which
// options exist, so the form cannot drift from the CLI.
const NOTE = {
  "--output": "Where the converted tree is written. A mistyped value overwrites a real tree.",
  "--input": "One XML, a comma-separated list, or a directory. Only the directory form is authoritative.",
  "--data-dir": "The DataSource workbooks. Optional — a suite with no workbooks needs none. When the suite HAS them, forgetting this is the usual cause of a bad run.",
  "--clean": "DELETES this suite's previously-generated files first.",
  "--classic": "Whole-tree: one output holds one mode. Mixing is refused.",
  "--skip-self-test": "Skips the converter's own unit checks before emitting.",
};
// --data-dir is NOT required: the converter does not require it either,
// and a suite whose ReadyAPI project has no DataSource workbooks has
// nothing to point it at. It is ADVISED instead -- forgetting it when
// the suite does read workbooks is still the usual cause of a bad run,
// so the form says so without refusing to run.
const REQUIRED = ["--input", "--output"];
const ADVISED = ["--data-dir"];
const PRIMARY = ["--input", "--output", "--data-dir", "--package-root",
                 "--suite-name", "--envs", "--config"];

(async function buildConvertForm() {
  let spec;
  try { spec = (await api("/api/runnables")).convert; }
  catch { return; }
  const form = $("convert-form");
  const entries = Object.entries(spec.options);
  const order = (n) => {
    const i = PRIMARY.indexOf(n);
    return i < 0 ? 100 + (spec.options[n] === "flag" ? 50 : 0) : i;
  };
  entries.sort((a, b) => order(a[0]) - order(b[0]));

  for (const [name, kind] of entries) {
    const field = document.createElement("div");
    field.className = `field ${kind === "flag" ? "flag" : ""} ` +
                      (REQUIRED.includes(name) ? "req" : "") + " " +
                      (ADVISED.includes(name) ? "advised" : "");
    const id = `opt${name.replace(/-/g, "_")}`;
    if (kind === "flag") {
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.id = id; cb.dataset.opt = name;
      const lb = document.createElement("label");
      lb.htmlFor = id; lb.textContent = name; lb.style.margin = "0";
      field.append(cb, lb);
    } else {
      const lb = document.createElement("label");
      lb.htmlFor = id; lb.textContent = name;
      const inp = document.createElement("input");
      inp.type = "text"; inp.id = id; inp.dataset.opt = name;
      inp.spellcheck = false;
      field.append(lb, inp);
    }
    if (NOTE[name]) {
      const s = document.createElement("small");
      s.textContent = NOTE[name];
      field.append(s);
    }
    form.append(field);
  }
  form.addEventListener("input", warn);
  form.addEventListener("change", warn);
})();

function convertOptions() {
  const out = {};
  document.querySelectorAll("#convert-form [data-opt]").forEach((el) => {
    if (el.type === "checkbox") { if (el.checked) out[el.dataset.opt] = true; }
    else if (el.value.trim()) out[el.dataset.opt] = el.value.trim();
  });
  return out;
}

// Say what a destructive combination will do BEFORE the run, not after.
function warn() {
  const o = convertOptions();
  const msgs = [];
  if (o["--clean"]) {
    msgs.push(`<code>--clean</code> deletes the previously-generated files for` +
      (o["--suite-name"] ? ` suite <code>${esc(o["--suite-name"])}</code>`
                         : " this suite") +
      ` under <code>${esc(o["--output"] || "the output directory")}</code>.`);
  }
  if (o["--classic"]) {
    msgs.push("<code>--classic</code> is whole-tree. If that output already " +
              "holds phase-mode suites the converter will refuse, and " +
              "converting everything classic replaces them.");
  }
  for (const r of REQUIRED) {
    if (!o[r]) msgs.push(`<code>${r}</code> is not set.`);
  }
  if (!o["--data-dir"]) {
    msgs.push("<code>--data-dir</code> is not set. Fine for a suite whose " +
              "ReadyAPI project has no DataSource workbooks. If it does " +
              "have them, every DataSource column comes out EMPTY and the " +
              "run looks like an API problem rather than a missing path.");
  }
  const box = $("convert-warnings");
  box.hidden = msgs.length === 0;
  box.innerHTML = msgs.length ? "<ul><li>" + msgs.join("</li><li>") + "</li></ul>" : "";
}
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

$("run-convert").onclick = async () => {
  const o = convertOptions();
  if (o["--clean"] &&
      !confirm("--clean deletes previously-generated files under the output " +
               "directory before writing. Continue?")) return;
  try {
    await post("/api/start",
               { job: job(), runnable: "convert", options: o });
    follow("convert", $("convert-log"), $("convert-state"));
  } catch (e) { setState($("convert-state"), "bad", e.message); }
};

$("stop-convert").onclick = async () => {
  try { await post("/api/stop", { job: job(), runnable: "convert" }); }
  catch (e) { setState($("convert-state"), "bad", e.message); }
};

for (const [btn, runnable] of [["run-keys", "audit-service-keys"],
                               ["run-token", "audit-token-chain"]]) {
  $(btn).onclick = async () => {
    const o = {};
    const out = convertOptions()["--output"];
    if (out) o["--root"] = out;
    try {
      await post("/api/start", { job: job(), runnable, options: o });
      follow(runnable, $("convert-log"), $("convert-state"));
    } catch (e) { setState($("convert-state"), "bad", e.message); }
  };
}

// ---- tab 3: the agent loop --------------------------------------------
// The state shown here is review.json in the job directory, written by
// tools/agent/loop.py. The page decides nothing: the push button is
// enabled only when that file says "pending-review", and the server-side
// command refuses anything else whatever the page sends.
const REVIEW_TEXT = {
  "none": "No agent run for this job yet.",
  "generating": "Cursor is working… (if the run was stopped or died, Discard puts everything back)",
  "rejected": "Rejected — the agent broke a rule.",
  "verify-failed": "Did not verify. The files are still in the working tree.",
  "pending-review": "PENDING REVIEW — read the diff, then approve or discard.",
  "pushed": "Pushed.",
  "approved-local": "Approved. The converted files stay on this machine; the " +
                    "change is stored and is put back after every convert " +
                    "of the suite.",
  "push-failed": "Committed locally, but the push failed. Approve again to retry.",
  "discarded": "Discarded.",
};

async function refreshReview() {
  let review = { state: "none" };
  const shown = job();
  // The buttons act on the job whose review is on screen, not on whatever
  // is typed in the box when they are clicked.
  $("agent-push").dataset.job = shown;
  $("agent-discard").dataset.job = shown;
  try {
    const r = await api(
      `/api/artifact?job=${encodeURIComponent(shown)}&name=review.json`);
    if (r.text) review = JSON.parse(r.text);
  } catch { /* no review yet */ }
  const state = review.state || "none";
  $("review-box").hidden = false;
  const badge = $("review-badge");
  badge.textContent = state.replace("-", " ");
  badge.className = `badge ${state}`;
  let detail = REVIEW_TEXT[state] || state;
  if (review.mock) detail = "MOCK RUN — written by the stand-in for Cursor; it cannot be " +
                            "approved, only discarded. " + detail;
  if (review.reason) detail += " " + review.reason;
  if (review.branch) detail += ` Branch: ${review.branch}.`;
  $("review-detail").textContent = detail;
  const canPush = (state === "pending-review" || state === "push-failed") && !review.mock;
  $("agent-push").disabled = !canPush;
  // What approving DOES depends on the scope the job ran with.
  $("agent-push").textContent = review.pushable === false
    ? "Approve (keep on this machine)" : "Approve & push";
  $("agent-push").dataset.pushable = String(review.pushable !== false);
  if (review.scope) detail += ` Scope: ${review.scope}` +
                              (review.suite ? ` (${review.suite}).` : ".");
  $("review-detail").textContent = detail;
  if (state === "rejected")
    detail += review.needs_discard
      ? " Its changes were NOT undone — use Discard." : " Its changes were undone.";
  $("review-detail").textContent = detail;
  // Set by the tool only when the policy allows a repeat for this job.
  $("agent-discard").dataset.again =
    String(state === "discarded" && review.repeatable === true);
  $("agent-discard").textContent =
    state === "discarded" && review.repeatable === true ? "Discard again" : "Discard";
  $("agent-discard").disabled = !(review.needs_discard) &&
    !(state === "pending-review" || state === "verify-failed" ||
      state === "generating");   // a stopped run: discard puts it all back

  const f = review.files || {};
  const lines = [];
  for (const p of f.created || []) lines.push(`new       ${p}`);
  for (const p of f.modified || []) lines.push(`modified  ${p}`);
  $("agent-files").textContent = lines.join("\n") || "—";
  const steps = (review.verify || {}).steps || [];
  $("agent-verify").textContent = steps.length
    ? steps.map((s) => `${s.rc === 0 ? "ok  " : "FAIL"} ${s.name}` +
                       (s.rc === 0 ? "" : `\n${s.tail}`)).join("\n")
    : "—";
  if (f.created || f.modified) await showArtifact("proposed.diff", $("agent-diff"));
  else $("agent-diff").textContent = "—";
}

// The cursor log is its own file (cursor.log), appended to across runs.
// Read it by byte offset like any other log, on its own timer, so it
// keeps moving while a long Cursor call is in flight.
let cursorOffset = 0;
let cursorTimer = null;
async function pollCursorLog(reset) {
  if (reset) { cursorOffset = 0; $("cursor-log").textContent = ""; }
  try {
    const chunk = await api(
      `/api/log?job=${encodeURIComponent(job())}&runnable=cursor&offset=${cursorOffset}`);
    if (chunk.restarted) $("cursor-log").textContent = "";
    if (chunk.text) {
      cursorOffset = chunk.offset;
      $("cursor-log").textContent += chunk.text;
      $("cursor-log").scrollTop = $("cursor-log").scrollHeight;
    }
  } catch { /* no cursor log for this job yet */ }
}
function watchCursorLog() {
  clearInterval(cursorTimer);
  pollCursorLog(true);
  cursorTimer = setInterval(() => pollCursorLog(false), 1500);
}

function runAgent(runnable, options) {
  return post("/api/start", { job: job(), runnable, options })
    .then(() => follow(runnable, $("agent-log"), $("agent-state"), () => {
      refreshReview();
      pollCursorLog(false);
    }))
    .catch((e) => setState($("agent-state"), "bad", e.message));
}

const SCOPE_NOTE = {
  "new-test": "Tracked files. Approve commits them to branch agent/<job> and pushes it.",
  "converted": "Generated files are gitignored: approve keeps them on THIS machine " +
               "and stores the change, which is put back after every " +
               "convert of that suite. One suite only.",
  "converter": "Tracked files. The whole gate runs before and after; a check that " +
               "passed before must still pass. Approve pushes branch agent/<job>.",
};
function scopeChanged() {
  const s = $("agent-scope").value;
  $("agent-suite-field").hidden = s !== "converted";
  $("agent-scope-note").textContent = SCOPE_NOTE[s] || "";
}
$("agent-scope").onchange = scopeChanged;
scopeChanged();

$("agent-setup").onclick = () => runAgent("agent-setup", { "--job": job() });
$("agent-generate").onclick = () => {
  const scope = $("agent-scope").value;
  const suite = $("agent-suite").value.trim();
  if (scope === "converted" && !suite) {
    setState($("agent-state"), "bad", "name the suite Cursor may change");
    return;
  }
  const what = scope === "converted" ? `the converted files of suite "${suite}"`
             : scope === "converter" ? "the converter and its runtime"
             : "tests/jira/ and csv/manual/";
  if (!confirm(`Cursor will change ${what} for job "${job()}". ` +
               "Nothing is committed. Continue?")) return;
  const options = { "--job": job(), "--scope": scope };
  if (scope === "converted") options["--suite"] = suite;
  runAgent("agent-generate", options);
};
$("agent-stop").onclick = async () => {
  try { await post("/api/stop", { job: job(), runnable: "agent-generate" }); }
  catch (e) { setState($("agent-state"), "bad", e.message); }
};
$("agent-refresh").onclick = () => { refreshReview(); pollCursorLog(true); };
$("agent-patches").onclick = () => {
  const suite = $("agent-suite").value.trim();
  runAgent("agent-patches", suite ? { "--suite": suite } : {});
};
$("agent-reapply").onclick = () => {
  const suite = $("agent-suite").value.trim();
  if (!suite) { setState($("agent-state"), "bad", "name the suite"); return; }
  runAgent("agent-reapply", { "--suite": suite });
};
$("agent-discard").onclick = () => {
  if (!sameJobOrRefresh($("agent-discard"))) return;
  // A repeat (policy repeat_discard) is not the same question as the first.
  const again = $("agent-discard").dataset.again === "true";
  const msg = again
    ? "Discard AGAIN?\n\nThis puts the working tree back to how it was BEFORE " +
      "THE RUN. Anything you have changed by hand since then is reverted, " +
      "and files you have added are removed.\n\nOnly do this if the run is " +
      "still writing files. End its process first if you can."
    : "Remove the files the agent created and restore the ones it changed?";
  if (!confirm(msg)) return;
  runAgent("agent-discard", { "--job": job() });
};
function sameJobOrRefresh(btn) {
  if (btn.dataset.job === job()) return true;
  setState($("agent-state"), "bad",
           "the job id changed since this review was loaded — refreshed, check it again");
  refreshReview();
  return false;
}
$("agent-push").onclick = () => {
  if (!sameJobOrRefresh($("agent-push"))) return;
  const j = job();
  const pushes = $("agent-push").dataset.pushable !== "false";
  const msg = pushes
    ? "Approve and push?\n\nThis commits the reviewed files to branch agent/" +
      j + " and pushes that branch to origin. The repository is public. " +
      "main is not touched."
    : "Approve?\n\nThe converted files stay as Cursor left them, on this " +
      "machine only. Nothing is committed or pushed. The change is " +
      "stored and put back after every convert of the suite.";
  if (!confirm(msg)) return;
  runAgent("agent-approve", { "--job": j, "--confirm": j });
};
document.querySelector('.tabs button[data-tab="agent"]')
  .addEventListener("click", () => { refreshReview(); watchCursorLog(); });
$("job").addEventListener("change", () => { refreshReview(); pollCursorLog(true); });

// ---- building what is shown -------------------------------------------
// Everything from Jira or from a test run is somebody else's text. It is
// only ever put on the page as TEXT (textContent), never as markup.
function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "on") for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
    else n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const banner = (cls, text) => el("div", { class: `banner ${cls}`, text });
const MAX_TABLE_ROWS = 500;
function table(headers, rows) {
  const shown = rows.slice(0, MAX_TABLE_ROWS);
  const t = el("table", {},
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", { text: h })))),
    el("tbody", {}, shown.map((r) => el("tr", {}, r.map((c) =>
      c instanceof Node ? el("td", {}, c) : el("td", { text: c === null || c === undefined ? "" : String(c) }))))));
  const box = el("div", { class: "scroll" }, t);
  // Said BEFORE the list, like every other reason a list is not whole.
  return rows.length > shown.length
    ? el("div", {}, banner("warn",
        `Only the first ${shown.length} of ${rows.length} rows are shown here; ` +
        `all of them are in the result file.`), box)
    : box;
}
function heading(text, count) {
  return el("h4", {}, text, count === undefined ? null : el("span", { class: "count", text: `  (${count})` }));
}

// One command at a time per tab; the buttons say so by being off.
function busy(selector, on) {
  document.querySelectorAll(selector).forEach((b) => { b.disabled = on; });
}

// Run a declared command, follow its log, then show the result FILE it
// wrote for this job. The page reads the file; it works nothing out.
async function runTool(runnable, options, ui) {
  const j = job();
  busy(ui.buttons, true);
  // What is on screen is the LAST command's answer. It goes now, so that
  // a command which fails before it can answer does not appear to have
  // answered with it.
  ui.clear();
  try {
    await post("/api/start", { job: j, runnable, options });
    follow(runnable, ui.log, ui.state, async () => {
      await showResult(j, ui);
      busy(ui.buttons, false);
    });
  } catch (e) {
    busy(ui.buttons, false);
    setState(ui.state, "bad", e.message);
  }
}
async function showResult(j, ui) {
  let data = null;
  try {
    const r = await api(`/api/artifact?job=${encodeURIComponent(j)}&name=${ui.artifact}`);
    if (r.text) data = JSON.parse(r.text);
  } catch { /* no result yet, or not readable: show nothing rather than a guess */ }
  // The job box may have been edited while this was being fetched: what
  // came back belongs to `j`, and is only shown if `j` is still the job.
  if (j !== job()) return null;
  if (data) ui.render(data);
  else ui.clear();                 // this job has no result: not another job's
  return data;
}
// "At most" is a number or it is nothing.
function atMost(el, stateEl) {
  const v = el.value.trim();
  if (v && !/^[0-9]{1,5}$/.test(v)) {
    setState(stateEl, "bad", '"At most" is a whole number');
    return null;
  }
  return v;
}

// ---- tab 4: Jira --------------------------------------------------------
const jiraUi = { buttons: "[data-jira]", log: $("jira-log"), state: $("jira-state"),
                 artifact: "jira-result.json", render: renderJira,
                 clear: () => $("jira-result").replaceChildren() };

// null when "At most" is not usable; the caller then starts nothing.
function jiraOptions(extra) {
  const o = Object.assign({}, extra);
  const max = atMost($("jira-max"), $("jira-state"));
  if (max === null) return null;
  if (max) o["--max"] = max;
  return o;
}
function needProject() {
  const p = $("jira-project").value.trim();
  if (!p) setState($("jira-state"), "bad", "give the project key");
  return p;
}
$("jira-check").onclick = () => runTool("jira-verify", {}, jiraUi);
$("jira-paste").onclick = () => {
  const text = $("jira-paste-text").value.trim();
  if (!text) { setState($("jira-state"), "bad", "nothing was pasted"); return; }
  const o = jiraOptions({ "--text": text });
  if (o) runTool("jira-paste", o, jiraUi);
};
$("jira-versions").onclick = () => {
  const p = needProject();
  if (!p) return;
  const o = { "--project": p };
  if ($("jira-all").checked) o["--all"] = true;
  runTool("jira-versions", o, jiraUi);
};
$("jira-release").onclick = () => {
  const p = needProject();
  if (!p) return;
  const v = $("jira-version").value.trim();
  if (!v) { setState($("jira-state"), "bad", "give the version"); return; }
  const o = jiraOptions({ "--project": p, "--version": v });
  if (!o) return;
  if ($("jira-compare").value.trim()) o["--compare"] = $("jira-compare").value.trim();
  if ($("jira-type").value.trim()) o["--type"] = $("jira-type").value.trim();
  runTool("jira-release", o, jiraUi);
};
$("jira-tests").onclick = () => {
  const p = needProject();
  if (!p) return;
  const o = jiraOptions({ "--project": p });
  if (!o) return;
  if ($("jira-type").value.trim()) o["--type"] = $("jira-type").value.trim();
  runTool("jira-tests", o, jiraUi);
};

const issueRows = (issues) => (issues || []).map((i) =>
  [i.key, i.type, i.status, i.summary, (i.fix_versions || []).join(", "), (i.labels || []).join(", ")]);
const ISSUE_HEAD = ["Key", "Type", "Status", "Summary", "Fix versions", "Labels"];

// What a list says about itself: how much was read, and every reason it
// may not be the whole answer. Shown above the list, never below it.
function listFacts(r) {
  const out = [];
  const total = r.total === null || r.total === undefined ? "?" : r.total;
  out.push(el("div", { class: "meta" }, `${(r.issues || []).length} read of ${total}  ·  `,
              el("code", { text: r.jql || "" })));
  if (r.ordered_by_tool) out.push(el("div", { class: "meta",
    text: "ORDER BY key ASC was added so that paging cannot reshuffle the list." }));
  if (r.complete === false) out.push(banner("bad", `INCOMPLETE — ${r.why_incomplete || "the list is short"}`));
  if (r.limited) out.push(banner("warn",
    `LIMIT — only the first ${r.limit} were read. Raise "At most" or narrow the question.`));
  if (r.duplicates) out.push(banner("warn",
    `${r.duplicates} issue(s) came back twice and were kept once.`));
  if (r.page_size && r.page_size < 100) out.push(el("div", { class: "meta",
    text: `This Jira returns at most ${r.page_size} issues a page.` }));
  return out;
}

function renderJira(r) {
  const box = $("jira-result");
  box.replaceChildren();
  if (!r || !r.kind) return;
  if (r.mock) box.append(banner("warn", "MOCK — sample data from this machine, not from Jira."));
  if (r.kind === "error") {
    box.append(banner("bad", (r.refused ? "Refused — " : "Failed — ") + (r.message || "")));
    return;
  }
  if (r.kind === "verify") {
    box.append(banner(r.ok ? "ok" : "bad", (r.ok ? "Token accepted — " : "Token NOT accepted — ") + (r.message || "")));
    return;
  }
  if (r.kind === "versions") {
    box.append(heading(`Versions of ${r.project}`, (r.versions || []).length),
      el("div", { class: "meta", text: "Newest first, by the number in the name (release dates are often not set)." }),
      table(["Version", "State", "Release date"], (r.versions || []).map((v) => [
        el("button", { class: "linkish", text: v.name, title: "Use as the version",
                       on: { click: () => { $("jira-version").value = v.name; } } }),
        (v.released ? "released" : "unreleased") + (v.archived ? ", archived" : ""),
        v.release_date || ""])));
  } else if (r.kind === "issues") {
    box.append(heading(r.title || "Issues", (r.issues || []).length), ...listFacts(r),
               table(ISSUE_HEAD, issueRows(r.issues)));
  } else if (r.kind === "release") {
    const res = r.result || {};
    box.append(heading(`${r.project} ${r.version}`, (res.issues || []).length), ...listFacts(res));
    if (r.delta) {
      const old = r.old || {};
      box.append(heading(`${r.project} ${r.compared_with}`, (old.issues || []).length), ...listFacts(old));
      if (r.delta.reliable === false) box.append(banner("bad",
        "NOT RELIABLE — one of the two lists is incomplete or was cut at the limit, so an issue " +
        "shown on one side only may just not have been read on the other."));
      for (const [title, key] of [[`Only in ${r.version}`, "added"],
                                  [`Only in ${r.compared_with}`, "removed"],
                                  ["In both", "continuing"]]) {
        box.append(heading(title, (r.delta[key] || []).length), table(ISSUE_HEAD, issueRows(r.delta[key])));
      }
    } else {
      box.append(table(ISSUE_HEAD, issueRows(res.issues)));
    }
  }
  if (r.file) box.append(el("div", { class: "meta" }, "Also written to ", el("code", { text: r.file })));
}
onTab.jira = () => showResult(job(), jiraUi);

// ---- the Defects tab ------------------------------------------------------
const defectUi = { buttons: "[data-defects]", log: $("defects-log"), state: $("defects-state"),
                   artifact: "defects-result.json", render: renderDefects,
                   clear: () => { $("defects-result").replaceChildren(); defectsShown = null; } };
let defectsShown = null;          // the last result rendered, for the Suggest button's count

$("defects-load").onclick = () => {
  const o = {};
  const text = $("defects-text").value.trim();
  const project = $("defects-project").value.trim();
  if (text) o["--text"] = text;
  else if (project) {
    o["--project"] = project;
    if ($("defects-version").value.trim()) o["--version"] = $("defects-version").value.trim();
  } else { setState($("defects-state"), "bad", "paste keys or a query, or give a project"); return; }
  const max = atMost($("defects-max"), $("defects-state"));
  if (max === null) return;
  if (max) o["--max"] = max;
  runTool("defects-load", o, defectUi);
};
$("defects-suggest").onclick = () => {
  const n = defectsShown && defectsShown.kind === "defects" ? (defectsShown.defects || []).length : 0;
  if (!n) { setState($("defects-state"), "bad", "load some defects first"); return; }
  if (!confirm(`Send the text of ${n} defect(s) to Cursor for a suggested reason?\n\n` +
               "Summaries, descriptions and recent comments are sent, with host names and the " +
               "private configuration's values removed. Nothing is written to Jira.")) return;
  runTool("defects-suggest", {}, defectUi);
};

function flagClass(flag) {
  if (/^DIFFERS/.test(flag)) return "flag differs";
  if (/^agrees/.test(flag)) return "flag agrees";
  if (/NOT REVIEWED|not one of the allowed/.test(flag)) return "flag unreviewed";
  return "flag";
}

function defectRow(d, r) {
  const suggested = el("div", {},
    d.suggested ? el("div", { text: d.suggested + (d.confidence ? `  (${d.confidence})` : "") +
                                    (d.suggested_by === "mock" ? "  — MOCK" : "") }) : null,
    d.said ? el("small", { text: `it answered: ${d.said}` }) : null,
    d.flag ? el("span", { class: flagClass(d.flag), text: d.flag }) : null);
  const why = el("div", {}, d.why || "", d.evidence ? el("small", { text: `"${d.evidence}"` }) : null);
  const similar = el("div", {}, (d.precedents || []).map((p) => el("div", {},
    el("code", { text: p.key }), `: ${p.reason}`,
    el("small", { text: `${Math.round((p.score || 0) * 100)}% — ${(p.shared || []).join("; ")}` }))));
  let write;
  if (!r.write_back) {
    write = el("small", { text: "writing is off" });
  } else {
    // The box starts on the suggestion, or on what the bug has if that is
    // an allowed reason -- and otherwise on nothing. Not on the first
    // reason of the list: a careless click would write a value nobody chose.
    const reasons = r.reasons || [];
    // ...and not on a stand-in's keyword match when the Jira is real.
    const offered = d.suggested_by === "mock" && !r.mock_jira ? "" : d.suggested;
    const start = reasons.includes(offered) ? offered
                : reasons.includes(d.current) ? d.current : "";
    const pick = el("select", {},
      el("option", { value: "", text: "choose…" }),
      reasons.map((x) => el("option", { value: x, text: x })));
    pick.value = start;
    const oneLine = (s) => String(s || "").replace(/\s+/g, " ").trim();
    write = el("div", { class: "writecell" }, pick,
      el("button", { "data-defects": "", text: "Write to Jira", on: { click: () => {
        const reason = pick.value;
        if (!reason) { setState($("defects-state"), "bad", `choose a reason for ${d.key}`); return; }
        if (!confirm(`Change Jira?\n\n${d.key}: ${oneLine(r.field.name)}\n` +
                     `from: ${oneLine(d.current) || "(empty)"}\nto:   ${oneLine(reason)}\n\n` +
                     (r.mock_jira
                       ? "MOCK: this changes a file on this machine. No Jira is contacted."
                       : "This writes to Jira now. It is one field of one bug."))) return;
        runTool("defects-apply", { "--key": d.key, "--reason": reason, "--confirm-key": d.key }, defectUi);
      } } }),
      d.applied ? el("small", { text: d.applied.ok === true
        ? `written ${d.applied.at}; was ${d.applied.was || "(empty)"}`
        : d.applied.ok === null
          ? `${d.applied.at}: SENT, NOT CONFIRMED — ${d.applied.note || "check this bug in Jira"}`
          : `write of ${d.applied.at} did not take: Jira holds ${d.applied.holds || "(empty)"}` }) : null);
  }
  return [el("code", { text: d.key }), el("div", {}, d.summary, el("small", { text: d.status })),
          d.current || "", suggested, why, similar, write];
}

function renderDefects(r) {
  const box = $("defects-result");
  box.replaceChildren();
  defectsShown = r;
  if (!r || !r.kind) return;
  if (r.kind === "error") {
    box.append(banner("bad", (r.refused === false ? "Failed — " : "Refused — ") + (r.message || "")));
    return;
  }
  if (r.kind !== "defects") return;
  if (r.mock_jira || r.mock_cursor) box.append(banner("warn",
    "MOCK — " + [r.mock_jira ? "the defects are sample data, and a write changes only a file on this machine" : "",
                 r.mock_cursor ? "the suggested reasons are a keyword match, not Cursor" : ""]
      .filter(Boolean).join("; ") + "."));
  if (r.message) box.append(banner(r.ok === false ? "bad" : "ok", r.message));
  box.append(
    el("div", { class: "meta", text:
      `Field: ${r.field.name}  ·  ${(r.reasons || []).length} reason(s) from ${r.reasons_from}` +
      `  ·  ${r.earlier_read || 0} earlier defect(s) with a reason read for comparison` }),
    banner(r.write_back ? "warn" : "ok", r.write_back
      ? "Writing to Jira is ON for this machine: each Write to Jira button changes one bug, after asking."
      : "Writing to Jira is OFF. To allow it, set \"write_back\": true in jira_config.defects."));
  box.append(...listFacts(Object.assign({ issues: r.defects }, r.source)));
  for (const w of r.warnings || []) box.append(banner("bad", w));
  for (const f of r.suggest_failures || []) box.append(banner("bad", `No answer for ${f}`));
  box.append(heading("Defects", (r.defects || []).length),
    table(["Key", "Summary", "Current reason", "Suggested", "Why", "Similar earlier defects",
           "Jira"], (r.defects || []).map((d) => defectRow(d, r))));
}
onTab.defects = () => showResult(job(), defectUi);

// ---- tab 5: Failures -----------------------------------------------------
// Saving a note does not record a run: the command answers with the
// comparison read again, the note in it. (It used to be followed by a
// `record`, which wiped a failed note's error from the page, and after a
// new test run quietly recorded that run.)
const failUi = { buttons: "[data-fail]", log: $("fail-log"), state: $("fail-state"),
                 artifact: "failures-result.json", render: renderFailures,
                 clear: () => { $("fail-detail").replaceChildren(); } };

$("fail-record").onclick = () => {
  const o = {};
  if ($("fail-label").value.trim()) o["--label"] = $("fail-label").value.trim();
  runTool("failures-record", o, failUi);
};
$("fail-list").onclick = () => runTool("failures-list", {}, failUi);

const where = (t) => (t.row && t.row !== "-" ? `${t.test} [${t.row}]` : t.test);

function signatureCard(s) {
  const input = el("input", { type: "text", spellcheck: "false",
                              placeholder: "what this turned out to be",
                              value: s.note ? s.note.text : "" });
  const card = el("div", { class: "card" },
    el("div", {}, el("code", { text: `[${s.id}]` }), `  ${s.count} failure(s)`),
    el("div", { class: "sig", text: s.signature }),
    el("div", { class: "meta", text: s.runs
      ? `Seen in ${s.runs} earlier run(s): first ${s.first}, last ${s.last}.`
      : "Not seen in any earlier run." }));
  if (s.resembles) {
    const r = s.resembles;
    card.append(el("div", { class: "resembles" },
      el("div", {}, `RESEMBLES `, el("code", { text: `[${r.id}]` }),
         ` — ${Math.round((r.score || 0) * 100)}%: ${(r.shared || []).join("; ")}. A pointer, not a diagnosis.`),
      el("div", { class: "sig", text: r.signature }),
      r.note ? el("div", { class: "meta", text: `Its note: ${r.note}` }) : null));
  }
  if (s.note) card.append(el("div", { class: "meta", text: `Note (${s.note.at}): ${s.note.text}` }));
  card.append(el("div", { class: "noterow" }, input,
    el("button", { "data-fail": "", text: "Save note", on: { click: () => {
      const text = input.value.trim();
      if (!text) { setState($("fail-state"), "bad", "the note is empty"); return; }
      runTool("failures-note", { "--id": s.id, "--text": text }, failUi);
    } } }),
    el("button", { "data-fail": "", text: "Every run it was in", on: { click: () =>
      runTool("failures-show", { "--id": s.id }, failUi) } })));
  return card;
}

function renderFailures(r) {
  if (!r || !r.kind) return;
  // The comparison stays on screen while a list of runs or one
  // signature's history is looked at below it.
  const main = $("fail-result");
  const detail = $("fail-detail");
  if (r.kind === "error") {
    detail.replaceChildren(banner("bad",
      (r.refused === false ? "Failed — " : "Refused — ") + (r.message || "")));
    return;
  }
  if (r.kind === "noted") {
    detail.replaceChildren(banner("ok", `Noted for [${r.id}].`));
    return;
  }
  if (r.kind === "runs") {
    detail.replaceChildren(heading("Recorded runs", (r.runs || []).length),
      table(["When", "Suite", "Failing", "Signatures", "Tests run", "About"],
            (r.runs || []).map((x) => [x.at, x.suite, x.failing, x.distinct,
                                       x.tests_run === null || x.tests_run === undefined ? "" : x.tests_run,
                                       x.label || ""])));
    return;
  }
  if (r.kind === "show") {
    const kids = [heading(`Signature [${r.id}]`, (r.runs || []).length)];
    if (!(r.runs || []).length) kids.push(banner("warn", "No recorded failure has this signature."));
    if (r.signature) kids.push(el("div", { class: "sig", text: r.signature }));
    if (r.note) kids.push(el("div", { class: "meta", text: `Note (${r.note.at}): ${r.note.text}` }));
    kids.push(table(["When", "Suite", "About", "Failing tests"], (r.runs || []).map((x) =>
      [x.at, x.suite, x.label || "", (x.tests || []).map(where).join(", ")])));
    detail.replaceChildren(...kids);
    return;
  }
  if (r.kind !== "record") return;
  detail.replaceChildren(r.noted ? banner("ok", `Noted for [${r.noted}].`) : "");
  const kids = [heading(`Suite ${r.suite}`),
    el("div", { class: "meta", text:
      `${r.failing} failing (test, row) pair(s), ${r.distinct} distinct signature(s)` +
      (r.tests_run ? `, ${r.tests_run} test(s) run` : "") + `  ·  run of ${r.at}` +
      (r.label ? `  ·  ${r.label}` : "") })];
  if (r.fresh === false) kids.push(banner("warn",
    "This digest was already recorded; nothing new was kept. Run the tests again for a new one."));
  for (const w of r.warnings || []) kids.push(banner("bad", w));
  if (!r.previous) {
    kids.push(banner("ok", "This is the first recorded run of this suite: nothing to compare with yet."));
  } else {
    kids.push(el("div", { class: "meta", text:
      `Compared with the run of ${r.previous.at}` + (r.previous.label ? ` (${r.previous.label})` : "") }));
    for (const n of r.notes || []) kids.push(banner("warn", n));
    const g = r.groups || {};
    kids.push(heading("New — not failing before", (g.new || []).length));
    if ((g.new || []).length) kids.push(table(["Test", "Now"], g.new.map((x) => [where(x), x.now])));
    kids.push(heading("Failing differently — same test, another signature", (g.changed || []).length));
    if ((g.changed || []).length) kids.push(table(["Test", "Was", "Now"],
      g.changed.map((x) => [where(x), x.was, x.now])));
    kids.push(heading("Still failing the same way", (g.same || []).length));
    kids.push(heading("No longer failing — passed, or did not run", (g.gone || []).length));
    if ((g.gone || []).length) kids.push(table(["Test"], g.gone.map((x) => [where(x)])));
  }
  kids.push(heading("Signatures in this run", (r.signatures || []).length));
  if (!(r.signatures || []).length) kids.push(banner("ok", "Nothing failed."));
  for (const s of r.signatures || []) kids.push(signatureCard(s));
  main.replaceChildren(...kids);
}
// Opening the tab, or changing the job, shows THAT job's last result --
// and nothing, comparison included, when it has none.
onTab.failures = async () => {
  const j = job();
  const data = await showResult(j, failUi);
  if (j === job() && !(data && data.kind === "record")) $("fail-result").replaceChildren();
};

showMockBanner();

// Whatever tab the address names, open it -- after every tab above has
// said what it does when opened.
(function openTabFromAddress() {
  const name = (location.hash || "").replace(/^#/, "");
  const b = name && document.querySelector(`.tabs button[data-tab="${name.replace(/[^a-z]/g, "")}"]`);
  if (b) b.click();
})();
$("job").addEventListener("change", () => {
  const open = document.querySelector('.tabs button[aria-selected="true"]');
  if (open && onTab[open.dataset.tab]) onTab[open.dataset.tab]();
});
