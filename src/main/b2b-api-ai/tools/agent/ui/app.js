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
