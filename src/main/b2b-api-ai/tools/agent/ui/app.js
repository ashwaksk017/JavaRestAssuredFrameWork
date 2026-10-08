"use strict";
// The page holds no state. Everything it shows comes from the job
// directory, so a refresh loses nothing and every run is reproducible
// from the CLI with the same arguments.

const $ = (id) => document.getElementById(id);
const job = () => $("job").value.trim();

async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({ error: "not JSON" }));
  if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
  return body;
}
const post = (path, payload) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload) });

// ---- tabs ------------------------------------------------------------
document.querySelectorAll(".tabs button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((x) =>
      x.setAttribute("aria-selected", String(x === b)));
    document.querySelectorAll(".tab").forEach((s) =>
      s.hidden = s.id !== `tab-${b.dataset.tab}`);
  };
});

// ---- following a run -------------------------------------------------
// One poller per (job, runnable). It appends only what is new, so a long
// convert does not re-render its whole log every second.
function follow(runnable, logEl, stateEl, onDone) {
  let offset = 0;
  logEl.textContent = "";
  setState(stateEl, "run", "running…");
  const tick = async () => {
    try {
      const j = job();
      const chunk = await api(
        `/api/log?job=${encodeURIComponent(j)}&runnable=${runnable}&offset=${offset}`);
      if (chunk.text) {
        offset = chunk.offset;
        logEl.textContent += chunk.text;
        logEl.scrollTop = logEl.scrollHeight;
      }
      const st = await api(
        `/api/status?job=${encodeURIComponent(j)}&runnable=${runnable}`);
      if (st.state === "running") { setTimeout(tick, 900); return; }
      if (st.state === "done") setState(stateEl, "ok", "finished");
      else setState(stateEl, "bad", `exit ${st.exit_code}`);
      if (onDone) onDone(st);
    } catch (e) {
      setState(stateEl, "bad", e.message);
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
  "rejected": "Rejected — the agent broke a rule and its changes were undone.",
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
  try {
    const r = await api(
      `/api/artifact?job=${encodeURIComponent(job())}&name=review.json`);
    if (r.text) review = JSON.parse(r.text);
  } catch { /* no review yet */ }
  const state = review.state || "none";
  $("review-box").hidden = false;
  const badge = $("review-badge");
  badge.textContent = state.replace("-", " ");
  badge.className = `badge ${state}`;
  let detail = REVIEW_TEXT[state] || state;
  if (review.reason) detail += " " + review.reason;
  if (review.branch) detail += ` Branch: ${review.branch}.`;
  $("review-detail").textContent = detail;
  const canPush = state === "pending-review" || state === "push-failed";
  $("agent-push").disabled = !canPush;
  // What approving DOES depends on the scope the job ran with.
  $("agent-push").textContent = review.pushable === false
    ? "Approve (keep on this machine)" : "Approve & push";
  $("agent-push").dataset.pushable = String(review.pushable !== false);
  if (review.scope) detail += ` Scope: ${review.scope}` +
                              (review.suite ? ` (${review.suite}).` : ".");
  $("review-detail").textContent = detail;
  $("agent-discard").disabled =
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
  if (!confirm("Remove the files the agent created and restore the ones it " +
               "changed?")) return;
  runAgent("agent-discard", { "--job": job() });
};
$("agent-push").onclick = () => {
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
