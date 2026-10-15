"use strict";
// The page holds no state. Everything it shows comes from the job
// directory, so a refresh loses nothing and every run is reproducible
// from the CLI with the same arguments.

const $ = (id) => document.getElementById(id);
const job = () => $("job").value.trim();
// `?job=name` in the address opens the page on that job, so a result can
// be linked to: http://127.0.0.1:8787/?job=release-6#jira
// Without one -- the workbench just started -- the page opens on a NEW job,
// empty: nothing of an earlier job's design, review or Xray tests shows.
// The job in use is written into the address, so a refresh stays on it.
// Earlier jobs are kept on disk and offered in the job box's list.
function newJobId() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `job-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
function jobIntoAddress() {
  const j = job();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(j)) return;
  const q = new URLSearchParams(location.search);
  if (q.get("job") === j) return;
  q.set("job", j);
  try { history.replaceState(null, "", `${location.pathname}?${q.toString()}${location.hash}`); } catch { /* file: or sandboxed */ }
}
(function jobFromAddress() {
  const j = new URLSearchParams(location.search).get("job") || "";
  $("job").value = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(j) ? j : newJobId();
  jobIntoAddress();
  $("job").addEventListener("change", jobIntoAddress);
})();

// ---- saying what the page is doing --------------------------------------
// Nothing the page waits for is silent. A request that takes longer than a
// blink is named in the strip at the top ("Storing the file..."), and the
// button that sent it cannot be pressed again until it has answered.
// The polls that follow a running command are not named here: the command
// says what IT is doing, beside its own button (see follow()).
const QUIET = ["/api/log", "/api/status", "/api/mock", "/api/client-error", "/api/whoami", "/api/jobs"];
const DOING = {
  "/api/intake": "Saving the story and its links",
  "/api/design": "Starting the design",
  "/api/manual": "Starting",
  "/api/start": "Starting the command",
  "/api/stop": "Stopping it",
  "/api/upload": "Storing the file",
  "/api/upload-clear": "Clearing the earlier choice",
  "/api/doc-upload": "Storing the document",
  "/api/doc-remove": "Removing the document",
  "/api/spec-upload": "Storing the specification file",
  "/api/spec-clear": "Removing the stored specification",
  "/api/artifact": "Loading the result",
  "/api/jobs": "Listing the jobs",
  "/api/uploads": "Looking at the files chosen for the convert",
  "/api/docs": "Looking at the documents kept for this job",
  "/api/spec": "Looking at the specification kept for this job",
  "/api/diag": "Reading the logs",
  "/api/runnables": "Asking what can be run",
};
const inFlight = new Map();
let flight = 0;
function showActivity() {
  const bar = document.getElementById("activity");
  if (!bar) return;
  const texts = [...new Set(inFlight.values())];
  bar.textContent = texts.length ? `${texts.join(" \u00b7 ")}\u2026` : "";
  bar.hidden = !texts.length;
}
// The button a click landed on, for as long as that click is being handled.
let clicked = null;
document.addEventListener("click", (e) => {
  const b = e.target && e.target.closest ? e.target.closest("button") : null;
  if (!b || b.closest(".tabs")) return;
  if (b.classList.contains("waiting")) {
    // Its last press has not been answered yet.
    e.stopImmediatePropagation();
    e.preventDefault();
    return;
  }
  clicked = b;
  setTimeout(() => { if (clicked === b) clicked = null; }, 0);
}, true);
async function watchedFetch(path, opts) {
  const p = String(path).split("?")[0];
  if (QUIET.includes(p)) return fetch(path, opts);
  const id = ++flight;
  const timer = setTimeout(() => {
    inFlight.set(id, DOING[p] || "Asking the workbench");
    showActivity();
  }, 350);
  // Held by a mark, not by `disabled`: what the page's own code does to
  // `disabled` in the meantime stays exactly as that code left it.
  const held = opts && opts.method === "POST" && clicked ? clicked : null;
  if (held) held.classList.add("waiting");
  try {
    return await fetch(path, opts);
  } finally {
    clearTimeout(timer);
    inFlight.delete(id);
    showActivity();
    if (held) held.classList.remove("waiting");
  }
}

async function api(path, opts) {
  const r = await watchedFetch(path, opts);
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
// What each command is FOR, in the server's words, shown the moment it is
// started -- before it has said anything itself.
const LABELS = {};
api("/api/runnables").then((r) => {
  for (const [name, v] of Object.entries(r || {})) LABELS[name] = v.label || "";
}).catch(() => { /* the label is then "Working" */ });

// The list of steps a command has said, kept just above its log pane.
function stepsAbove(logEl) {
  let s = logEl.previousElementSibling;
  if (!s || !s.classList || !s.classList.contains("steps")) {
    s = document.createElement("ol");
    s.className = "steps";
    s.setAttribute("aria-live", "polite");
    logEl.before(s);
  }
  return s;
}
const STEP_MARK = "==> ";

// One poller per (job, runnable). It appends only what is new, so a long
// convert does not re-render its whole log every second.
function follow(runnable, logEl, stateEl, onDone, forJob) {
  // The job is read ONCE. Re-reading the text box every tick would switch
  // this poller to another job's log if the box is edited mid-run. A
  // caller that already knows which job it started passes it.
  const j = forJob || job();
  let offset = 0;
  let misses = 0;
  logEl.textContent = "";
  // The pane and the label belong to the command started LAST. A command
  // still running from before (a review that takes minutes, then Approve)
  // keeps being followed to its end, but no longer writes its log into
  // another command's, or its exit code over another command's.
  const mine = {};
  logEl.followedBy = mine;
  const owns = () => logEl.followedBy === mine;
  // The label has one writer as well: two commands followed into different
  // panes but one label took turns writing over each other.
  stateEl.followedBy = mine;
  const ownsLabel = () => stateEl.followedBy === mine;
  // What it is doing, in words, and for how long. A command says its
  // steps as lines that start with a mark (tools/diag.py, `step`); until
  // it has said one, what is shown is what the command is for.
  // Counted from when the COMMAND started, once the server has said when:
  // a page opened on a run already going shows the run's time, not its own.
  let began = Date.now();
  let ended = 0;
  const timed = (st) => {
    const a = Date.parse(st && st.started || "");
    const z = Date.parse(st && st.finished || "");
    if (!Number.isNaN(a)) began = Math.min(began, a);
    if (!Number.isNaN(z)) ended = z;
  };
  const clock = () => {
    const s = Math.max(0, Math.floor(((ended || Date.now()) - began) / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };
  let doing = "";                   // the last step said; until one is, what the command is for
  const what = () => doing || LABELS[runnable] || "Working";
  let unfinished = "";              // a line the last chunk stopped in the middle of
  const stepsEl = stepsAbove(logEl);
  stepsEl.replaceChildren();
  const paint = () => { if (ownsLabel()) setState(stateEl, "run", `${what()}… ${clock()}`); };
  const read = async () => {
    const chunk = await api(
      `/api/log?job=${encodeURIComponent(j)}&runnable=${runnable}&offset=${offset}`);
    if (!chunk.text) return;
    offset = chunk.offset;
    if (owns()) {
      logEl.textContent += chunk.text;
      logEl.scrollTop = logEl.scrollHeight;
    }
    const lines = (unfinished + chunk.text).split("\n");
    unfinished = lines.pop();
    for (const line of lines) {
      if (line.startsWith(STEP_MARK)) say(line.slice(STEP_MARK.length).trim().slice(0, 200));
    }
  };
  const say = (text) => {
    if (!text || text === doing) return;
    doing = text;
    if (!owns()) return;
    for (const li of stepsEl.children) li.className = "done";
    const li = document.createElement("li");
    li.className = "now";
    li.textContent = text;          // text, whatever it holds
    stepsEl.append(li);
  };
  const settle = (ok) => {
    if (!owns()) return;
    const last = stepsEl.lastElementChild;
    for (const li of stepsEl.children) li.className = "done";
    if (!ok && last) last.className = "stopped";
  };
  paint();
  const tick = async () => {
    try {
      await read();
      const st = await api(
        `/api/status?job=${encodeURIComponent(j)}&runnable=${runnable}`);
      misses = 0;
      timed(st);
      if (st.state === "running") { paint(); setTimeout(tick, 900); return; }
      // What it wrote between the last read and its end: without this the
      // step a failure "stopped at" could be the one before the real one.
      await read();
      settle(st.state === "done" || st.state === "none");
      if (ownsLabel()) {
        if (st.state === "done") setState(stateEl, "ok", `finished in ${clock()}`);
        else if (st.state === "none") setState(stateEl, "", "not run yet for this job");
        else {
          // The reason, when the command said one: a label that only names
          // the step it stopped at hides the FAIL line two panes down.
          const said = (logEl.textContent.split("\n").filter((l) => /^(FAIL|REFUSED)\b/.test(l)).pop() || "").trim();
          setState(stateEl, "bad", `exit ${st.exit_code} after ${clock()} — ` +
            (said ? said.slice(0, 300) : `stopped at: ${what()}`));
        }
      }
      if (onDone) onDone(st);
    } catch (e) {
      // One failed request is not the end of the run. Giving up at once
      // re-enabled buttons while the job was still going.
      misses += 1;
      if (misses <= 8) {
        if (ownsLabel()) setState(stateEl, "run", `${what()}… (the workbench is not answering just now, retry ${misses})`);
        setTimeout(tick, 1500);
        return;
      }
      if (owns()) setState(stateEl, "bad", `${e.message} — the run may still be going; refresh to see`);
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
// The stories named in the box: each as written (a link or a key) with
// the key it is about. Several make one job per story.
const KEY_RX = /\b([A-Za-z][A-Za-z0-9_]+-[0-9]+)\b/;
function storiesNamed(text) {
  const out = [];
  const seen = new Set();
  for (const piece of text.split(/[\n,;]+|\s{2,}/).map((s) => s.trim()).filter(Boolean)) {
    // A key alone, or an address with the key in it. A URL with spaces is
    // not a thing, so splitting on runs of spaces is safe; a single space
    // between two keys is tried below.
    const parts = /^https?:\/\//i.test(piece) ? [piece] : piece.split(/\s+/);
    for (const p of parts) {
      // In an address, the key is the one after /browse/ (or in the query),
      // not a word of the host such as `acme-1.atlassian.net`.
      const m = p.match(/\/browse\/([A-Za-z][A-Za-z0-9_]+-[0-9]+)/) ||
                p.match(/[?&]selectedIssue=([A-Za-z][A-Za-z0-9_]+-[0-9]+)/) ||
                (/^https?:\/\//i.test(p) ? null : p.match(KEY_RX));
      if (!m) continue;
      const key = m[1].toUpperCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ given: p, key });
    }
  }
  return out;
}
const MAX_STORIES = 10;

// Where an input comes from: a radio group shows one box and hides the
// others. Only the shown box is read -- a hidden one keeps its text for
// when it is chosen again, but is never sent.
function chosen(name) {
  const r = document.querySelector(`input[name="${name}"]:checked`);
  return r ? r.value : "";
}
function showChosen(name) {
  // "Use the story's Xray tests" reads the story by its link, as "A link or
  // a key" does: it shows that box, with a note on what Design then does.
  const pick = chosen(name);
  const show = name === "story-from" && pick === "xray" ? "link" : pick;
  for (const r of document.querySelectorAll(`input[name="${name}"]`)) {
    const box = $(`${name}-${r.value}`);
    if (box) box.hidden = r.value !== show;
  }
  if (name === "story-from") {
    const xray = pick === "xray";
    if ($("story-xray-note")) $("story-xray-note").hidden = !xray;
    if ($("run-design")) {
      $("run-design").textContent = xray ? "Design from the Xray tests" : "Design the tests";
      $("run-design").title = xray ? "the Xray tests linked to the story, with their steps, become the cases; Cursor is not asked" : "";
    }
  }
}
for (const name of ["story-from", "pages-from", "spec-from", "req-from"]) {
  for (const r of document.querySelectorAll(`input[name="${name}"]`)) r.onchange = () => showChosen(name);
  showChosen(name);
}
// What Read it is given: the story by link or pasted, the pages by link or
// pasted -- whichever each choice shows.
function storyInputs() {
  const byLink = chosen("story-from") !== "paste";
  const pagesByLink = chosen("pages-from") !== "paste";
  return {
    story: byLink ? $("story").value.trim() : "",
    text: byLink ? "" : $("text").value,
    links: pagesByLink ? $("links").value.split("\n").map((s) => s.trim()).filter(Boolean) : [],
    pages: pagesByLink ? "" : $("pages").value,
  };
}

$("run-intake").onclick = async () => {
  try {
    // The story goes first: it is the story of the job, and one a job.
    const given = storyInputs();
    const story = given.story;
    const links = given.links;
    const stories = storiesNamed(story);
    if (!story && !links.length && !given.text.trim() && !given.pages.trim()) {
      setState($("new-state"), "bad", chosen("story-from") === "paste"
        ? "paste the story, or give a Confluence link"
        : "give a Jira story's link or key, or a Confluence link");
      return;
    }
    if (stories.length > 1) { await readStories(stories, links); return; }
    $("stories").replaceChildren();
    // The Jira token is checked by Read it itself, before the story is
    // asked for: an expired token says so instead of "no such story".
    await post("/api/intake", {
      job: job(),
      text: given.text,
      pages: given.pages,
      links: (story ? [story] : []).concat(links),
    });
    follow("intake", $("new-log"), $("new-state"),
           () => showArtifact("brief.md", $("brief")));
  } catch (e) { setState($("new-state"), "bad", e.message); }
};

// Several stories: one job each, named by its key, all read at once. The
// Confluence links given go with every one; what was pasted goes with
// none -- a paste is about one story, and which one cannot be told.
async function readStories(stories, links) {
  const box = $("stories");
  if (stories.length > MAX_STORIES) {
    setState($("new-state"), "bad", `${stories.length} stories; up to ${MAX_STORIES} at a time`);
    return;
  }
  if (storyInputs().pages.trim()) {
    setState($("new-state"), "warn", "several stories: the pasted page text was not " +
      "used (it belongs to one story); read a story on its own to use it");
  } else {
    setState($("new-state"), "run", `reading ${stories.length} stories, one job each…`);
  }
  const rows = new Map();
  const draw = () => {
    box.replaceChildren(heading("Stories read, one job each", stories.length),
      table(["Job", "State", "Story", "Requests", "Criteria", ""], stories.map((s) => {
        const r = rows.get(s.key) || {};
        const open = el("button", { type: "button", on: { click: () => {
          $("job").value = s.key;
          $("job").dispatchEvent(new Event("change"));
          showArtifact("brief.md", $("brief"));
          setState($("new-state"), "", `job ${s.key} is in the box`);
        } } }, "Open");
        return [el("code", { text: s.key }),
                el("span", { class: `state ${r.cls || "run"}`, text: r.state || "starting…" }),
                el("span", { text: r.summary || "" }),
                r.requests === undefined ? "" : String(r.requests),
                r.ac === undefined ? "" : (r.ac ? "found" : "NONE FOUND"),
                open];
      })));
  };
  draw();
  await Promise.all(stories.map(async (s) => {
    try {
      await post("/api/intake", { job: s.key, text: "", pages: "", links: [s.given].concat(links) });
    } catch (e) {
      rows.set(s.key, { state: `not started: ${e.message}`, cls: "bad" });
      draw();
      return;
    }
    rows.set(s.key, { state: "reading…", cls: "run" });
    draw();
    for (let n = 0; n < 600; n += 1) {
      let st = null;
      try { st = await api(`/api/status?job=${encodeURIComponent(s.key)}&runnable=intake`); } catch { st = null; }
      if (st && st.state !== "running") break;
      await new Promise((ok) => setTimeout(ok, 1000));
    }
    const got = await artifactJson(s.key, "intake.json");
    if (!got) { rows.set(s.key, { state: "no result; see its Logs", cls: "bad" }); draw(); return; }
    const read = got.story && got.story.key;
    rows.set(s.key, {
      state: read ? (got.ok ? "read" : "read, no request in it") : "story not read",
      cls: read ? (got.ok ? "ok" : "warn") : "bad",
      summary: read ? got.story.summary : ((got.notes || [])[0] || ""),
      requests: (got.requests || []).length,
      ac: read ? Boolean((got.story.acceptance_criteria || {}).present) : undefined,
    });
    draw();
  }));
  const done = [...rows.values()];
  setState($("new-state"), done.every((r) => r.cls === "ok" || r.cls === "warn") ? "ok" : "warn",
    `${done.filter((r) => r.cls === "ok").length} of ${stories.length} stories read with requests; ` +
    "press Open on one to go on with it");
}

// Has the story changed since the job read it? Reads Jira again and shows
// the difference, and what in the job was made from the earlier reading.
$("story-check").onclick = async () => {
  const j = job();
  if (!j) { setState($("story-check-state"), "bad", "name the job first"); return; }
  $("story-check").disabled = true;
  try {
    await post("/api/start", { job: j, runnable: "story-check", options: { "--job": j } });
    follow("story-check", document.createElement("pre"), $("story-check-state"), async () => {
      $("story-check").disabled = false;
      const r = await artifactJson(j, "story-check.json");
      const box = $("story-check-result");
      if (!r) { box.replaceChildren(banner("bad", "no answer; the Logs tab has the reason")); return; }
      const kids = [banner(!r.ok ? "bad" : r.changed ? "warn" : "ok", r.message || "")];
      if (r.changed) {
        if (r.at_stake && r.at_stake.length) {
          kids.push(heading("Made from the earlier reading", r.at_stake.length));
          kids.push(el("ul", {}, r.at_stake.map((s) => el("li", { text: s }))));
        }
        if (r.diff) {
          kids.push(heading("What changed in the story's text"));
          kids.push(el("pre", { class: "doc", text: r.diff + (r.diff_cut ? `\n... ${r.diff_cut} more line(s)` : "") }));
        }
        const again = el("button", { type: "button", text: "Read it again, with the same links and pasted text" });
        again.onclick = async () => {
          again.disabled = true;
          try {
            await post("/api/intake", { job: j, again: true });
            $("stories").replaceChildren();
            follow("intake", $("new-log"), $("new-state"),
                   () => { again.disabled = false; showArtifact("brief.md", $("brief")); });
          } catch (e) { again.disabled = false; setState($("new-state"), "bad", e.message); }
        };
        kids.push(el("div", { class: "row" }, [again]));
        if (!r.diff) kids.push(el("small", { text: "Only Jira's revision moved -- a field, a comment or an " +
          "attachment; the story's text is as it was. Read it again to pick that up; if the brief comes back " +
          "the same, the design, the review and the tests stand as they are." }));
        else kids.push(el("small", { text: "To take the change in: Read it again (the button above reuses what " +
          "this job was given), then Design again (cases that say the same thing keep their id, their " +
          "review and their Xray test; a changed case is a new case), review the new ones, Create in Xray " +
          "for them (an earlier case's test stays in Jira until closed by hand), and ask Cursor to change " +
          "the automated test with what changed." }));
      }
      box.replaceChildren(...kids);
    }, j);
  } catch (e) { $("story-check").disabled = false; setState($("story-check-state"), "bad", e.message); }
};

// `&follow=intake` in the address shows a command of this job that is
// running (or has run) the way its button would: after a refresh, or from
// a link. It reads what the button reads and starts nothing.
(function followFromAddress() {
  const name = new URLSearchParams(location.search).get("follow") || "";
  if (/^[a-z][a-z-]{0,40}$/.test(name) && job()) follow(name, $("new-log"), $("new-state"));
})();

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

// ---- tab 1, second half: design the API tests ------------------------
// Requirements come from a file or from the paste box, whichever is
// chosen. A file is read here, in the browser, and kept apart from the
// paste box: switching back to Paste does not send the file, nor Upload
// the pasted text.
let requirementsFile = null;               // {name, text} of the chosen file
$("design-requirements-file").onchange = async () => {
  const f = $("design-requirements-file").files[0];
  requirementsFile = null;
  $("design-requirements-loaded").textContent = "";
  if (!f) return;
  if (f.size > 2 * 1024 * 1024) {
    setState($("design-requirements-loaded"), "bad", `${f.name} is over 2 MB; paste the part that matters`);
    $("design-requirements-file").value = "";
    return;
  }
  requirementsFile = { name: f.name, text: await f.text(), job: job() };
  setState($("design-requirements-loaded"), "ok",
           `${f.name}: ${requirementsFile.text.length.toLocaleString()} characters read`);
};
// Another job: the file read for the last one is not this one's.
$("job").addEventListener("change", () => {
  requirementsFile = null;
  $("design-requirements-file").value = "";
  $("design-requirements-loaded").textContent = "";
});
function requirementsGiven() {
  if (chosen("req-from") === "paste") return $("design-requirements").value;
  return requirementsFile && requirementsFile.job === job() ? requirementsFile.text : "";
}

// The specification is different: it is kept as a FILE for the job, under
// .workbench/<job>/spec/, whether it was chosen here or taken from a
// Confluence page's attachments. The design step reads it from there when
// nothing is pasted. The page is told where it was put; it names no path.
async function showSpec(j, note) {
  j = j || job();
  const box = $("design-spec");
  box.replaceChildren();
  if (!j) return;
  const kids = [];
  if (note) kids.push(banner(note.cls, note.text));
  let spec = null;
  try { spec = (await api(`/api/spec?job=${encodeURIComponent(j)}`)).spec; }
  catch { /* no such job yet */ }
  // The box was changed while this was being asked: that is another job's
  // answer, and its Remove button would act on a job no longer on screen.
  if (j !== job()) return;
  if (spec && spec.name) {
    kids.push(el("div", { class: "file" },
      el("strong", { text: spec.name }), " ",
      el("span", { text: `${spec.endpoints} endpoint(s), ${spec.format}, ${size(spec.bytes)}` }), " ",
      el("code", { text: spec.path }), " ",
      el("small", { text: spec.origin ? `— ${spec.origin}` : "" })));
    kids.push(el("small", { text: "Used when the box above is empty. What is pasted there wins." }));
    kids.push(el("button", { type: "button", on: { click: async () => {
      try { await post("/api/spec-clear", { job: j }); } catch { /* still listed if so */ }
      $("design-swagger-file").value = "";
      showSpec(j);
    } } }, "Remove"));
  }
  box.replaceChildren(...kids);
}

$("design-swagger-file").onchange = async () => {
  const f = $("design-swagger-file").files[0];
  if (!f) return;
  const j = job();
  const refuse = (text) => {
    // Emptied, so choosing the same file again is a change again.
    $("design-swagger-file").value = "";
    $("design-spec").replaceChildren(banner("bad", text));
  };
  if (!j) { refuse("Name the job first (top right): the file is kept with the job."); return; }
  // Said here: a file the server refuses unread comes back as a dropped
  // connection, which reads as a network fault.
  if (!/\.(json|ya?ml)$/i.test(f.name)) {
    refuse(`Not kept — ${f.name} does not end in .json, .yaml or .yml.`); return;
  }
  if (f.size > 20 * 1024 * 1024) {
    refuse(`Not kept — ${f.name} is ${size(f.size)}; the limit is 20 MB.`); return;
  }
  let note;
  try {
    const r = await watchedFetch(
      `/api/spec-upload?job=${encodeURIComponent(j)}&name=${encodeURIComponent(f.name)}`,
      { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f });
    let body = {};
    try { body = await r.json(); } catch { /* the status says it */ }
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    note = { cls: "ok", text: `${f.name} is kept for this job.` };
  } catch (e) {
    note = { cls: "bad", text: `Not kept — ${e.message}` };
    $("design-swagger-file").value = "";
  }
  showSpec(j, note);
};

$("design-spec-fetch").onclick = async () => {
  const j = job();
  const link = $("design-spec-link").value.trim();
  if (!j || !link) {
    setState($("design-spec-state"), "bad", j ? "paste the page's link" : "name the job first");
    return;
  }
  const options = { "--job": j, "--link": link };
  if ($("design-spec-name").value.trim()) options["--name"] = $("design-spec-name").value.trim();
  $("design-spec-fetch").disabled = true;
  try {
    await post("/api/start", { job: j, runnable: "spec-confluence", options });
    // Its own log, not the design step's pane: taking that over leaves a
    // running design showing "running" for ever.
    follow("spec-confluence", document.createElement("pre"), $("design-spec-state"), async () => {
      $("design-spec-fetch").disabled = false;
      let r = null;
      try {
        const got = await api(`/api/artifact?job=${encodeURIComponent(j)}&name=spec-result.json`);
        if (got.text) r = JSON.parse(got.text);
      } catch { /* shown as nothing */ }
      await showSpec(j, r ? { cls: r.kind === "spec" && r.ok ? "ok" : "bad",
                              text: (r.refused ? "Refused — " : "") + (r.message || "") } : null);
      // Several fit: list them, so one can be named.
      if (r && r.kind === "spec" && !r.ok && (r.candidates || []).length) {
        $("design-spec").append(table(["Attachment", "Size", "Endpoints"],
          r.candidates.map((c) => [c.title, size(c.bytes),
            c.endpoints === null || c.endpoints === undefined
              ? (c.note || "not a specification") : c.endpoints])));
      }
    }, j);
  } catch (e) {
    $("design-spec-fetch").disabled = false;
    setState($("design-spec-state"), "bad", e.message);
  }
};

// `j` is the job the run was started for, not whatever the box holds now.
async function showDesign(j, fresh) {
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
  if (j === job()) { refreshXray("design", fresh); showReview("design"); }
}

// ---- the Review step: which proposed cases stand ---------------------------
// Each decision is sent as it is made and kept with the job
// (case-review.json). Create in Xray and the Agentic step read it there.
async function artifactJson(j, name) {
  try {
    const r = await api(`/api/artifact?job=${encodeURIComponent(j)}&name=${name}`);
    return JSON.parse(r.text || "null");
  } catch { return null; }
}

async function decide(j, what, ids, fields, stateEl) {
  try {
    await post("/api/case-review", Object.assign({ job: j, what, ids }, fields));
    if (stateEl) setState(stateEl, "ok", "saved");
    return true;
  } catch (e) {
    if (stateEl) setState(stateEl, "bad", `not saved: ${e.message}`);
    return false;
  }
}

function reviewTable(j, what, cases, entries, after, done, change, suites) {
  const box = el("div", { class: "review" });
  const st = el("span", { class: "state" });
  const entryOf = (c) => entries[c.id] || {};
  const stateOf = (c) => (entryOf(c).state === "approved" || entryOf(c).state === "rejected")
    ? entryOf(c).state : "proposed";
  const counts = { approved: 0, rejected: 0, proposed: 0 };
  for (const c of cases) counts[stateOf(c)] += 1;
  const bulk = (fields) => async () => {
    if (await decide(j, what, cases.map((c) => c.id), fields, st)) after();
  };
  box.append(el("div", { class: "row" },
    el("button", { type: "button", on: { click: bulk({ state: "approved" }) } }, "Approve all"),
    el("button", { type: "button", on: { click: bulk({ state: "rejected" }) } }, "Reject all"),
    el("small", { text: `${counts.approved} approved, ${counts.rejected} rejected, ${counts.proposed} not yet decided` }),
    st));
  const headers = what === "design" ? ["Approve", "Automate", "Case", "Title", "Endpoint", "Suites", "Steps", "Note", "Write it"]
                                    : ["Approve", "Case", "Title", "Steps", "Note"];
  box.append(table(headers, cases.map((c) => {
    const e = entryOf(c);
    const state = stateOf(c);
    const approve = el("input", { type: "checkbox" });
    approve.checked = state === "approved";
    approve.onchange = async () => {
      if (await decide(j, what, [c.id], { state: approve.checked ? "approved" : "rejected" }, st)) after();
    };
    const automate = el("input", { type: "checkbox" });
    automate.checked = e.automate !== false;
    automate.onchange = async () => {
      if (await decide(j, what, [c.id], { automate: automate.checked }, st)) after();
    };
    const title = el("textarea", { rows: "1", spellcheck: "false", class: "title" });
    title.value = e.title || c.title || "";
    fitHeight(title, 6);
    title.onchange = () => {
      if (title.value.trim()) decide(j, what, [c.id], { title: title.value.trim() }, st);
    };
    const note = el("input", { type: "text", value: e.note || "", placeholder: "why, if rejected or changed" });
    note.onchange = () => decide(j, what, [c.id], { note: note.value.trim() }, st);
    const steps = stepEditor(e.steps || c.steps || [], (clean) => decide(j, what, [c.id], { steps: clean }, st), st);
    const cell = el("div", {}, el("code", { text: c.id }),
      el("small", { class: `flag ${state === "rejected" ? "differs" : ""}`, text: state }),
      e.title && e.title !== c.title ? el("small", { text: "title changed" }) : null,
      e.steps ? el("small", { text: "steps changed" }) : null);
    const row = [approve];
    if (what === "design") row.push(automate);
    row.push(cell, el("div", {}, title));
    if (what === "design") row.push(el("code", { class: "endpoint" },
      // A line break is allowed after each slash, nowhere else.
      ...String(c.endpoint || "").split("/").flatMap((part, i) => (i ? [el("wbr"), "/" + part] : [part]))));
    if (what === "design") {
      // The TestNG groups -- the suites -- the case's test runs in: from
      // suite-map.json's rules, or set here by hand. Empty: the rules again.
      const s = (suites || {})[c.id] || { groups: [], chosen: false };
      const groups = el("textarea", { rows: "1", spellcheck: "false", class: "groups",
                                      title: "comma-separated TestNG groups; each runs as suites/jira/group-<name>.xml. Empty it to go back to the rules." });
      groups.value = (s.groups || []).join(", ");
      fitHeight(groups, 4);
      groups.onchange = async () => {
        const v = groups.value.replace(/\s*\n\s*/g, ",").trim();
        if (await decide(j, what, [c.id], { groups: v }, st)) after();
      };
      row.push(el("div", {}, groups, el("small", { text: s.chosen ? "set by hand" : "from the rules" })));
    }
    row.push(steps, el("div", {}, note));
    if (what === "design") {
      // What the case's last run came to -- if it is about the change
      // there is now. A case written into a change since discarded (or a
      // design since cleared) is not "written" any more.
      let ran = (done || {})[c.id];
      if (ran && ran.written && (!change || ran.change !== change)) ran = null;
      const go = el("button", { type: "button", text: ran && ran.written ? "Again" : "Automate",
                                title: "Cursor writes this case's test, in the job's one change",
                                on: { click: () => automateCases([c.id], j) } });
      go.disabled = !(state === "approved" && e.automate !== false);
      row.push(el("div", { class: "automate-cell" }, go,
        ran ? el("small", { class: `flag ${ran.written && ran.state === "pending-review" ? "" : "differs"}`, text: ran.said || ran.state }) : null));
    }
    return row;
  })));
  return box;
}

// A text box as tall as what it holds (up to a limit), so nothing is cut.
function fitHeight(box, max = 10) {
  const fit = () => {
    box.style.height = "auto";
    const line = parseFloat(getComputedStyle(box).lineHeight) || 18;
    box.style.height = `${Math.min(box.scrollHeight + 2, line * max + 12)}px`;
  };
  box.addEventListener("input", fit);
  requestAnimationFrame(fit);
  return box;
}

// A case's steps, editable: action, data and expected result of each, a
// step added or removed. Saved as the person's correction when a field is
// left; the Agentic step and Create in Xray use the corrected steps.
function stepEditor(given, save, st) {
  const box = el("div", { class: "steps-edit" });
  const rows = [];
  const send = () => {
    const clean = rows.map((r) => ({ action: r.action.value.trim(), data: r.data.value.trim(),
                                     expected: r.expected.value.trim() }))
                      .filter((s) => s.action || s.expected);
    if (!clean.length) { setState(st, "bad", "a case needs at least one step with an action or an expected result"); return; }
    save(clean);
  };
  const add = (s) => {
    // Each of a step's three fields can be removed (its ×) and brought back
    // (+ do / + data / + expect). A field the case has no text for starts
    // removed; a new step shows all three. A step keeps a do or an expect.
    const r = {};
    const fresh = !s.action && !s.data && !s.expected;
    const parts = [el("span", { class: "n" })];
    const restore = el("div", { class: "restore" });
    const shows = [];
    for (const [key, word, cls, hint] of [["action", "do", "do", "what is sent / done"],
                                          ["data", "data", "data", "the data it needs"],
                                          ["expected", "expect", "expect", "what is expected"]]) {
      const field = el("textarea", { rows: "1", spellcheck: "false", class: cls, placeholder: hint });
      field.value = s[key] || "";
      field.onchange = send;
      fitHeight(field, 8);
      const lbl = el("span", { class: `lbl l-${cls}`, text: word });
      const drop = el("button", { type: "button", class: `drop d-${cls}`, text: "×",
                                  title: `remove the ${word} of this step` });
      const back = el("button", { type: "button", class: "linkish", text: `+ ${word}`,
                                  title: `give this step a ${word} again` });
      r[key] = field;
      const show = (on) => {
        for (const n of [lbl, field, drop]) n.hidden = !on;
        back.hidden = on;
        restore.hidden = ![...restore.children].some((c) => !c.hidden);
      };
      r["show_" + key] = show;
      drop.onclick = () => {
        const other = key === "action" ? r.expected : key === "expected" ? r.action : null;
        const keeps = (key === "data") || (other && !other.hidden && other.value.trim());
        if (!keeps) {
          setState(st, "bad", "a step needs a do or an expect: to drop the whole step, press Remove step");
          return;
        }
        field.value = "";
        show(false);
        send();
      };
      back.onclick = () => { show(true); field.focus(); };
      parts.push(lbl, field, drop);
      restore.append(back);
      shows.push([show, fresh || Boolean(String(s[key] || "").trim())]);
    }
    const remove = el("button", { type: "button", class: "remove", title: "remove this step", text: "Remove step" });
    r.node = el("div", { class: "step" }, ...parts, remove, restore);
    for (const [show, on] of shows) show(on);
    remove.onclick = () => {
      if (rows.length === 1) { setState(st, "bad", "a case needs at least one step"); return; }
      rows.splice(rows.indexOf(r), 1); r.node.remove(); number(); send();
    };
    rows.push(r);
    box.insertBefore(r.node, more);
    number();
  };
  const number = () => rows.forEach((r, i) => { r.node.querySelector(".n").textContent = `${i + 1}.`; });
  const more = el("button", { type: "button", class: "more-step", text: "+ step",
                              on: { click: () => add({}) } });
  box.append(more);
  for (const s of given) add(s);
  if (!given.length) add({});
  return box;
}

// "Automate the approved cases" can be pressed only when there is one, and
// not while a design runs (it would hand Cursor a design about to change).
let designRunning = false;
function automateReady(cases, entries) {
  const b = $("auto-selected");
  if (!b) return;
  const n = (cases || []).filter((c) => c && entries[c.id] && entries[c.id].state === "approved"
                                        && entries[c.id].automate !== false).length;
  b.disabled = designRunning || n === 0;
  b.title = designRunning ? "a design is running; automate when it has finished"
    : n ? `Cursor writes the tests of the ${n} approved case(s) marked Automate`
        : "approve a case and tick Automate first (Review the designed cases)";
}

async function showReview(what) {
  const j = job();
  const box = $(`${what}-review`);
  if (!j) { box.replaceChildren(); return; }
  // Someone is typing in the table: redraw when they leave it, not under
  // their hands (the field's change is saved first).
  const typing = document.activeElement;
  if (typing && box.contains(typing) && typing.matches("input[type=text], textarea")) {
    if (!box.dataset.redraw) {
      box.dataset.redraw = "1";
      box.addEventListener("focusout", () => setTimeout(() => {
        delete box.dataset.redraw;
        showReview(what);
      }, 300), { once: true });
    }
    return;
  }
  const [result, review, done, agentReview, suites] = await Promise.all([
    artifactJson(j, what === "design" ? "design.json" : "manual-result.json"),
    artifactJson(j, "case-review.json"),
    what === "design" ? artifactJson(j, "automated-cases.json") : Promise.resolve(null),
    what === "design" ? artifactJson(j, "review.json") : Promise.resolve(null),
    what === "design" ? api(`/api/suite-groups?job=${encodeURIComponent(j)}`).catch(() => null) : Promise.resolve(null)]);
  if (j !== job()) return;
  const cases = (result && (what === "design" ? result.test_cases : result.new_cases)) || [];
  const entries = (review && review[what]) || {};
  if (!cases.length) {
    if (what === "design") automateReady([], {});
    box.replaceChildren(el("small", { text: `no ${what} cases in this job yet` }));
    showFlow();
    return;
  }
  const kids = [];
  if (result.mock) kids.push(banner("warn", "These are a stand-in's cases (mock Cursor); a review of them goes nowhere real."));
  if (suites && suites.problem) kids.push(banner("warn", `suite-map.json: ${suites.problem}. Every case gets the default groups.`));
  if (what === "design") automateReady(cases, entries);
  const live = agentReview && ["pending-review", "verify-failed", "pushed", "approved-local", "push-failed"]
    .includes(agentReview.state) ? agentReview.change : "";
  kids.push(reviewTable(j, what, cases.filter((c) => c && c.id), entries, () => {
    showReview(what);
    refreshXray(what, true);
  }, done, live, suites && suites.cases));
  box.replaceChildren(...kids);
  showFlow();
}

// Where the job is, in order: what has been done is read from what the
// job holds, not remembered by the page.
async function showFlow() {
  const j = job();
  const flow = $("flow");
  if (!flow) return;
  if (!j) { flow.replaceChildren(); return; }
  const [brief, plan, design, review, xray, agent] = await Promise.all([
    artifactText(j, "brief.md"), artifactText(j, "plan.md"), artifactJson(j, "design.json"),
    artifactJson(j, "case-review.json"), artifactJson(j, "xray-tests.json"), artifactJson(j, "review.json")]);
  if (j !== job()) return;
  // Decisions about cases the design still has; a re-design renumbers a
  // case whose text changed, and the old number's decision is not its.
  const nowIds = new Set(((design && design.test_cases) || []).map((c) => c && c.id));
  const approved = Object.entries((review && review.design) || {})
    .filter(([id, e]) => e && e.state === "approved" && nowIds.has(id)).length;
  const keys = Object.keys((xray && xray.tests) || {}).filter((k) => k.startsWith("design:")).length;
  const agentState = (agent && agent.state) || "none";
  // Each step is a link to where it is done: an element of this tab, or
  // another tab.
  const steps = [
    ["Read it", Boolean(brief), "", "new", "story"],
    ["Is it automated?", Boolean(plan), "", "new", "run-locate"],
    ["Design", Boolean(design && design.test_cases && design.test_cases.length), "", "new", "run-design"],
    ["Review", approved > 0, approved ? `${approved} approved` : "", "new", "design-review"],
    ["Create in Xray", keys > 0, keys ? `${keys} test(s)` : "", "new", "xray-design-create"],
    ["Automate", ["pending-review", "approved-local", "pushed"].includes(agentState),
     agentState !== "none" ? agentState : "", "new", "auto-selected"],
    ["Commit and push", ["pushed", "approved-local"].includes(agentState), "", "agent", "agent-push"],
  ];
  let now = steps.findIndex(([, done]) => !done);
  flow.replaceChildren(...steps.map(([name, done, detail, tab, target], i) =>
    el("li", { class: done ? "done" : (i === now ? "now" : "") },
      el("button", { type: "button", class: "step-link", "data-tab": tab, "data-target": target,
                     title: `go to: ${name}`,
                     on: { click: () => goToStep(tab, target) } },
         name + (detail ? ` (${detail})` : "")))));
}

function goToStep(tab, target) {
  const button = document.querySelector(`.tabs button[data-tab="${tab}"]`);
  if (button && button.getAttribute("aria-selected") !== "true") button.click();
  const there = $(target);
  if (!there) return;
  there.scrollIntoView({ behavior: "smooth", block: "center" });
  // A moment's highlight, so the eye lands on it.
  there.classList.add("here");
  setTimeout(() => there.classList.remove("here"), 1600);
  if (typeof there.focus === "function" && !there.disabled) there.focus({ preventScroll: true });
}
async function artifactText(j, name) {
  try { return (await api(`/api/artifact?job=${encodeURIComponent(j)}&name=${name}`)).text || ""; }
  catch { return ""; }
}

// ---- Xray: the cases as Jira Test issues ---------------------------------
// One block on each of the two tabs that produce cases. The last answer
// kept for the job is drawn at once; the server is asked again after a run
// or a create, and whenever the kept answer says the switch is off, broken
// or a stand-in (see refreshXray). The keys already created are shown.
const xrayStatus = {};              // what -> the last status read for the job in the box

function showXrayKeys(what, st) {
  const box = $(`xray-${what}-result`);
  const kids = [];
  const keys = (st && st.keys && st.keys[what]) || {};
  const names = Object.keys(keys).sort();
  if (st && st.problem) kids.push(banner("bad", `Xray cannot be written to: ${st.problem}`));
  else if (st && st.flavour === "cloud")
    kids.push(banner("warn", "The xray configuration says Xray Cloud. Creating tests there is not " +
      "supported yet: import xray.csv / manual-new-cases.csv with Xray's importer."));
  else if (st && !st.create_tests)
    kids.push(banner("warn", "Creating tests in Jira is switched off. To allow it, set " +
      "jira_config.publish.create_tests to true in program_configuration.json."));
  else if (st && st.mock_jira) kids.push(banner("warn", "MOCK Jira: a create changes a file on this machine; no Jira is contacted."));
  else if (st && st.create_tests && (st.cases || {})[what] > 0 && !(st.real || {})[what])
    kids.push(banner("warn", `The ${what} result was made by a stand-in, or does not say how it was made, ` +
      "and Jira here is real: it will not be sent. Run the step again for real first."));
  if (st && st.notes && st.notes[what]) kids.push(banner("warn", st.notes[what]));
  if (st && st.stale && st.stale[what])
    kids.push(banner("warn", "The design was made from an earlier reading of the story than this job " +
      "has now. Create in Xray will refuse it: Design again, review the cases, then create."));
  const orphans = (st && st.orphans && st.orphans[what]) || {};
  const gone = Object.keys(orphans).sort();
  const live = names.filter((n) => !orphans[n]);
  if (live.length) {
    kids.push(heading(`Already in ${st.mock_jira ? "the mock " : ""}Xray`, live.length));
    kids.push(table(["Case", "Jira test"], live.map((n) => [el("code", { text: n }), el("code", { text: keys[n] })])));
  }
  if (gone.length) {
    kids.push(heading("Tests for cases no longer in the result", gone.length));
    kids.push(el("small", { text: "Made for a case a re-design dropped or renumbered. Nothing here closes an " +
      "issue: close or re-point these in Jira by hand." }));
    kids.push(table(["Was case", "Jira test"], gone.map((n) => [el("code", { text: n }), el("code", { text: orphans[n] })])));
  }
  box.replaceChildren(...kids);
}

// What a status says, drawn: the keys, the banners, the button.
function settleXray(what, st) {
  const st0 = $(`xray-${what}-state`);
  xrayStatus[what] = st;
  showXrayKeys(what, st);
  const tally = ((st.review || {})[what]) || {};
  const approved = tally.approved || 0;
  const usable = approved > 0 && (st.mock_jira || (st.real || {})[what]);
  $(`xray-${what}-create`).disabled = !(st.create_tests && usable);
  if (st.story && !$(`xray-${what}-story`).value.trim()) $(`xray-${what}-story`).value = st.story;
  const have = Object.keys((st.keys || {})[what] || {}).length;
  if (!(st.cases || {})[what]) setState(st0, "", `no ${what} cases in this job yet`);
  else if (!approved) setState(st0, "warn", `${st.cases[what]} case(s), none approved yet: review them above first`);
  else setState(st0, "", `${approved} of ${st.cases[what]} case(s) approved; ${have} already in Xray`);
}

// `fresh`: ask the server again now. Otherwise the last answer kept for
// the job is used and no command is started -- opening a tab must not set
// a process going each time. A create checks everything again itself, and
// after a run or a create the block is refreshed fresh.
function refreshXray(what, fresh) {
  const j = job();
  const st0 = $(`xray-${what}-state`);
  $(`xray-${what}-create`).disabled = true;
  xrayStatus[what] = null;
  if (!j) { $(`xray-${what}-result`).replaceChildren(); return; }
  api(`/api/artifact?job=${encodeURIComponent(j)}&name=xray-status.json`).then((r) => {
    let was = null;
    try { was = JSON.parse(r.text || "null"); } catch { was = null; }
    if (j !== job()) return;
    const had = Boolean(was && was.kind === "xray-status" && was.job === j);
    // A kept answer that says "on" is good until something is run; one that
    // says "off", "broken" or "stand-in" is worth asking about again, because
    // the person has very likely just changed the configuration to fix it.
    const unsettled = !had || !was.create_tests || was.problem || was.mock_jira;
    if (had) {
      settleXray(what, was);
      if (fresh || unsettled) setState(st0, "run", "checking what is switched on now…");
    }
    if (fresh || unsettled) askXray(what, j);
  }).catch(() => { if (j === job()) askXray(what, j); });
}

function askXray(what, j) {
  const st0 = $(`xray-${what}-state`);
  const begin = post("/api/start", { job: j, runnable: "xray-status", options: { "--job": j } })
    // Already being asked (the page was drawn twice quickly): follow that run.
    .catch((e) => { if (/already running/.test(e.message)) return {}; throw e; });
  begin
    .then((begun) => follow("xray-status", document.createElement("pre"), document.createElement("span"), async (run) => {
      if (j !== job()) return;
      let st = null;
      try {
        const r = await api(`/api/artifact?job=${encodeURIComponent(j)}&name=xray-status.json`);
        st = JSON.parse(r.text || "null");
      } catch { st = null; }
      const fresh = run && run.state === "done" && st && st.kind === "xray-status" && st.job === j &&
                    String(st.at || "") >= String((begun && begun.started) || "");
      if (fresh) {
        settleXray(what, st);
      } else {
        xrayStatus[what] = null;
        $(`xray-${what}-create`).disabled = true;
        showXrayKeys(what, null);
        setState(st0, "bad", "what can be created could not be found out; the Logs tab has the reason");
      }
    }, j))
    .catch((e) => setState(st0, "bad", e.message));
}

function wireXray(what) {
  $(`xray-${what}-create`).onclick = () => {
    const j = job();
    const st = xrayStatus[what];
    if (!st || st.job !== j) { setState($(`xray-${what}-state`), "bad", "the status is not for this job; it is being read again"); refreshXray(what, true); return; }
    const story = $(`xray-${what}-story`).value.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9_]+-[0-9]+$/.test(story)) { setState($(`xray-${what}-state`), "bad", "give the story key, like ABC-12"); return; }
    const n = (((st.review || {})[what]) || {}).approved || 0;
    const have = Object.keys((st.keys || {})[what] || {}).length;
    const again = prompt(
      (st.mock_jira ? "MOCK: this changes a file on this machine. No Jira is contacted.\n\n"
                    : "This CREATES ISSUES IN JIRA now, and they cannot be deleted from here.\n\n") +
      `From job ${j}: up to ${Math.max(0, n - have)} new Test issue(s) in the project of ${story}, one an approved case, ` +
      `each linked to ${story}.${have ? ` ${have} case(s) already have a test and are not created again.` : ""}\n\n` +
      "Type the story key again to go ahead:");
    if (again === null) return;
    if (!again.trim()) { setState($(`xray-${what}-state`), "bad", "nothing was typed, so nothing was written"); return; }
    $(`xray-${what}-create`).disabled = true;
    $(`xray-${what}-log`).hidden = false;
    post("/api/start", { job: j, runnable: "xray-create", options: {
      "--job": j, "--what": what, "--story": story, "--confirm-story": again.trim(),
      "--expect": st.mock_jira ? "mock" : "real" } })
      .then(() => follow("xray-create", $(`xray-${what}-log`), $(`xray-${what}-state`), async () => {
        let r = null;
        try {
          const a = await api(`/api/artifact?job=${encodeURIComponent(j)}&name=xray-result.json`);
          r = JSON.parse(a.text || "null");
        } catch { r = null; }
        const box = $(`xray-${what}-result`);
        const kids = [];
        if (!r) kids.push(banner("bad", "The outcome could not be read; look at the log and in Jira."));
        else if (r.kind === "error") kids.push(banner("bad", (r.refused ? "Refused — " : "Failed — ") + (r.message || "")));
        else {
          kids.push(banner(r.ok === true ? "ok" : (r.ok === false ? "bad" : "warn"),
            (r.ok === null || r.ok === undefined ? "NOT KNOWN — " : "") + (r.message || "")));
          const rows = (r.created || []).map((c) => [el("code", { text: c.id }), el("code", { text: c.key }),
            el("div", {}, el("span", { text: c.title || "" }),
               el("small", { text: `${c.steps}/${c.steps_of} step(s)${c.linked ? ", linked" : ", NOT linked"}` }),
               c.note ? el("small", { class: "flag differs", text: c.note }) : null)]);
          if (rows.length) kids.push(table(["Case", "Jira test", "Created"], rows));
          for (const c of r.changed || []) kids.push(banner("warn", `${c.id} → ${c.key}: ${c.note}`));
        }
        box.replaceChildren(...kids);
        if (j === job()) refreshXray(what, true);
      }, j))
      .catch((e) => { setState($(`xray-${what}-state`), "bad", e.message); refreshXray(what, true); });
  };
}
wireXray("design");
wireXray("manual");

// The design's conversation with Cursor is in design-cursor.log, shown in
// the tab's one Cursor log (at the bottom, "The design").
$("design-cursor-go").onclick = () => { setCursorSource("design"); goToStep("new", "cursor-log"); };

$("run-design").onclick = async () => {
  if (chosen("story-from") === "xray") { xrayImport(); return; }
  const button = $("run-design");
  // One at a time: a second click used to wipe the Cursor pane of the run
  // already going, fail with "already running", and stop the pane's timer.
  button.disabled = true;
  let stopCursorLog = () => {};
  if (chosen("req-from") === "upload" && $("design-requirements-file").files.length && !requirementsGiven()) {
    setState($("design-state"), "bad", "the requirements file is still being read, or could not be; choose it again");
    button.disabled = false;
    return;
  }
  try {
    const j = job();
    // Where the Cursor log ends NOW, read before the run starts, so the
    // pane shows this run and nothing is lost between start and first poll.
    setCursorSource("design");
    watchCursorLog();
    designRunning = true;
    $("auto-selected").disabled = true;
    await post("/api/design", {
      job: j,
      service: $("design-service").value.trim(),
      speed: $("design-speed").value,
      mode: $("design-mode").value,
      fresh: $("design-fresh").checked,
      whole_spec: $("design-whole").checked,
      // Only a pasted specification is sent; an uploaded or fetched one is
      // already kept with the job.
      swagger: chosen("spec-from") === "paste" ? $("design-swagger").value : "",
      requirements: requirementsGiven(),
      notes: $("design-notes").value,
    });
    follow("agent-design", $("design-log"), $("design-state"), () => {
      designRunning = false;
      stopCursorLog();
      showDesign(j, true);
      // One run. Left ticked, every later click would pay again in silence.
      $("design-fresh").checked = false;
      button.disabled = false;
    });
  } catch (e) {
    stopCursorLog();
    designRunning = false;
    showReview("design");
    button.disabled = false;
    setState($("design-state"), "bad", e.message);
  }
};
// A design made earlier is still this job's design after a refresh.
showDesign();
showSpec();
$("job").addEventListener("change", () => { showDesign(); showSpec(); refreshXray("manual"); showReview("manual"); });
// The story's existing Xray tests, as the design: no Cursor call. Then the
// usual Review and the Agentic step, with the tests' real keys.
// Started by Design the tests when the Jira story choice is "Use the
// story's Xray tests".
async function xrayImport() {
  const j = job();
  if (!j) { setState($("design-state"), "bad", "name the job first"); return; }
  $("run-design").disabled = true;
  try {
    await post("/api/start", { job: j, runnable: "xray-import", options: { "--job": j } });
    follow("xray-import", $("design-log"), $("design-state"), async () => {
      $("run-design").disabled = false;
      const r = await artifactJson(j, "xray-import.json");
      if (r && r.ok) setState($("design-state"), "ok", r.message);
      else if (r && r.message) setState($("design-state"), "bad", r.message);
      showDesign(j, true);
      if (typeof showFlow === "function") showFlow();
    }, j);
  } catch (e) { $("run-design").disabled = false; setState($("design-state"), "bad", e.message); }
}

$("clear-design").onclick = async () => {
  const j = job();
  if (!j) { setState($("design-state"), "bad", "name the job first"); return; }
  if (!confirm("Clear this job's design? The cases, design.md and the kept Cursor replies are moved " +
               "aside; the next design asks Cursor afresh. Review decisions and Xray keys stay.")) return;
  try {
    const r = await post("/api/design-clear", { job: j });
    setState($("design-state"), r.cleared ? "ok" : "", r.cleared ? "design cleared" : "no design to clear");
    $("design-log").textContent = "";
    showDesign(j, true);
    if (typeof showFlow === "function") showFlow();
    pollCursorLog(false);              // the design's log went with it
  } catch (e) { setState($("design-state"), "bad", e.message); }
};

$("stop-design").onclick = async () => {
  try {
    const r = await post("/api/stop", { job: job(), runnable: "agent-design" });
    if (!(r && r.stopped)) await post("/api/stop", { job: job(), runnable: "xray-import" });
  } catch (e) { setState($("design-state"), "bad", e.message); }
};

// ---- tab 2: convert ---------------------------------------------------
// The form is written out in index.html: what a person sets is a handful
// of the converter's options, and the rest keep their defaults. The
// server still decides what a command may be given (jobs.RUNNABLES).
//
// A browser does not tell a page where a chosen file is, so the file is
// sent to the server, which keeps it under target/convert-input/ and
// answers with the path the converter is then given.
let uploaded = [];            // [{name, path, bytes}] as the server holds them
let uploading = false;
const convertJob = () => job() || "convert";
const size = (n) => n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB`
                  : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} bytes`;

function showUploaded(problem) {
  const box = $("convert-uploaded");
  if (uploading) { box.replaceChildren(el("div", { text: "Copying…" })); return; }
  const kids = [];
  if (problem) kids.push(banner("bad", problem));
  for (const f of uploaded) {
    kids.push(el("div", { class: "file" }, el("strong", { text: f.name }),
                 el("span", { text: size(f.bytes) }), el("code", { text: f.path })));
  }
  if (uploaded.length) {
    kids.push(el("button", { type: "button", on: { click: forgetUploads } }, "Remove"));
  }
  box.replaceChildren(...kids);
}

async function forgetUploads() {
  try { await post("/api/upload-clear", {}); } catch { /* shown as still listed */ }
  $("convert-files").value = "";
  await loadUploads();
}

async function loadUploads(problem) {
  try { uploaded = (await api("/api/uploads")).files || []; }
  catch { uploaded = []; }
  showUploaded(problem);
  warn();
}

$("convert-files").onchange = async () => {
  const files = [...$("convert-files").files];
  if (!files.length) return;
  uploading = true;
  showUploaded();
  let problem = "";
  try {
    // What is listed is what converts: a new choice replaces the old one.
    await post("/api/upload-clear", {});
    for (const f of files) {
      const r = await watchedFetch(`/api/upload?name=${encodeURIComponent(f.name)}`, {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f });
      let body = {};
      try { body = await r.json(); } catch { /* not JSON: the status says it */ }
      if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    }
  } catch (e) {
    problem = `Not copied — ${e.message}`;
    try { await post("/api/upload-clear", {}); } catch { /* listed below if so */ }
    $("convert-files").value = "";
  }
  uploading = false;
  await loadUploads(problem);
};

const picked = (name) =>
  (document.querySelector(`input[name="${name}"]:checked`) || {}).value || "";

function convertOptions() {
  const out = {};
  if (uploaded.length) out["--input"] = uploaded.map((f) => f.path).join(",");
  document.querySelectorAll("#convert-form [data-opt]").forEach((e) => {
    if (e.type === "checkbox") { if (e.checked) out[e.dataset.opt] = true; }
    else if (e.value.trim()) out[e.dataset.opt] = e.value.trim();
  });
  if (picked("cv-diagrams") === "png") out["--diagram-png"] = true;
  if (picked("cv-diagrams") === "only") out["--diagrams-only"] = true;
  if (picked("cv-phase") === "off") out["--no-phase-specs"] = true;
  else out["--phase-specs"] = true;
  if (picked("cv-cursor") === "on") out["--cursor-assist"] = true;
  else out["--no-cursor-assist"] = true;
  return out;
}

// Say what a destructive combination will do BEFORE the run, not after.
function warn() {
  const o = convertOptions();
  const msgs = [];
  if (!o["--input"]) msgs.push("No ReadyAPI project file is chosen yet.");
  if (o["--clean"]) {
    msgs.push("Clean first deletes the previously-generated files of " +
      (o["--suite-name"] ? `suite ${o["--suite-name"]}` : "this suite") +
      " before writing.");
  }
  if (o["--classic"]) {
    msgs.push("Classic mode is whole-tree. If this project already holds " +
              "phase-mode suites the converter will refuse, and converting " +
              "everything classic replaces them.");
  }
  if (o["--cursor-assist"]) {
    msgs.push("Cursor assist calls the REAL Cursor after the convert, whatever " +
              "tools/agent/mock.json says. It can cost money and edit files.");
  }
  // Only for a chosen file that has DataSource steps reading a workbook:
  // the server counts them (jobs.workbook_steps). A suite with none needs
  // no folder, and was warned about for nothing.
  const reads = uploaded.filter((f) => (f.workbooks || 0) > 0);
  if (!o["--data-dir"] && reads.length) {
    msgs.push(`No workbooks folder is set, and ${reads.map((f) => `${f.name} has ${f.workbooks} ` +
              "DataSource step(s) reading a workbook").join("; ")}. Without the folder ` +
              "every such DataSource column comes out EMPTY and the run looks like an API " +
              "problem rather than a missing path.");
  }
  const box = $("convert-warnings");
  box.hidden = msgs.length === 0;
  box.replaceChildren(el("ul", {}, msgs.map((m) => el("li", { text: m }))));
}
$("convert-form").addEventListener("input", warn);
$("convert-form").addEventListener("change", warn);
loadUploads();

function runVerifyAll(j) {
  $("verify-block").hidden = false;
  post("/api/start", { job: j, runnable: "verify-all", options: {} })
    .then(() => follow("verify-all", $("verify-log"), $("verify-state")))
    .catch((e) => setState($("verify-state"), "bad", e.message));
}

$("run-convert").onclick = async () => {
  if (uploading) { setState($("convert-state"), "bad", "the files are still being copied"); return; }
  const o = convertOptions();
  if (!o["--input"]) {
    setState($("convert-state"), "bad", "choose the ReadyAPI project XML to convert first");
    return;
  }
  if (o["--clean"] &&
      !confirm("Clean first deletes previously-generated files of this suite " +
               "before writing. Continue?")) return;
  const j = convertJob();
  const verifyAfter = $("convert-verify").checked;
  $("verify-block").hidden = true;
  try {
    await post("/api/start", { job: j, runnable: "convert", options: o });
    follow("convert", $("convert-log"), $("convert-state"), (st) => {
      if (!verifyAfter) return;
      if (st.state === "done") { runVerifyAll(j); return; }
      // Checking a tree a failed convert left half-written says nothing
      // about either. Said, so the tick is not taken for a run.
      $("verify-block").hidden = false;
      $("verify-log").textContent = "";
      stepsAbove($("verify-log")).replaceChildren();
      setState($("verify-state"), "bad",
               "not run: the convert did not finish cleanly");
    });
  } catch (e) { setState($("convert-state"), "bad", e.message); }
};

$("stop-convert").onclick = async () => {
  try {
    let stopped = false;
    for (const runnable of ["convert", "verify-all", "audit-service-keys", "audit-token-chain"]) {
      const r = await post("/api/stop", { job: convertJob(), runnable });
      if (r && r.stopped) stopped = true;
    }
    if (!stopped) setState($("convert-state"), "", "nothing was running");
  } catch (e) { setState($("convert-state"), "bad", e.message); }
};

for (const [btn, runnable] of [["run-keys", "audit-service-keys"],
                               ["run-token", "audit-token-chain"]]) {
  $(btn).onclick = async () => {
    try {
      await post("/api/start", { job: convertJob(), runnable, options: {} });
      follow(runnable, $("convert-log"), $("convert-state"));
    } catch (e) { setState($("convert-state"), "bad", e.message); }
  };
}

// ---- manual tests ------------------------------------------------------
// Requirement files are kept for the job under .workbench/<job>/docs/,
// as the specification is: the page sends the file and is told the path.
// Everything shown here that came from a document, from Jira or from
// Cursor is put on the page as text.
async function showManualDocs(j, problem) {
  j = j || job();
  const box = $("manual-docs");
  // Another job's answer arriving late must not touch what is on screen.
  if (j !== job()) return;
  if (!j) { box.replaceChildren(); return; }
  let files = [];
  try { files = (await api(`/api/docs?job=${encodeURIComponent(j)}`)).files || []; }
  catch { /* no such job yet */ }
  if (j !== job()) return;
  const kids = [];
  if (problem) kids.push(banner("bad", problem));
  for (const f of files) {
    kids.push(el("div", { class: "file" }, el("strong", { text: f.name }), " ",
      el("span", { text: size(f.bytes) }), " ", el("code", { text: f.path }), " ",
      el("button", { type: "button", on: { click: async () => {
        try { await post("/api/doc-remove", { job: j, name: f.name }); } catch { /* still listed */ }
        showManualDocs(j);
      } } }, "Remove")));
  }
  box.replaceChildren(...kids);
}

$("manual-files").onchange = async () => {
  const files = [...$("manual-files").files];
  const j = job();
  $("manual-files").value = "";            // so the same choice is a change again
  if (!files.length) return;
  // One batch at a time, and no run started on half of it.
  $("manual-files").disabled = true;
  $("manual-run").disabled = true;
  const done = () => { $("manual-files").disabled = false; $("manual-run").disabled = false; };
  if (!j) { done(); $("manual-docs").replaceChildren(
    banner("bad", "Name the job first (top right): the files are kept with the job.")); return; }
  const problems = [];
  for (const f of files) {
    if (f.size > 20 * 1024 * 1024) { problems.push(`${f.name}: ${size(f.size)}, the limit is 20 MB`); continue; }
    if (!/\.(txt|md|markdown|csv|json|log|html?|docx|pdf|xlsx)$/i.test(f.name)) {
      problems.push(`${f.name}: this kind of file is not read`); continue;
    }
    try {
      const r = await watchedFetch(
        `/api/doc-upload?job=${encodeURIComponent(j)}&name=${encodeURIComponent(f.name)}`,
        { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f });
      let body = {};
      try { body = await r.json(); } catch { /* the status says it */ }
      if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    } catch (e) { problems.push(`${f.name}: ${e.message}`); }
  }
  done();
  showManualDocs(j, problems.length ? `Not kept — ${problems.join("; ")}` : "");
};

async function manualArtifact(j, name) {
  try {
    const got = await api(`/api/artifact?job=${encodeURIComponent(j)}&name=${name}`);
    return got.text ? JSON.parse(got.text) : null;
  } catch { return null; }
}

async function showManualPull(j) {
  j = j || job();
  const box = $("manual-inventory");
  if (j !== job()) return;
  if (!j) { box.replaceChildren(); return; }
  const r = await manualArtifact(j, "manual-pull.json");
  if (j !== job()) return;
  box.replaceChildren();
  if (!r) return;
  if (r.kind !== "manual-pull") {
    box.replaceChildren(banner("bad", (r.refused ? "Refused — " : "Failed — ") + (r.message || "")));
    return;
  }
  const kids = [];
  if (r.mock_jira) kids.push(banner("warn", "MOCK. These tests are sample data from this " +
    "machine (tools/agent/mock.json). No Jira was contacted."));
  if (!r.complete || r.limited) {
    kids.push(banner("bad", "NOT THE WHOLE LIST — " +
      (r.why_incomplete || `the first ${r.count} of ${r.total} were read`) +
      ". A new case may repeat a test that was not read."));
  }
  if (r.steps_note) kids.push(banner("warn", r.steps_note));
  kids.push(banner("ok", r.message || ""));
  box.replaceChildren(...kids);
}

$("manual-pull").onclick = async () => {
  const j = job();
  const project = $("manual-project").value.trim();
  if (!j || !project) {
    setState($("manual-pull-state"), "bad", j ? "give the project key" : "name the job first");
    return;
  }
  const options = { "--job": j, "--project": project };
  if ($("manual-type").value.trim()) options["--type"] = $("manual-type").value.trim();
  if ($("manual-extra").value.trim()) options["--extra"] = $("manual-extra").value.trim();
  $("manual-pull").disabled = true;
  try {
    await post("/api/start", { job: j, runnable: "manual-inventory", options });
    follow("manual-inventory", $("manual-log"), $("manual-pull-state"), () => {
      $("manual-pull").disabled = false;
      showManualPull(j);
    }, j);
  } catch (e) {
    $("manual-pull").disabled = false;
    setState($("manual-pull-state"), "bad", e.message);
  }
};

async function showManual(j) {
  j = j || job();
  const box = $("manual-result");
  if (j !== job()) return;
  const r = j ? await manualArtifact(j, "manual-result.json") : null;
  if (j !== job()) return;
  box.replaceChildren();
  for (const id of ["dl-manual-md", "dl-manual-new", "dl-manual-upd"]) $(id).hidden = true;
  if (!r) return;
  if (r.kind !== "manual-tests") {
    box.replaceChildren(banner("bad", (r.refused ? "Refused — " : "Failed — ") + (r.message || "")));
    return;
  }
  for (const [id, name] of [["dl-manual-md", "manual-tests.md"],
                            ["dl-manual-new", "manual-new-cases.csv"],
                            ["dl-manual-upd", "manual-xray-updates.csv"]]) {
    $(id).hidden = false;
    $(id).href = `/api/download?job=${encodeURIComponent(j)}&name=${name}`;
  }
  const kids = [];
  const inv = r.inventory || {};
  if (r.mock) kids.push(banner("warn", "MOCK. These cases were made up by the stand-in for " +
    "Cursor (tools/agent/mock.json). They are not test cases."));
  if (inv.mock_jira && !r.mock) kids.push(banner("warn", "The existing tests these were " +
    "compared with are sample data, not Jira's."));
  // What is not whole, before what was found.
  for (const f of r.failures || []) kids.push(banner("bad", f));
  for (const w of r.warnings || []) kids.push(banner("warn", w));
  for (const n of r.notes || []) kids.push(banner("warn", n));
  const d = r.dropped || {};
  if (d.invented) kids.push(banner("warn", `${d.invented} update(s) named a test that was ` +
    "not among the ones sent, and were dropped."));
  if (d.over) kids.push(banner("warn", `${d.over} case(s) beyond the limit for this speed were left out.`));
  if (d.empty) kids.push(banner("warn", `${d.empty} entr(ies) had no title, no step or no change, and were dropped.`));
  if (d.steps_over) kids.push(banner("warn", `${d.steps_over} step(s) beyond the limit for one case were left out.`));
  kids.push(banner("ok", (r.message || "") + (inv.used
    ? ` Compared with ${inv.sent} existing test(s) of ${inv.project || "the project"}, read ${inv.pulled_at || "earlier"}.`
    : "")));
  const flags = (x) => (x.flags || []).map((f) => el("small", { class: "flag differs", text: f }));
  const cases = r.new_cases || [];
  kids.push(heading("New test cases", cases.length));
  if (cases.length) {
    kids.push(table(["ID", "Case", "Steps", "Source"], cases.map((c) => [
      el("div", {}, el("code", { text: c.id }), c.priority ? el("small", { text: c.priority }) : null),
      el("div", {}, el("div", { text: c.title }),
         el("small", { text: [c.module, c.category].filter(Boolean).join(" · ") }),
         c.preconditions ? el("small", { text: `Before: ${c.preconditions}` }) : null,
         flags(c)),
      el("ol", {}, (c.steps || []).map((s) => el("li", {},
         el("span", { text: s.action + (s.data ? ` [${s.data}]` : "") }),
         s.expected ? el("small", { text: `→ ${s.expected}` }) : null))),
      el("div", {}, el("span", { text: c.source || "" }),
         c.coverage_note ? el("small", { text: c.coverage_note }) : null),
    ])));
  }
  const updates = r.updates || [];
  kids.push(heading("Existing tests to update", updates.length));
  if (!inv.used) kids.push(el("small", { text: "None can be proposed: no existing tests were read for this job." }));
  if (updates.length) {
    kids.push(table(["Test", "Why", "Change", "Source"], updates.map((u) => [
      el("div", {}, el("code", { text: u.key }), el("small", { text: u.existing_title || "" }),
         u.priority ? el("small", { text: u.priority }) : null),
      el("div", {}, el("span", { text: u.reason }), flags(u)),
      u.changes, u.source || "",
    ])));
  }
  box.replaceChildren(...kids);
}

$("manual-run").onclick = async () => {
  const j = job();
  if (!j) { setState($("manual-state"), "bad", "name the job first"); return; }
  $("manual-run").disabled = true;
  try {
    await post("/api/manual", { job: j, app: $("manual-app").value.trim(),
                                speed: $("manual-speed").value, notes: $("manual-notes").value });
    follow("manual-generate", $("manual-log"), $("manual-state"), () => {
      $("manual-run").disabled = false;
      showManual(j);
      if (j === job()) { refreshXray("manual", true); showReview("manual"); }
    }, j);
  } catch (e) {
    $("manual-run").disabled = false;
    setState($("manual-state"), "bad", e.message);
  }
};
$("manual-stop").onclick = async () => {
  try {
    let stopped = false;
    for (const runnable of ["manual-generate", "manual-inventory"]) {
      const r = await post("/api/stop", { job: job(), runnable });
      if (r && r.stopped) stopped = true;
    }
    if (!stopped) setState($("manual-state"), "", "nothing was running for this job");
  } catch (e) { setState($("manual-state"), "bad", e.message); }
};
onTab.manual = () => { showManualDocs(); showManualPull(); showManual(); refreshXray("manual"); showReview("manual"); };

// ---- publish -----------------------------------------------------------
// The one tab that writes to Jira and Confluence. What is switched on, and
// which of this job's results exist, is ASKED each time the tab is opened
// (publish.py status) -- never read from a file left by an earlier visit:
// the wording of the confirmation ("MOCK" or "writes to Jira now") comes
// from it. The command is also told what the page said (--expect), and
// writes nothing when that is no longer true.
const PAGE_NAMES = { manual: "Manual test cases", design: "API test design",
                     review: "Code review of the proposed change" };
let publishStatus = null;      // the status of publishJob, asked just now
let publishJob = "";

// `earlier`: this is what was found on an earlier visit, shown while it is
// asked again. Nothing can be written from it: the buttons stay off and
// publishStatus stays empty until the fresh answer is in.
function fillPublish(st, earlier) {
  publishStatus = earlier ? null : st;
  const files = (st && st.files) || [];
  const pages = (st && st.pages) || [];
  $("publish-file").replaceChildren(...files.map((f) => el("option", { value: f, text: f })));
  $("publish-what").replaceChildren(...pages.map((p) =>
    el("option", { value: p, text: PAGE_NAMES[p] || p })));
  $("publish-attach").disabled = earlier || !(st && st.attach && files.length);
  $("publish-page").disabled = earlier || !(st && st.create_page && pages.length);
  const box = $("publish-status");
  if (!st) { box.replaceChildren(); return; }
  const kids = [];
  if (earlier) kids.push(banner("warn", `As it was at ${st.at || "an earlier time"} — ` +
    "checking again now. Nothing can be published until that is done."));
  if (st.problem) kids.push(banner("bad", st.problem));
  if (st.mock_jira || st.mock_confluence) {
    kids.push(banner("warn", "MOCK — " +
      [st.mock_jira ? "Jira" : "", st.mock_confluence ? "Confluence" : ""].filter(Boolean).join(" and ") +
      " here is a stand-in: publishing there changes a file on this machine and contacts nobody." +
      (st.mock_jira !== st.mock_confluence ? " The other one is REAL." : "")));
  }
  kids.push(banner(st.attach ? "ok" : "warn", st.attach
    ? "Attaching to Jira is ON" + ((st.projects || []).length ? ` for project(s) ${st.projects.join(", ")}.` : ".")
    : "Attaching to Jira is OFF (jira_config.publish.attach is not true)."));
  kids.push(banner(st.create_page ? "ok" : "warn", st.create_page
    ? `Creating Confluence pages is ON for space(s) ${(st.spaces || []).join(", ") || "— none listed, so none"}.`
    : "Creating Confluence pages is OFF (confluence_config.publish.create_page is not true)."));
  if (!files.length && !pages.length) {
    kids.push(banner("warn", "This job has nothing to publish yet: design the tests, write " +
      "manual tests or review a change first. A step that failed leaves nothing to publish."));
  }
  box.replaceChildren(...kids);
}

async function publishFile(j, name) {
  try {
    const got = await api(`/api/artifact?job=${encodeURIComponent(j)}&name=${name}`);
    return got.text || "";
  } catch { return ""; }
}

async function showPublishResult(j) {
  const box = $("publish-result");
  let r = null;
  try { r = JSON.parse(await publishFile(j, "publish-result.json") || "null"); } catch { r = null; }
  const record = await publishFile(j, "publish.log");
  if (j !== job()) return;
  $("publish-record").textContent = record || "—";
  if (!r) { box.replaceChildren(); return; }
  if (r.kind !== "publish") {
    box.replaceChildren(banner("bad", (r.refused ? "Refused — nothing was written. " : "Failed — ") + (r.message || "")));
    return;
  }
  const kids = [];
  if (r.mock) kids.push(banner("warn", "MOCK. Nothing left this machine."));
  kids.push(banner(r.ok === true ? "ok" : "bad",
    (r.ok === null || r.ok === undefined ? "NOT KNOWN — " : "") + (r.message || "")));
  if (r.link) kids.push(el("div", {}, "It is at ", el("code", { text: r.link })));
  box.replaceChildren(...kids);
}

function publishBusy(on) {
  for (const id of ["publish-attach", "publish-page", "publish-refresh"]) $(id).disabled = on;
}

// Ask what is switched on NOW, for the job in the box, and draw from the
// answer. `afterWrite`: this follows a write whose outcome is on screen --
// the log of that write and the label saying how it ended are left alone.
function refreshPublish(afterWrite) {
  const j = job();
  publishJob = j;
  fillPublish(null);
  if (!afterWrite) {
    $("publish-result").replaceChildren();
    $("publish-record").textContent = "—";
    $("publish-log").textContent = "";
    stepsAbove($("publish-log")).replaceChildren();
  }
  if (!j) { setState($("publish-state"), "bad", "name the job first"); return; }
  publishBusy(true);
  // What is on record is shown at once: the last outcome, the writes made,
  // and what was found last time (marked as such, with the buttons off).
  showPublishResult(j);
  publishFile(j, "publish-status.json").then((text) => {
    let was = null;
    try { was = JSON.parse(text || "null"); } catch { was = null; }
    if (j === job() && !publishStatus && was && was.kind === "publish-status" && was.job === j) {
      fillPublish(was, true);
    }
  });
  const label = afterWrite ? document.createElement("span") : $("publish-state");
  post("/api/start", { job: j, runnable: "publish-status", options: { "--job": j } })
    .then((begun) => follow("publish-status", document.createElement("pre"), label, async (run) => {
      // Whatever happens next, the way back is open.
      $("publish-refresh").disabled = false;
      let st = null;
      try { st = JSON.parse(await publishFile(j, "publish-status.json") || "null"); } catch { st = null; }
      if (j !== job()) return;
      // Only the answer THIS run wrote. The file is never cleared, so one
      // left by an earlier visit is there to be read when this run wrote
      // nothing -- and it must not be taken for the fresh answer.
      const fresh = run && run.state === "done" && st && st.kind === "publish-status" &&
                    st.job === j && String(st.at || "") >= String((begun && begun.started) || "");
      fillPublish(fresh ? st : null);
      if (!fresh) {
        $("publish-status").replaceChildren(banner("bad", "What is switched on could not be " +
          "found out just now (the check did not finish). Nothing can be published until it " +
          "has. The Logs tab has the reason."));
      }
      showPublishResult(j);
    }, j))
    .catch((e) => {
      $("publish-refresh").disabled = false;
      setState($("publish-state"), "bad", e.message);
    });
}
$("publish-refresh").onclick = () => refreshPublish(false);

function runPublish(runnable, options) {
  const j = publishJob;
  publishBusy(true);
  post("/api/start", { job: j, runnable, options: Object.assign({ "--job": j }, options) })
    .then(() => follow(runnable, $("publish-log"), $("publish-state"), () => {
      // The outcome first, then what is now switched on and there.
      showPublishResult(j).then(() => {
        if (j === job()) refreshPublish(true); else $("publish-refresh").disabled = false;
      });
    }, j))
    .catch((e) => {
      $("publish-refresh").disabled = false;
      fillPublish(publishStatus);
      setState($("publish-state"), "bad", e.message);
    });
}

// The job the status was asked for is the job that is written from.
function publishReady() {
  if (publishStatus && publishJob && publishJob === job()) return true;
  setState($("publish-state"), "bad",
           "the job changed since this was checked — checking again; read what it says");
  refreshPublish();
  return false;
}

$("publish-attach").onclick = () => {
  if (!publishReady()) return;
  const key = $("publish-key").value.trim().toUpperCase();
  const file = $("publish-file").value;
  if (!key || !file) { setState($("publish-state"), "bad", "choose the file and give the issue key"); return; }
  const mocked = publishStatus.mock_jira;
  const again = prompt(
    (mocked ? "MOCK: this changes a file on this machine. No Jira is contacted.\n\n"
            : "This WRITES TO JIRA now, and cannot be undone from here.\n\n") +
    `From job ${publishJob}: it attaches ${file} to ${key}, as ${publishJob}-${file}.\n\n` +
    "Type the issue key again to go ahead:");
  if (again === null) return;
  if (!again.trim()) { setState($("publish-state"), "bad", "nothing was typed, so nothing was written"); return; }
  runPublish("publish-attach", { "--key": key, "--file": file, "--confirm-key": again.trim(),
                                 "--expect": mocked ? "mock" : "real" });
};

$("publish-page").onclick = () => {
  if (!publishReady()) return;
  const what = $("publish-what").value;
  const space = $("publish-space").value.trim();
  const parent = $("publish-parent").value.trim();
  const title = $("publish-title").value.trim();
  if (!what || !space || !parent || !title) {
    setState($("publish-state"), "bad", "give the space, the parent page id and the title");
    return;
  }
  const mocked = publishStatus.mock_confluence;
  const again = prompt(
    (mocked ? "MOCK: this changes a file on this machine. No Confluence is contacted.\n\n"
            : "This WRITES TO CONFLUENCE now: other people will see the page.\n\n") +
    `From job ${publishJob}: it creates a new page (${PAGE_NAMES[what] || what}) in ${space} ` +
    `under page ${parent}.\n\nType the title again to go ahead:`);
  if (again === null) return;
  if (!again.trim()) { setState($("publish-state"), "bad", "nothing was typed, so nothing was written"); return; }
  runPublish("publish-page", { "--what": what, "--space": space, "--parent": parent,
                               "--title": title, "--confirm-title": again.trim(),
                               "--expect": mocked ? "mock" : "real" });
};
onTab.publish = () => refreshPublish(false);

// ---- logs --------------------------------------------------------------
// The diagnostics kept by tools/diag.py: this job's record, and the web
// server's. Shown as text. "Only errors" keeps each line that reports one
// and the lines of its traceback.
const LOG_ERROR = /\b(HANDLED|CRASH|ERROR|REFUSED|PAGE ERROR|Traceback|exited [1-9-]|-> [45]\d\d)\b/;
function onlyErrors(text) {
  const out = [];
  let keep = 0;
  for (const line of text.split("\n")) {
    if (LOG_ERROR.test(line)) keep = 40;
    else if (/\b(START|END exit|-> [23]\d\d)\b/.test(line)) keep = 0;
    if (keep > 0) { out.push(line); keep -= 1; }
  }
  return out.join("\n");
}
async function showLogs() {
  const j = job();
  const filter = (text) => ($("logs-errors").checked ? onlyErrors(text) : text) || "—";
  $("logs-job-path").textContent = j ? `.workbench/${j}/diagnostics.log` : "name the job (top right)";
  let mine = "", server = "";
  if (j) {
    try { mine = (await api(`/api/diag?job=${encodeURIComponent(j)}&which=job`)).text || ""; }
    catch (e) { mine = `(${e.message})`; }
  }
  try { server = (await api(`/api/diag?which=server`)).text || ""; }
  catch (e) { server = `(${e.message})`; }
  if (j !== job()) return;
  for (const [id, text] of [["logs-job", mine], ["logs-server", server]]) {
    $(id).textContent = filter(text);
    $(id).scrollTop = $(id).scrollHeight;
  }
  setState($("logs-state"), "", `read at ${new Date().toLocaleTimeString()}`);
}
$("logs-refresh").onclick = showLogs;
$("logs-errors").onchange = showLogs;
onTab.logs = showLogs;

// ---- Settings: the Jira, Confluence and Xray connection ---------------------
// The server says what is set; it never sends a token back. A token box left
// empty keeps the token; the boxes are emptied after every save.
async function showSettings() {
  let s;
  try { s = await api("/api/settings"); }
  catch (e) { setState($("settings-state"), "bad", e.message); return; }
  const box = $("settings-env");
  box.replaceChildren();
  if (s.problem) { box.append(banner("bad", s.problem)); return; }
  box.append(banner("ok", `Environment ${s.env} (chosen by ${s.env_from}); file ${s.file}` +
    (s.exists ? "" : " -- not there yet: Save makes it")));
  $("set-jira-urls").value = (s.jira.base_urls || []).join(", ");
  $("set-conf-urls").value = (s.confluence.base_urls || []).join(", ");
  $("set-xray-url").value = s.xray.base_url || "";
  $("set-xray-project").value = s.xray.project_key || "";
  $("set-xray-regression").value = s.xray.regression_key || "";
  $("set-xray-enabled").checked = Boolean(s.xray.enabled);
  $("set-xray-same").checked = Boolean(s.xray.same_as_jira);
  $("set-jira-pat-state").textContent = s.jira.pat;
  $("set-conf-pat-state").textContent = s.confluence.pat;
  $("set-xray-token-state").textContent = s.xray.same_as_jira ? "the Jira token" : s.xray.token;
  for (const id of ["set-jira-pat", "set-conf-pat", "set-xray-token"]) $(id).value = "";
  for (const id of ["set-jira-pat-clear", "set-conf-pat-clear", "set-xray-token-clear"]) $(id).checked = false;
}
onTab.settings = showSettings;
$("settings-save").onclick = async () => {
  const changes = {
    jira_base_urls: $("set-jira-urls").value,
    confluence_base_urls: $("set-conf-urls").value,
    xray_base_url: $("set-xray-url").value,
    xray_project_key: $("set-xray-project").value,
    xray_regression_key: $("set-xray-regression").value,
    xray_enabled: $("set-xray-enabled").checked,
    xray_same_as_jira: $("set-xray-same").checked,
    jira_pat: $("set-jira-pat").value,
    confluence_pat: $("set-conf-pat").value,
    xray_token: $("set-xray-same").checked ? "" : $("set-xray-token").value,
    jira_pat_clear: $("set-jira-pat-clear").checked,
    confluence_pat_clear: $("set-conf-pat-clear").checked,
    xray_token_clear: $("set-xray-token-clear").checked,
  };
  try {
    const r = await post("/api/settings", { changes });
    setState($("settings-state"), r.saved ? "ok" : "", r.saved ? `saved: ${r.said}` : r.said);
  } catch (e) { setState($("settings-state"), "bad", e.message); }
  // The token boxes are emptied either way: nothing typed stays on the page.
  for (const id of ["set-jira-pat", "set-conf-pat", "set-xray-token"]) $(id).value = "";
  showSettings();
};
$("settings-check").onclick = async () => {
  try {
    const r = await post("/api/settings-check", {});
    setState($("settings-state"), String(r.jira).startsWith("the token is accepted") ? "ok" : "bad", `Jira: ${r.jira}`);
  } catch (e) { setState($("settings-state"), "bad", e.message); }
};

// A fault in this page's own script is otherwise seen by nobody: the
// button "does nothing". It is sent to the server's log, a bounded number
// of times per page load.
let pageErrors = 0;
function reportPageError(message, source, line) {
  pageErrors += 1;
  if (pageErrors > 20) return;
  const open = document.querySelector('.tabs button[aria-selected="true"]');
  fetch("/api/client-error", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: String(message || "").slice(0, 400),
                           source: String(source || "").split("/").pop().slice(0, 120),
                           line: String(line || ""), tab: open ? open.dataset.tab : "" }) })
    .catch(() => { /* the server is not there to be told */ });
}
window.addEventListener("error", (e) => reportPageError(e.message, e.filename, e.lineno));
window.addEventListener("unhandledrejection", (e) =>
  reportPageError(`unhandled: ${e.reason && e.reason.message ? e.reason.message : e.reason}`, "", ""));

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
  $("agent-run").dataset.job = shown;
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
  // A rejection is worded by what happened: a rule broken, or a call
  // that did not finish -- which is no fault of the change.
  if (state === "rejected" && review.why === "timeout")
    detail = "Rejected — the Cursor call did not finish in time. No rule was broken; the files it had written were put back.";
  else if (state === "rejected" && review.why === "call")
    detail = "Rejected — the Cursor call failed.";
  else if (state === "rejected" && review.why === "answered")
    detail = "Cursor answered and changed nothing — its answer is in the command log under Ask Cursor.";
  if (review.mock) detail = "MOCK RUN — written by the stand-in for Cursor; it cannot be " +
                            "approved, only discarded. " + detail;
  if (review.reason) detail += " " + review.reason;
  if (review.branch) detail += ` Branch: ${review.branch}.`;
  // A state is what the LAST run left; said with its time so that an old
  // result is not read as a live one.
  if (review.updated && state !== "none") detail += ` (as of ${review.updated})`;
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
  // An outcome with nothing left in the tree can be put away.
  $("agent-dismiss").disabled = !(["rejected", "discarded", "pushed", "approved-local"].includes(state)
                                  && !review.needs_discard);
  $("agent-dismiss").dataset.job = shown;

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
  $("agent-review").disabled = !(state === "pending-review" || state === "verify-failed");
  // Ask Cursor works in any state but these: it changes a change waiting,
  // starts one when none is, and answers a question. Where it cannot, the
  // button says why instead of only being grey.
  const askBlocked = review.mock && ["pending-review", "verify-failed"].includes(state)
    ? "The change waiting here was made by the stand-in (mock Cursor): Discard it first."
    : state === "generating" ? "A run of this job did not finish: Discard it on the Commit and push tab first."
    : ["pushed", "approved-local"].includes(state) ? "This job's change was already approved: ask in a new job."
    : state === "push-failed" ? "The push failed: push again on the Commit and push tab first." : "";
  $("agent-ask-send").disabled = Boolean(askBlocked);
  $("agent-ask-send").title = askBlocked;
  $("agent-ask-send").dataset.waiting = ["pending-review", "verify-failed"].includes(state) ? "1" : "";
  // Run the tests: for a change of new tests waiting for review. Approve &
  // push: only once a run of it passed.
  const newTests = (review.scope || "new-test") === "new-test";
  const ran = review.execution || null;
  $("agent-run").disabled = !(state === "pending-review" && newTests && !review.mock);
  // Publishing needs a run; it is the last step, after the push.
  $("agent-xray").disabled = !ran;
  if (state === "pending-review" && newTests && !review.mock && (!ran || !ran.ok)) {
    $("agent-push").disabled = true;
    $("agent-push").title = ran ? `the last run on ${ran.env} did not pass: ${ran.why}` : "run the tests first: only a change whose tests all passed is pushed";
  }
  const runBox = $("agent-run-result");
  if (runBox) {
    runBox.replaceChildren();
    if (ran) runBox.append(banner(ran.ok ? "ok" : "bad",
      `Last run on ${ran.env} at ${ran.at}: ${ran.passed} passed, ${ran.failed} failed, ${ran.skipped} skipped` +
      (ran.switched_off ? `, ${ran.switched_off} switched off in the data sheet` : "") + ` -- ${ran.why}`));
  }
  $("agent-review").dataset.job = shown;
  await showCodeReview(shown, review);
}

// code-review.json, written by tools/agent/codereview.py. Every word in
// it that came from Cursor or from the change is put on the page as text.
async function showCodeReview(j, run) {
  const box = $("agent-codereview");
  // Emptied first: whatever goes wrong below, another job's review is not
  // left standing under this job's change.
  box.replaceChildren();
  $("dl-review").hidden = true;
  let r = null;
  let diffNow = "";
  try {
    const got = await api(`/api/artifact?job=${encodeURIComponent(j)}&name=code-review.json`);
    if (got.text) r = JSON.parse(got.text);
    diffNow = got.diff_sha256 || "";
  } catch { /* none yet */ }
  // A review is of one run and one diff. Another run, or the same run's
  // diff written again (Approve does that when files changed), is not it.
  const otherRun = r && (run.started || "") !== (r.run_started || "");
  const otherDiff = r && r.kind === "code-review" && diffNow !== (r.diff_sha256 || "");
  // A refusal says something about the moment it was made, nothing more.
  if (r && r.kind !== "code-review" && otherRun) r = null;
  if (!r) {
    // No result. If a review was started and ended without one, say so:
    // an empty box reads as "nothing found".
    try {
      const st = await api(`/api/status?job=${encodeURIComponent(j)}&runnable=agent-review`);
      if (st.state && st.state !== "none" && st.state !== "running" && st.state !== "done") {
        box.replaceChildren(banner("warn", "The last review did not finish (it was stopped, " +
          "or it failed before it could write a result). Nothing was read."));
      }
    } catch { /* no review was ever started */ }
    return;
  }
  r.findings = r.findings || [];
  r.unconfirmed = r.unconfirmed || [];
  r.files = r.files || [];
  $("dl-review").hidden = r.kind !== "code-review";
  $("dl-review").href = `/api/download?job=${encodeURIComponent(j)}&name=code-review.md`;
  if (r.kind !== "code-review") {
    box.replaceChildren(banner("bad", (r.refused ? "Refused — " : "Failed — ") + (r.message || "")));
    return;
  }
  const kids = [];
  if (r.mock) kids.push(banner("warn", "MOCK. These findings are a keyword match by the " +
    "stand-in for Cursor (tools/agent/mock.json). This is not a review."));
  // Said before anything it found.
  const waitingNow = run.state === "pending-review" || run.state === "verify-failed";
  if (!waitingNow && !otherRun) {
    kids.push(banner("warn", `This review is of a change that is no longer waiting ` +
      `(the job is ${String(run.state || "none").toUpperCase()}).`));
  }
  if (otherRun) {
    kids.push(banner("warn", "This review is of an EARLIER run of this job, not of the " +
      "change shown below. Review the change again."));
  } else if (otherDiff) {
    kids.push(banner("warn", "The change has been WRITTEN AGAIN since this review: the " +
      "diff below is not the one that was read, and the line numbers may not match. " +
      "Review the change again."));
  }
  const unread = (r.files || []).filter((f) => f.status !== "reviewed");
  if (unread.length) {
    kids.push(banner("bad", `NOT ALL OF THE CHANGE WAS REVIEWED — ${unread.length} of ` +
      `${r.files.length} file(s):`));
    kids.push(table(["File", "Read", "Why"], unread.map((f) => [f.file, f.status, f.why])));
  }
  for (const f of r.failures || []) kids.push(banner("bad", `A question to Cursor failed — ${f}`));
  for (const n of r.notes || []) kids.push(banner("warn", `Note — ${n}`));
  kids.push(banner(r.findings.length ? "warn" : "ok", (r.message || "") +
    (r.findings.length ? "" : " No findings is not a sign-off: read the change.")));
  const row = (x) => [
    String(x.severity || "unrated").toUpperCase(),
    el("div", {}, el("code", { text: x.line ? `${x.file}:${x.line}` : x.file }),
       x.where && x.where !== "added"
         ? el("small", { text: x.where === "removed" ? "a removed line" : "an unchanged line" }) : null,
       x.matches > 1
         ? el("small", { text: `the first of ${x.matches} lines that read the same` }) : null),
    el("div", {}, el("div", { text: x.title || "" }),
       el("small", { text: `${x.category || "other"}. ${x.why || ""}` })),
    x.evidence ? el("code", { text: x.evidence }) : "",
    x.fix,
  ];
  const cols = ["Severity", "Where", "What", "The line", "Suggested change"];
  if (r.findings.length) {
    kids.push(heading("Findings", r.findings.length));
    kids.push(table(cols, r.findings.map(row)));
  }
  if ((r.unconfirmed || []).length) {
    kids.push(heading("Unconfirmed", r.unconfirmed.length));
    kids.push(el("small", { text: "The line each of these quotes is not in the change, so it " +
      "may be about something else, or about nothing." }));
    kids.push(table(cols, r.unconfirmed.map(row)));
  }
  if (r.dropped || r.over) {
    kids.push(el("small", { text: `Set aside: ${r.dropped || 0} finding(s) that named a file ` +
      `not in the change or said nothing, ${r.over || 0} beyond the limit for one file.` }));
  }
  box.replaceChildren(...kids);
}

// Cursor's conversations are files of the job, appended to across runs:
// cursor.log for the code it writes (Automate, Ask Cursor, the Migration
// tab), design-cursor.log for the design. Each is read by byte offset on
// its own timer, so it keeps moving while a long call is in flight.
//
// The Automate Test Case tab has ONE Cursor log, showing either file
// ("Show: The design / Writing the tests"); it turns to whichever a run
// started from this page is writing, and on load to whichever ran last.
// The Migration tab's pane always shows cursor.log.
let cursorTimer = null;
let cursorSource = "tests";
const cursorStreams = {
  tests: { runnable: "cursor", offset: 0 },
  design: { runnable: "design-cursor", offset: 0 },
  // The Migration tab's own runs (a converted suite, the converter), in
  // migration-cursor.log: never the Automate tab's conversation.
  migration: { runnable: "migration-cursor", offset: 0 },
};
function panesFor(src) {
  if (src === "migration") return $("mig-cursor-log") ? [$("mig-cursor-log")] : [];
  return src === cursorSource && $("cursor-log") ? [$("cursor-log")] : [];
}
async function pollStream(src, reset) {
  const s = cursorStreams[src];
  if (reset) { s.offset = 0; for (const p of panesFor(src)) p.textContent = ""; }
  try {
    const chunk = await api(
      `/api/log?job=${encodeURIComponent(job())}&runnable=${s.runnable}&offset=${s.offset}`);
    // Gone (Clear the design, Dismiss) or started again: the pane starts again.
    const gone = !chunk.text && !chunk.offset && s.offset > 0;
    if (chunk.restarted || gone) { s.offset = 0; for (const p of panesFor(src)) p.textContent = ""; }
    if (chunk.text) {
      s.offset = chunk.offset;
      for (const p of panesFor(src)) { p.textContent += chunk.text; p.scrollTop = p.scrollHeight; }
    }
  } catch { /* no such log for this job yet */ }
}
async function pollCursorLog(reset) {
  await pollStream("tests", reset);
  await pollStream("design", reset);
  await pollStream("migration", reset);
}
function setCursorSource(src) {
  if (!cursorStreams[src]) return;
  const box = document.querySelector(`input[name="cursor-from"][value="${src}"]`);
  if (box) box.checked = true;
  if (src === cursorSource) return;
  cursorSource = src;
  // The pane shows the other file now: read it from its beginning. (The
  // Migration pane keeps its place: only this stream starts again.)
  $("cursor-log").textContent = "";
  const s = cursorStreams[src];
  s.offset = 0;
  pollStream(src, false);
}
document.querySelectorAll('input[name="cursor-from"]').forEach((r) =>
  r.addEventListener("change", () => { if (r.checked) setCursorSource(r.value); }));
function watchCursorLog() {
  clearInterval(cursorTimer);
  pollCursorLog(true);
  cursorTimer = setInterval(() => pollCursorLog(false), 1500);
}
// On load, and for another job: the conversation of whatever ran last.
async function pickCursorSource() {
  const j = job();
  if (!j) return;
  const when = async (r) => {
    try { return String((await api(`/api/status?job=${encodeURIComponent(j)}&runnable=${r}`)).started || ""); }
    catch { return ""; }
  };
  const [d, ...others] = await Promise.all(["agent-design", "agent-automate", "agent-generate", "agent-followup"].map(when));
  const lastTests = others.sort().pop() || "";
  setCursorSource(d && d > lastTests ? "design" : "tests");
}
pickCursorSource();
$("job").addEventListener("change", pickCursorSource);

// ---- the repository: where the agent's branch goes ----------------------
// Read from git each time the tab is opened; the page remembers nothing
// about it. Switching the working tree is offered only on a clean tree.
let repoInfo = null;
async function showRepo() {
  const where = $("repo-where");
  const sel = $("repo-branches");
  let r = null;
  try { r = await api("/api/repo"); } catch (e) { where.textContent = `the repository could not be read: ${e.message}`; return; }
  repoInfo = r;
  where.replaceChildren();
  if (!r.is_repo) {
    where.append(el("span", { class: "dirty", text: r.note || "not a git work tree" }));
    $("repo-switch").disabled = true;
    return;
  }
  where.append("on ", el("code", { text: r.branch || "(detached)" }),
    ...(r.head ? [el("small", { text: ` ${r.head}` })] : []),
    r.clean ? el("span", { text: ", clean" })
      : el("span", { class: r.switchable ? "" : "dirty",
                     title: [...(r.modified || []), ...(r.untracked || []).map((p) => p + " (untracked)")].join("\n"),
                     text: `, ${(r.modified || []).length} changed` +
                           ((r.blocking || []).length ? ` (${r.blocking.join(", ")})` : "") +
                           `, ${(r.untracked || []).length} untracked` }),
    r.has_remote ? el("span", { title: "Approve & push sends the agent/<job> branch to this remote" },
                      `; pushes to ${r.remote}`)
                 : el("span", { class: "dirty", text: `; no remote "${r.remote}" -- Approve & push has nowhere to push` }),
    // append() prints a null as the text "null": only real parts go in.
    ...(r.upstream && (r.ahead || r.behind) ? [el("small", { text: ` (${r.ahead} ahead, ${r.behind} behind ${r.upstream})` })] : []));
  const names = [...new Set([...(r.local_branches || []), ...(r.remote_branches || [])])].sort();
  sel.replaceChildren(...names.map((n) => el("option", { value: n, text: n + (r.local_branches.includes(n) ? "" : `  (${r.remote} only)`) })));
  sel.value = r.branch;
  // Untracked files and an edited mock.json do not stop a switch; another
  // changed tracked file does, and the strip names it.
  $("repo-switch").disabled = !r.switchable || !names.length;
  $("repo-switch").title = r.switchable ? "" :
    `Changed tracked file(s): ${(r.blocking || []).join(", ")}. Commit, stash or discard them first.`;
  // The base for the job's branch: the remote branches, the current one first.
  const base = $("agent-base");
  const bases = (r.remote_branches || []).slice().sort((a, b) => (a === r.branch ? -1 : b === r.branch ? 1 : a.localeCompare(b)));
  base.replaceChildren(...bases.map((n) => el("option", { value: n, text: `${r.remote}/${n}` + (n === r.branch ? "  (checked out)" : "") })));
  if (!bases.length) base.append(el("option", { value: "", text: `no branch of ${r.remote} is known here (fetch first)` }));
  // Left empty, the branch is feature_<story key> (feature_<job> without one):
  // the server knows the key.
  if ($("agent-branch").dataset.auto !== "0") {
    $("agent-branch").value = "";
    $("agent-branch").placeholder = "feature_<story key>  (feature_<job> when the job has no Jira key)";
    $("agent-branch").dataset.auto = "1";
  }
}
$("agent-branch").addEventListener("input", () => { $("agent-branch").dataset.auto = "0"; });
$("repo-switch").onclick = async () => {
  const branch = $("repo-branches").value;
  if (!branch) return;
  const again = prompt(`Switch the working tree to ${branch}? Every file changes to that branch's ` +
                       "version. Nothing is lost only because the tree is clean.\n\nType the branch name again:");
  if (again === null) return;
  $("repo-switch").disabled = true;
  try {
    const r = await post("/api/repo-checkout", { branch, confirm: again.trim() });
    setState($("repo-state"), "ok", r.message || "switched");
  } catch (e) { setState($("repo-state"), "bad", e.message); }
  showRepo();
};

let agentRunning = "agent-generate";     // what Stop stops

$("agent-ask-send").onclick = async () => {
  const text = $("agent-ask").value.trim();
  if (!text) { setState($("auto-state"), "bad", "write what Cursor should change, or ask it something"); return; }
  const adds = $("agent-ask-send").dataset.waiting === "1";
  if (!confirm(adds
      ? `Cursor will change what it wrote for job "${job()}" as you ask, within the same paths -- or, ` +
        "for a question, answer it. Nothing is committed. Continue?"
      : `No change is waiting for job "${job()}": Cursor starts one as you ask, in tests/jira/ and ` +
        "csv/manual/ -- or, for a question, answers it. Nothing is committed. Continue?")) return;
  agentRunning = "agent-followup";
  try {
    await post("/api/agent-followup", { job: job(), text });
    $("agent-ask").value = "";
    setCursorSource("tests");
    watchCursorLog();
    follow("agent-followup", $("auto-log"), $("auto-state"), () => {
      refreshReview();
      pollCursorLog(false);
    });
  } catch (e) { setState($("auto-state"), "bad", e.message); }
};
// A command of the agent: its log and its label are those of the tab it was
// started from (the Commit and push tab's, unless said).
function runAgent(runnable, options, logEl, stateEl) {
  if (["agent-generate", "agent-review", "agent-followup", "agent-automate"].includes(runnable)) agentRunning = runnable;
  logEl = logEl || $("agent-log");
  stateEl = stateEl || $("agent-state");
  return post("/api/start", { job: job(), runnable, options })
    .then(() => {
      if (["agent-generate", "agent-followup", "agent-automate"].includes(runnable)) {
        setCursorSource("tests");
        watchCursorLog();
      }
    })
    .then(() => follow(runnable, logEl, stateEl, () => {
      refreshReview();
      pollCursorLog(false);
      showReview("design");
      if (typeof showFlow === "function") showFlow();
    }))
    .catch((e) => setState(stateEl, "bad", e.message));
}

// ---- Automate the cases (the Automate Test Case tab) ----------------------
// One row's case, or every approved case marked Automate. The command adds
// to the job's change when one is waiting, and checks the Cursor setup first.
async function automateCases(ids, forJob) {
  const j = job();
  if (!j) { setState($("auto-state"), "bad", "name the job (top right) first"); return; }
  if (forJob && forJob !== j) {
    setState($("auto-state"), "bad", `the table is job "${forJob}", the box says "${j}"; refreshed, check it again`);
    showReview("design");
    return;
  }
  if (!ids.length) {
    setState($("auto-state"), "bad", "approve a case and tick Automate first (Review the designed cases)");
    return;
  }
  const waiting = await artifactJson(j, "review.json");
  const st = (waiting && waiting.state) || "none";
  if (["pending-review", "verify-failed"].includes(st) && waiting.mock) {
    setState($("auto-state"), "bad", "the change waiting was made by the stand-in (mock Cursor): Discard it on the Commit and push tab first");
    return;
  }
  if (["pending-review", "verify-failed"].includes(st) && (waiting.scope || "new-test") !== "new-test") {
    setState($("auto-state"), "bad", `the change waiting for this job is for ${waiting.scope}: Approve or Discard it on the Commit and push tab first`);
    return;
  }
  const adds = ["pending-review", "verify-failed"].includes(st);
  if (!confirm(`Cursor writes the test(s) of ${ids.join(", ")} for job "${j}", in tests/jira/ and ` +
               `csv/manual/${adds ? ", added to the change already waiting for review" : ""}. ` +
               "Nothing is committed. Continue?")) return;
  runAgent("agent-automate", { "--job": j, "--cases": ids.join(",") }, $("auto-log"), $("auto-state"));
}
$("auto-selected").onclick = async () => {
  const j = job();
  const [design, review] = await Promise.all([artifactJson(j, "design.json"), artifactJson(j, "case-review.json")]);
  const entries = (review && review.design) || {};
  const ids = ((design && design.test_cases) || []).map((c) => c && c.id)
    .filter((id) => id && entries[id] && entries[id].state === "approved" && entries[id].automate !== false);
  automateCases(ids);
};

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

// ---- Ask Cursor (the Migration from ReadyAPI tab) -------------------------
// A request in the box becomes the job's brief and plan (or is added to the
// change waiting). An empty box runs on the brief already written -- by Fix
// with Cursor on the Failures tab.
$("mig-run").onclick = async () => {
  const j = job();
  if (!j) { setState($("mig-state"), "bad", "name the job (top right) first"); return; }
  const scope = $("agent-scope").value;
  const suite = $("agent-suite").value.trim();
  const text = $("mig-ask").value.trim();
  if (scope === "converted" && !suite) { setState($("mig-state"), "bad", "name the suite Cursor may change"); return; }
  const what = scope === "converted" ? `the converted files of suite "${suite}"` : "the converter and its runtime";
  if (!text) {
    const plan = await artifactText(j, "plan.md");
    if (!plan.trim()) { setState($("mig-state"), "bad", "write what Cursor should change"); return; }
    if (!confirm(`Cursor will change ${what} for job "${j}", as the job's brief says. Nothing is committed. Continue?`)) return;
    const options = { "--job": j, "--scope": scope };
    if (scope === "converted") options["--suite"] = suite;
    runAgent("agent-generate", options, $("mig-log"), $("mig-state"));
    return;
  }
  if (!confirm(`Cursor will change ${what} for job "${j}" as you ask. Nothing is committed. Continue?`)) return;
  try {
    const r = await post("/api/migration-ask", { job: j, scope, suite, text });
    const runnable = (r && r.runnable) || "agent-generate";
    agentRunning = runnable;
    $("mig-ask").value = "";
    watchCursorLog();
    follow(runnable, $("mig-log"), $("mig-state"), () => {
      refreshReview();
      pollCursorLog(false);
    }, j);
  } catch (e) { setState($("mig-state"), "bad", e.message); }
};
// The converted suites, offered in the Suite box.
api("/api/converted-suites").then((r) => {
  $("converted-suites").replaceChildren(...((r && r.suites) || []).map((s) => el("option", { value: s })));
}).catch(() => { /* none listed: the box still takes a name */ });

// Stop: the one this page started last, then the others. Asked of the
// server, not remembered: after a reload, or with the job box changed
// since, what this page started last is not what is running.
async function stopAgent(stateEl) {
  const others = ["agent-generate", "agent-automate", "agent-review", "agent-followup", "agent-patches",
                  "agent-reapply", "fix-brief"].filter((r) => r !== agentRunning);
  try {
    let stopped = false;
    for (const runnable of [agentRunning, ...others]) {
      const r = await post("/api/stop", { job: job(), runnable });
      if (r && r.stopped) { stopped = true; break; }
    }
    if (!stopped) setState(stateEl, "", "nothing was running for this job");
  } catch (e) { setState(stateEl, "bad", e.message); }
}
$("agent-stop").onclick = () => stopAgent($("auto-state"));
$("mig-stop").onclick = () => stopAgent($("mig-state"));
$("agent-refresh").onclick = () => { refreshReview(); pollCursorLog(true); };
// What runs from this tab: the code review, Discard, Approve. A review
// stops at once. Discard and Approve are asked about first: stopped
// half-way, Discard has put back only some files (run it again), and
// Approve may have made its commit or branch without pushing it.
$("agent-commit-stop").onclick = async () => {
  const j = job();
  if (!j) return;
  const running = [];
  for (const r of ["agent-review", "agent-discard", "agent-approve"]) {
    try {
      const s = await api(`/api/status?job=${encodeURIComponent(j)}&runnable=${r}`);
      if (s && s.state === "running") running.push(r);
    } catch { /* not known: not running */ }
  }
  if (!running.length) { setState($("agent-state"), "", "nothing is running for this job on this tab"); return; }
  const r = running[0];
  if (r === "agent-discard" && !confirm("Stop Discard half-way? Some files will be back as they were and " +
      "others not. Press Discard again afterwards to finish.")) return;
  if (r === "agent-approve" && !confirm("Stop Approve half-way? Its commit or branch may already exist " +
      "without having been pushed; the log says how far it got. Approve again afterwards, or tidy the branch by hand.")) return;
  try {
    const out = await post("/api/stop", { job: j, runnable: r });
    setState($("agent-state"), out && out.stopped ? "warn" : "", out && out.stopped ? `${r} was stopped` : `${r} had already finished`);
  } catch (e) { setState($("agent-state"), "bad", e.message); }
  refreshReview();
};
$("agent-dismiss").onclick = async () => {
  const j = job();
  if (!j || $("agent-dismiss").dataset.job !== j) { refreshReview(); return; }
  $("agent-dismiss").disabled = true;
  try {
    await post("/api/agent-dismiss", { job: j });
    // The logs went with the outcome: the panes start blank.
    $("agent-log").textContent = "";
    setState($("agent-state"), "", "not run yet for this job");
    pollCursorLog(true);
  } catch (e) { setState($("agent-state"), "bad", e.message); }
  refreshReview();
};
// The change's tests on stg. Approve & push opens only when the last run of
// THIS code passed (the server checks the files' hashes too).
$("agent-run").onclick = () => {
  if (!sameJobOrRefresh($("agent-run"))) return;
  if (!confirm(`Run the tests of job "${job()}" against stg? They call the real stg APIs.`)) return;
  runAgent("agent-run-tests", { "--job": job(), "--env": "stg" });
};
// The last run, to Xray: a new Test Execution, and the regression tests to
// the regression Test Set / Plan named on the Settings tab.
$("agent-xray").onclick = async () => {
  const j = job();
  if (!confirm(`Publish the last run of job "${j}" to Xray? It creates a new Test Execution, and adds ` +
               "the regression tests to the regression Test Set or Test Plan (Settings tab).")) return;
  try {
    await post("/api/start", { job: j, runnable: "xray-publish", options: { "--job": j } });
    follow("xray-publish", $("agent-log"), $("agent-state"), async () => {
      const r = await artifactJson(j, "xray-publish.json");
      const box = $("agent-xray-result");
      box.replaceChildren();
      if (r) box.append(banner(r.ok ? "ok" : "bad",
        `Xray: Test Execution ${r.execution || "(none)"}${r.mock ? " (mock: nothing was contacted)" : ""}, ` +
        `${Object.keys(r.published || {}).length} test(s) published` +
        ((r.not_published || []).length ? `, ${r.not_published.length} without an Xray key` : "") +
        (r.regression && r.regression.added && r.regression.added.length
          ? `; ${r.regression.added.length} regression test(s) added to ${r.regression.key}` : "")));
    }, j);
  } catch (e) { setState($("agent-state"), "bad", e.message); }
};
$("agent-review").onclick = () => {
  if (!sameJobOrRefresh($("agent-review"))) return;
  runAgent("agent-review", { "--job": job() });
};
$("agent-patches").onclick = () => {
  const suite = $("agent-suite").value.trim();
  runAgent("agent-patches", suite ? { "--suite": suite } : {}, $("mig-log"), $("mig-state"));
};
$("agent-reapply").onclick = () => {
  const suite = $("agent-suite").value.trim();
  if (!suite) { setState($("mig-state"), "bad", "name the suite"); return; }
  runAgent("agent-reapply", { "--suite": suite }, $("mig-log"), $("mig-state"));
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
  const base = $("agent-base").value;
  const branch = $("agent-branch").value.trim() || "feature_<the story's key>";
  const remote = (repoInfo && repoInfo.remote) || "origin";
  if (pushes && !base) { setState($("agent-state"), "bad", `no branch of ${remote} to build on; fetch first`); return; }
  const msg = pushes
    ? `Approve and push?\n\nThis commits the reviewed files to branch ${branch}, built on ` +
      `${remote}/${base}, and pushes that branch to ${remote}. The repository is public. ` +
      `${base} itself is not touched.`
    : "Approve?\n\nThe converted files stay as Cursor left them, on this " +
      "machine only. Nothing is committed or pushed. The change is " +
      "stored and put back after every convert of the suite.";
  if (!confirm(msg)) return;
  const options = { "--job": j, "--confirm": j };
  if (pushes) { options["--base"] = base; if ($("agent-branch").value.trim()) options["--branch"] = branch; }
  runAgent("agent-approve", options);
};
onTab.agent = () => { showRepo(); };
// The review state drives buttons on three tabs (Ask Cursor to change it,
// the Migration tab's Ask Cursor, Commit and push): read it when any of
// them is opened, and once at load.
for (const tab of ["agent", "new", "convert"]) {
  const b = document.querySelector(`.tabs button[data-tab="${tab}"]`);
  if (b) b.addEventListener("click", () => { refreshReview(); watchCursorLog(); });
}
refreshReview();
watchCursorLog();
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
// A signature's failures, written as the job's brief and plan, then the
// Agentic tab on the scope and suite the brief found. The suite box of
// the Agentic tab, when filled, picks the suite for a signature that
// several suites share; otherwise the brief tells it from the test.
const fixNeedsSuite = new Set();   // signatures refused for spanning suites: the Suite box applies
async function fixWithCursor(sid, scope) {
  const j = job();
  if (!j) { setState($("fail-state"), "bad", "name the job (top right) first"); return; }
  const options = { "--job": j, "--signature": sid, "--scope": scope };
  // The Suite box of the Agentic tab may hold another job's suite. It
  // applies only after this signature was refused for spanning suites.
  const suite = $("agent-suite").value.trim();
  if (suite && fixNeedsSuite.has(sid)) options["--suite"] = suite;   // the Migration tab's Suite box
  busy(failUi.buttons, true);
  try {
    await post("/api/start", { job: j, runnable: "fix-brief", options });
    follow("fix-brief", failUi.log, failUi.state, async () => {
      busy(failUi.buttons, false);
      const r = await artifactJson(j, "fix-brief.json");
      if (!r || !r.ok) {
        const why = (r && r.message) || "the brief could not be written; the log above says why";
        if (/name the suite/i.test(why)) {
          fixNeedsSuite.add(sid);
          $("fail-detail").replaceChildren(banner("warn", why +
            " Type it in the Suite box of the Migration from ReadyAPI tab (Ask Cursor), then click again."));
        } else $("fail-detail").replaceChildren(banner("bad", why));
        return;
      }
      $("fail-detail").replaceChildren(banner("ok", `Brief written for ${r.cases.length} case(s)` +
        (r.suite ? ` of suite ${r.suite}` : "") + `. On the Migration from ReadyAPI tab, press Ask Cursor ` +
        `with the box empty to run on it.`));
      $("agent-scope").value = r.scope;
      $("agent-scope").dispatchEvent(new Event("change"));
      if (r.suite) $("agent-suite").value = r.suite;
      $("mig-ask").value = "";
      goToStep("convert", "mig-run");
    }, j);
  } catch (e) { busy(failUi.buttons, false); setState($("fail-state"), "bad", e.message); }
}

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
  // The failure becomes a job for Cursor: the brief is written from what
  // the run left behind, and the Agentic tab opens on the right scope.
  card.append(el("div", { class: "noterow" },
    el("button", { "data-fail": "", class: "go", text: "Fix with Cursor (this suite's Java)",
                   title: "writes the brief for this signature; then Ask Cursor on the Migration from ReadyAPI tab",
                   on: { click: () => fixWithCursor(s.id, "converted") } }),
    el("button", { "data-fail": "", text: "Fix the converter with Cursor",
                   title: "the same brief, for the emitter: scope The converter itself",
                   on: { click: () => fixWithCursor(s.id, "converter") } })));
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
// ---- Change the workbench (the Logs tab) --------------------------------
// Cursor changing the workbench itself, in a job of its own (workbench-...),
// only while nothing else runs. Its conversation is workbench-cursor.log.
let wbJob = "";
let wbTimer = null;
let wbOffset = 0;
function wbWatch(j) {
  clearInterval(wbTimer);
  wbOffset = 0;
  $("wb-cursor-log").textContent = "";
  if (!j) return;
  const read = async () => {
    try {
      const c = await api(`/api/log?job=${encodeURIComponent(j)}&runnable=workbench-cursor&offset=${wbOffset}`);
      if (c.restarted) $("wb-cursor-log").textContent = "";
      if (c.text) {
        wbOffset = c.offset;
        $("wb-cursor-log").textContent += c.text;
        $("wb-cursor-log").scrollTop = $("wb-cursor-log").scrollHeight;
      }
    } catch { /* no log yet */ }
  };
  read();
  wbTimer = setInterval(read, 1500);
}
async function wbRefresh() {
  try {
    const r = await api("/api/workbench-job");
    wbJob = r.new ? (r.latest || "") : r.job;
    $("wb-job").textContent = wbJob ? `job ${wbJob}` + (r.new ? "" : ` -- ${r.state.replace("-", " ")}`) : "";
    wbWatch(wbJob);
  } catch { /* the server is older than this page */ }
}
$("wb-run").onclick = async () => {
  const text = $("wb-ask").value.trim();
  if (!text) { setState($("wb-state"), "bad", "write what should be different in the workbench"); return; }
  if (!confirm("Cursor will change the workbench itself (tools/agent) as you ask. Nothing else can " +
               "run until it has finished, and the change waits for your review. Continue?")) return;
  $("wb-run").disabled = true;
  try {
    const r = await post("/api/workbench-ask", { text });
    wbJob = r.job;
    $("wb-job").textContent = `job ${wbJob}`;
    $("wb-ask").value = "";
    wbWatch(wbJob);
    follow(r.runnable, $("wb-log"), $("wb-state"), () => { $("wb-run").disabled = false; wbRefresh(); }, wbJob);
  } catch (e) { $("wb-run").disabled = false; setState($("wb-state"), "bad", e.message); }
};
$("wb-stop").onclick = async () => {
  if (!wbJob) { setState($("wb-state"), "", "nothing to stop"); return; }
  try {
    let stopped = false;
    for (const r of ["agent-generate", "agent-followup"]) {
      const out = await post("/api/stop", { job: wbJob, runnable: r });
      if (out && out.stopped) { stopped = true; break; }
    }
    setState($("wb-state"), stopped ? "warn" : "", stopped ? "stopped -- Discard it on the Commit and push tab" : "nothing was running");
  } catch (e) { setState($("wb-state"), "bad", e.message); }
};
$("wb-review").onclick = () => {
  if (!wbJob) { setState($("wb-state"), "", "no change to the workbench yet"); return; }
  $("job").value = wbJob;
  $("job").dispatchEvent(new Event("change"));
  goToStep("agent", "agent-push");
};
document.querySelector('.tabs button[data-tab="logs"]').addEventListener("click", wbRefresh);

// The jobs kept on disk, newest first, offered in the job box.
api("/api/jobs").then((list) => {
  const box = $("known-jobs");
  if (!box || !Array.isArray(list)) return;
  const rows = list.slice().sort((a, b) => String(b.modified).localeCompare(String(a.modified))).slice(0, 50);
  box.replaceChildren(...rows.map((r) => el("option", { value: r.job, text: `${r.job}  (${r.modified})` })));
}).catch(() => { /* no list: the box still takes a typed id */ });
(function openTabFromAddress() {
  const name = (location.hash || "").replace(/^#/, "");
  const b = name && document.querySelector(`.tabs button[data-tab="${name.replace(/[^a-z]/g, "")}"]`);
  if (b) b.click();
})();
$("job").addEventListener("change", () => {
  const open = document.querySelector('.tabs button[aria-selected="true"]');
  if (open && onTab[open.dataset.tab]) onTab[open.dataset.tab]();
});
