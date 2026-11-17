// Voxel site. The Anthropic key lives only in the password box until Build is
// pressed, goes to this server in one POST body, and is then cleared. It is never
// put in localStorage, sessionStorage, a URL or the console.
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const money = (n) => (typeof n === "number" ? "$" + n.toFixed(2) : "not recorded");
const effortWord = (e) => (e ? e.charAt(0).toUpperCase() + e.slice(1) : "");
// "Opus, medium effort, up to $2.50, questions off": what one build was set to.
function settingsLine(s) {
  if (!s) return "";
  const parts = [];
  if (s.model) parts.push(s.model);
  if (s.effort) parts.push(s.effort + " effort");
  if (typeof s.max_cost_usd === "number") parts.push("up to " + money(s.max_cost_usd));
  if (typeof s.questions === "boolean") parts.push(s.questions ? "questions on" : "questions off");
  return parts.join(", ");
}

async function getJSON(url, options) {
  const response = await fetch(url, options);
  let data = null;
  try { data = await response.json(); } catch (_) { /* not JSON */ }
  if (!response.ok) throw new Error((data && data.error) || "The server did not answer. Is it still running?");
  return data;
}

function thumb(src, cls) {
  if (!src) return el("span", "thumb none " + (cls || ""));
  const img = el("img", "thumb " + (cls || ""));
  img.src = src; img.alt = ""; img.loading = "lazy";
  img.onerror = () => img.replaceWith(el("span", "thumb none " + (cls || "")));
  return img;
}

// ---------------------------------------------------------------- Build page

function buildPage() {
  const SETS_KEY = "voxel.sets";   // set numbers and names only
  const JOB_KEY = "voxel.job";     // the running build's id, so a reload keeps watching it
  const SETTINGS_KEY = "voxel.settings";  // model, effort, max cost, questions: never the key
  const WAIT_MS = 350;             // pause after the last keystroke before searching
  const POLL_MS = 3000;

  let sets = [];
  try { sets = JSON.parse(localStorage.getItem(SETS_KEY) || "[]"); } catch (_) { sets = []; }
  if (!Array.isArray(sets)) sets = [];
  let lastResults = [];
  let timer = null, inFlight = null, pollTimer = null;

  const saveSets = () => { try { localStorage.setItem(SETS_KEY, JSON.stringify(sets)); } catch (_) {} };

  let limits = { min_cost_usd: 0.25, max_cost_usd: 20 };

  function readSettings() {
    const effort = document.querySelector('input[name="effort"]:checked');
    return {
      model: $("model").value,
      effort: effort ? effort.value : "medium",
      max_cost_usd: Number($("max-cost").value),
      questions: $("questions").checked,
    };
  }

  function writeSettings(s) {
    if (!s) return;
    if (s.model && [...$("model").options].some((o) => o.value === s.model)) $("model").value = s.model;
    const radio = document.querySelector('input[name="effort"][value="' + s.effort + '"]');
    if (radio) radio.checked = true;
    if (typeof s.max_cost_usd === "number") $("max-cost").value = s.max_cost_usd.toFixed(2);
    if (typeof s.questions === "boolean") $("questions").checked = s.questions;
    drawSwitch();
  }

  function drawSwitch() { $("questions-state").textContent = $("questions").checked ? "On" : "Off"; }

  function saveSettings() { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(readSettings())); } catch (_) {} }

  function drawLimit() {
    const cost = Number($("max-cost").value);
    $("limit").textContent = Number.isFinite(cost) && cost > 0
      ? "This build stops at " + money(cost) + " of model use."
      : "";
  }

  for (const id of ["model", "max-cost", "questions"]) {
    $(id).addEventListener("change", () => { drawSwitch(); drawLimit(); saveSettings(); });
  }
  $("max-cost").addEventListener("input", drawLimit);
  for (const radio of document.querySelectorAll('input[name="effort"]')) radio.addEventListener("change", saveSettings);
  const owned = (num) => sets.some((s) => s.set_num === num);

  function drawSets() {
    const list = $("mysets");
    list.replaceChildren();
    $("sets-empty").hidden = sets.length > 0;
    for (const s of sets) {
      const li = el("li");
      const text = el("div");
      text.append(el("div", "setname", s.name), el("div", "setnum", s.set_num));
      const remove = el("button", "remove", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove " + s.name);
      remove.onclick = () => { sets = sets.filter((x) => x.set_num !== s.set_num); saveSets(); drawSets(); drawResults(); };
      li.append(thumb(s.image), text, remove);
      list.append(li);
    }
  }

  function drawResults() {
    const list = $("results");
    list.replaceChildren();
    list.hidden = lastResults.length === 0;
    for (const r of lastResults) {
      const button = el("button");
      button.type = "button";
      const text = el("div");
      const facts = [r.set_num, r.year, r.num_parts ? r.num_parts + " pieces" : ""].filter(Boolean).join(", ");
      text.append(el("div", "setname", r.name), el("div", "setnum", facts));
      const has = owned(r.set_num);
      button.disabled = has;
      button.append(thumb(r.image), text, el("span", "add", has ? "In My sets" : "Add"));
      button.onclick = () => {
        if (owned(r.set_num)) return;
        sets.push({ set_num: r.set_num, name: r.name, image: r.image });
        saveSets(); drawSets(); drawResults();
      };
      const li = el("li"); li.append(button); list.append(li);
    }
  }

  function searchStatus(text, bad) {
    const node = $("search-status");
    node.textContent = text; node.classList.toggle("bad", !!bad);
  }

  async function search(query) {
    if (inFlight) inFlight.abort();
    inFlight = new AbortController();
    searchStatus("Searching.");
    try {
      const data = await getJSON("/api/sets?q=" + encodeURIComponent(query), { signal: inFlight.signal });
      if (data.superseded) return;
      lastResults = data.results || [];
      searchStatus(lastResults.length ? "" : "No set matches that. Try the number on the box.");
      drawResults();
    } catch (err) {
      if (err.name === "AbortError") return;
      lastResults = []; drawResults();
      searchStatus(err.message, true);
    }
  }

  $("search").addEventListener("input", (event) => {
    const query = event.target.value.trim();
    clearTimeout(timer);
    if (query.length < 2) {
      if (inFlight) inFlight.abort();
      lastResults = []; drawResults(); searchStatus("");
      return;
    }
    timer = setTimeout(() => search(query), WAIT_MS);
  });

  // ---- building

  function setBuilding(on) {
    $("build").disabled = on;
    $("build-label").textContent = on ? "Building" : "Build";
  }

  function clock(seconds) {
    return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }

  function showBuilding(job) {
    const box = $("result");
    box.hidden = false;
    const title = el("h2", "", "Building");
    const line = el("p", "building-line");
    line.append("Designing “" + job.request + "”. This takes a few minutes. ", el("span", "", clock(job.seconds || 0)));
    box.replaceChildren(title, line, el("p", "note", settingsLine(job.settings)),
      el("p", "note", "You can reload this page. The build keeps running on your computer."));
  }

  let shownQuestions = "";  // the job whose questions are on screen, so polling does not redraw them

  function showQuestions(job) {
    if (shownQuestions === job.id) return;
    shownQuestions = job.id;
    const box = $("result");
    box.hidden = false;
    const chosen = {};
    const list = el("div", "questions");
    for (const q of job.questions || []) {
      const block = el("div", "question");
      block.append(el("p", "", q.text));
      if (q.options && q.options.length) {
        const row = el("div", "options");
        for (const option of q.options) {
          const chip = el("button", "chip", option + (option === q.default ? " (default)" : ""));
          chip.type = "button";
          chip.setAttribute("aria-pressed", String(option === q.default));
          chip.onclick = () => {
            chosen[q.id] = option;
            for (const other of row.querySelectorAll(".chip")) other.setAttribute("aria-pressed", String(other === chip));
          };
          row.append(chip);
        }
        block.append(row);
      } else {
        const input = el("input");
        input.type = "text"; input.maxLength = 100; input.placeholder = q.default || "";
        input.setAttribute("aria-label", q.text);
        input.oninput = () => { chosen[q.id] = input.value.trim(); };
        block.append(input);
      }
      list.append(block);
    }
    const send = el("button", "plain-button", "Send answers");
    send.type = "button";
    const skip = el("button", "plain-button quiet", "Use the defaults");
    skip.type = "button";
    const status = el("p", "status");
    const submit = async (answers) => {
      send.disabled = skip.disabled = true;
      try {
        await getJSON("/api/build/" + encodeURIComponent(job.id) + "/answers", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answers }),
        });
        shownQuestions = "";
        showBuilding({ ...job, state: "building" });
      } catch (err) {
        send.disabled = skip.disabled = false;
        status.textContent = err.message; status.classList.add("bad");
      }
    };
    send.onclick = () => submit(chosen);
    skip.onclick = () => submit({});
    const row = el("div", "answer-row");
    row.append(send, skip);
    box.replaceChildren(
      el("h2", "", "A few questions first"),
      el("p", "note", "Your answers shape \u201c" + job.request + "\u201d. Anything you skip uses the default."),
      list, row, status);
  }

  function showFailed(job) {
    const box = $("result");
    box.hidden = false;
    const nodes = [el("h2", "", "The build did not finish"), el("p", "problem", job.error || "Something went wrong.")];
    const cost = job.result && job.result.cost_usd;
    if (typeof cost === "number" && cost > 0) nodes.push(el("p", "note", "Cost of this attempt: " + money(cost)));
    if (job.settings) nodes.push(el("p", "note", "Settings: " + settingsLine(job.settings)));
    box.replaceChildren(...nodes);
  }

  function showDone(job) {
    const r = job.result || {};
    const box = $("result");
    box.hidden = false;
    const cols = el("div", "cols");

    const left = el("div");
    if (r.picture) {
      const img = el("img", "build");
      img.src = r.picture; img.alt = "The finished model: " + job.request;
      left.append(img);
    } else {
      left.append(el("p", "note", "No picture was rendered for this build."));
    }

    const right = el("div");
    const facts = el("dl", "facts");
    const fact = (name, value) => { facts.append(el("dt", "", name), el("dd", "", value)); };
    const chosen = job.settings || {};
    fact("Cost", money(r.cost_usd) + (typeof chosen.max_cost_usd === "number" ? " of " + money(chosen.max_cost_usd) : ""));
    if (chosen.model) fact("Model", chosen.model);
    if (chosen.effort) fact("Effort", effortWord(chosen.effort));
    if (typeof chosen.questions === "boolean") fact("Questions", chosen.questions ? "Asked" : "Not asked");
    if (r.parts) fact("Parts", String(r.parts));
    if (r.pages) fact("Manual pages", String(r.pages));
    right.append(facts);

    const review = r.review || {};
    if (review.items) {
      const total = review.items.length;
      right.append(el("p", "verdict", (review.passed ? "Review passed" : "Review failed") + ": " + review.passed_count + " of " + total));
      const list = el("ul", "checks");
      for (const item of review.items) {
        const li = el("li");
        const text = el("div");
        text.append(el("div", "", item.item), el("div", "why", item.reason));
        li.append(el("span", "tag " + (item.passed ? "yes" : "no"), item.passed ? "Passed" : "Failed"), text);
        list.append(li);
      }
      right.append(list);
    } else {
      right.append(el("p", "verdict", "Not reviewed"), el("p", "note", review.skipped || ""));
    }

    if (r.manual) {
      const link = el("a", "download", "Download manual (.ldr)");
      link.href = r.manual; link.setAttribute("download", "");
      right.append(link);
    } else {
      right.append(el("p", "note", "No manual could be written for this build."));
    }

    cols.append(left, right);
    box.replaceChildren(el("h2", "", job.request), cols);
  }

  async function poll(id) {
    clearTimeout(pollTimer);
    let job;
    try {
      job = await getJSON("/api/build/" + encodeURIComponent(id));
    } catch (err) {
      sessionStorage.removeItem(JOB_KEY);
      setBuilding(false);
      if ($("result").hidden === false) showFailed({ error: err.message });
      return;
    }
    if (job.state === "asking") {
      setBuilding(true); showQuestions(job);
      pollTimer = setTimeout(() => poll(id), POLL_MS);
      return;
    }
    if (job.state === "building") {
      setBuilding(true);
      if (shownQuestions !== job.id) showBuilding(job);
      pollTimer = setTimeout(() => poll(id), POLL_MS);
      return;
    }
    sessionStorage.removeItem(JOB_KEY);
    setBuilding(false);
    if (job.state === "done") showDone(job); else showFailed(job);
  }

  $("build-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const status = $("form-status");
    status.textContent = ""; status.classList.remove("bad");
    const keyBox = $("key");
    const request = $("request").value.trim();
    const settings = readSettings();
    const cost = settings.max_cost_usd;
    const problem =
      !keyBox.value.trim() ? "Paste your Anthropic key first." :
      !sets.length ? "Add at least one set to My sets." :
      !request ? "Say what to build." :
      !(Number.isFinite(cost) && cost >= limits.min_cost_usd && cost <= limits.max_cost_usd)
        ? "Set the max cost between " + money(limits.min_cost_usd) + " and " + money(limits.max_cost_usd) + "." : "";
    if (problem) { status.textContent = problem; status.classList.add("bad"); return; }

    setBuilding(true);
    settings.max_cost_usd = Math.round(cost * 100) / 100;
    const body = JSON.stringify({ key: keyBox.value.trim(), request, sets: sets.map((s) => s.set_num), settings });
    keyBox.value = "";  // the page keeps no copy
    try {
      const job = await getJSON("/api/build", { method: "POST", headers: { "Content-Type": "application/json" }, body });
      sessionStorage.setItem(JOB_KEY, job.id);
      showBuilding(job);
      $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
      pollTimer = setTimeout(() => poll(job.id), POLL_MS);
    } catch (err) {
      setBuilding(false);
      status.textContent = err.message; status.classList.add("bad");
    }
  });

  drawSets();
  let remembered = null;
  try { remembered = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null"); } catch (_) { remembered = null; }
  getJSON("/api/config").then((c) => {
    limits = c;
    $("max-cost").min = c.min_cost_usd; $("max-cost").max = c.max_cost_usd;
    writeSettings(remembered || c.defaults);
    drawLimit();
  }).catch(() => { writeSettings(remembered); drawLimit(); });
  drawSwitch(); drawLimit();
  const running = sessionStorage.getItem(JOB_KEY);
  if (running) poll(running);
}

// ---------------------------------------------------------------- Guide page

function guidePage() {
  const chosen = { model: "All", style: "All" };
  let builds = [];

  function chips(box, name, values) {
    for (const value of ["All", ...values]) {
      const chip = el("button", "chip", value === "one-line" ? "One-line" : value === "detailed" ? "Detailed" : value);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(chosen[name] === value));
      chip.onclick = () => {
        chosen[name] = value;
        for (const other of box.querySelectorAll(".chip")) other.setAttribute("aria-pressed", String(other === chip));
        draw();
      };
      box.append(chip);
    }
  }

  function card(b) {
    const li = el("li", "card");
    const img = el("img");
    img.src = b.picture; img.loading = "lazy";
    img.alt = b.request ? "Model built for: " + b.request : "A built model";
    const inner = el("div", "in");
    inner.append(el("p", "asked", b.request ? "“" + b.request + "”" : "Request not recorded"));
    const tags = el("div", "tags");
    if (b.model) tags.append(el("span", "tagbox " + b.model.toLowerCase(), b.model));
    if (b.style) tags.append(el("span", "tagbox", b.style === "one-line" ? "One-line request" : "Detailed request"));
    if (tags.children.length) inner.append(tags);
    const extra = [];
    if (b.effort) extra.push(effortWord(b.effort) + " effort");
    if (typeof b.max_cost_usd === "number") extra.push("limit " + money(b.max_cost_usd));
    if (typeof b.questions === "boolean") extra.push(b.questions ? "questions asked" : "no questions");
    if (extra.length) inner.append(el("p", "settings-line", extra.join(", ")));
    const facts = el("dl", "facts");
    const fact = (name, value) => { facts.append(el("dt", "", name), el("dd", "", value)); };
    fact("Cost", money(b.cost_usd));
    fact("Turns", b.turns ? String(b.turns) : "not recorded");
    fact("Review", b.review ? b.review.passed_count + " of " + b.review.total + (b.review.passed ? ", passed" : ", failed") : "not reviewed");
    inner.append(facts);
    li.append(img, inner);
    return li;
  }

  function draw() {
    const shown = builds.filter((b) =>
      (chosen.model === "All" || b.model === chosen.model) &&
      (chosen.style === "All" || b.style === chosen.style));
    $("cards").replaceChildren(...shown.map(card));
    $("gallery-status").textContent = shown.length
      ? shown.length + (shown.length === 1 ? " build" : " builds")
      : "No builds match these filters.";
  }

  getJSON("/api/gallery").then((data) => {
    builds = data.builds || [];
    if (!builds.length) {
      $("gallery-status").textContent = "No builds yet. Make one on the Build page and it will show up here.";
      return;
    }
    const models = [...new Set(builds.map((b) => b.model).filter(Boolean))].sort();
    const styles = ["one-line", "detailed"].filter((s) => builds.some((b) => b.style === s));
    chips($("filter-model"), "model", models);
    chips($("filter-style"), "style", styles);
    $("filters").hidden = false;
    draw();
  }).catch((err) => {
    const status = $("gallery-status");
    status.textContent = err.message; status.classList.add("bad");
  });
}

if ($("build-form")) buildPage();
if ($("cards")) guidePage();
