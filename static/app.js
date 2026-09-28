const idInput = document.getElementById("id-input");
const loadBtn = document.getElementById("load-btn");
const container = document.getElementById("transcript");
const statusEl = document.getElementById("status");
const statsEl = document.getElementById("stats-bar");
const jumpBtn = document.getElementById("jump-bottom");
const toggleBtns = [...document.querySelectorAll(".toggle-btn")];

let currentId = null;
let es = null;

// Each category's current bulk open/closed state, applied to any block that doesn't
// already have its own remembered per-block state (see saveOpenStates/restoreOpenStates).
const CATEGORIES = {
  output: { selector: "details.kind-output", open: true },
  reasoning: { selector: "details.thinking-card", open: false },
  user: { selector: "details.kind-user", open: true },
  tools: { selector: "details.tool-card", open: false },
};

function updateToggleButtonStyles() {
  toggleBtns.forEach((btn) => {
    const cat = CATEGORIES[btn.dataset.cat];
    btn.classList.toggle("active", cat.open);
  });
}

function applyCategoryStates(onlyNew, previousStates) {
  Object.values(CATEGORIES).forEach(({ selector, open }) => {
    container.querySelectorAll(selector).forEach((d) => {
      if (onlyNew && previousStates && d.dataset.key in previousStates) return;
      d.open = open;
    });
  });
}

toggleBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    const cat = CATEGORIES[btn.dataset.cat];
    cat.open = !cat.open;
    container.querySelectorAll(cat.selector).forEach((d) => (d.open = cat.open));
    updateToggleButtonStyles();
  });
});
updateToggleButtonStyles();

function saveOpenStates() {
  const states = {};
  container.querySelectorAll("details[data-key]").forEach((d) => {
    states[d.dataset.key] = d.open;
  });
  return states;
}

function restoreOpenStates(states) {
  container.querySelectorAll("details[data-key]").forEach((d) => {
    if (d.dataset.key in states) d.open = states[d.dataset.key];
  });
}

function isNearBottom() {
  return window.innerHeight + window.scrollY >= document.body.offsetHeight - 80;
}

function scrollToBottom(smooth) {
  window.scrollTo({ top: document.body.scrollHeight, behavior: smooth ? "smooth" : "auto" });
}

function updateJumpButton() {
  jumpBtn.hidden = !currentId || isNearBottom();
}

async function refresh() {
  if (!currentId) return;
  const states = saveOpenStates();
  const wasNearBottom = isNearBottom();

  const res = await fetch(`/render/${encodeURIComponent(currentId)}`);
  container.innerHTML = await res.text();

  restoreOpenStates(states);
  applyCategoryStates(true, states); // newly-appeared blocks follow the current bulk setting

  if (wasNearBottom) scrollToBottom(false);
  updateJumpButton();
  refreshStats();
}

function statChip(value, label, opts = {}) {
  const cls = opts.big ? "stat stat-big" : "stat";
  const title = opts.title ? ` title="${opts.title}"` : "";
  return (
    `<div class="${cls}"${title}>` +
    `<span class="stat-value">${value}</span>` +
    `<span class="stat-label">${label}</span>` +
    `</div>`
  );
}

async function refreshStats() {
  if (!currentId) return;
  const res = await fetch(`/api/stats/${encodeURIComponent(currentId)}`);
  if (!res.ok) return;
  const s = await res.json();
  const c = s.cumulative_tokens;
  const fmt = (n) => n.toLocaleString();

  const chips = [
    statChip(fmt(s.tool_calls), "tool calls"),
    statChip(`${fmt(s.assistant_messages)} / ${fmt(s.user_messages)}`, "assistant / user turns"),
  ];

  if (s.has_usage_data) {
    const cumulativeTitle =
      `Cumulative across all turns (reference only — double-counts context, ` +
      `since every call resends the whole conversation; cache misses inflate this further): ` +
      `input ${fmt(c.input)} + cache-write ${fmt(c.cache_creation)} + cache-read ${fmt(c.cache_read)} + output ${fmt(c.output)}`;
    chips.push(
      statChip(`${fmt(s.unique_input_tokens)} in / ${fmt(s.unique_output_tokens)} out`, "tokens", {
        title: cumulativeTitle,
      })
    );
    chips.push(statChip(fmt(s.total_tokens), "total tokens", { big: true, title: cumulativeTitle }));
  } else {
    chips.push(statChip("—", "no token usage recorded"));
  }

  statsEl.innerHTML = chips.join("");
}

function connect(id) {
  if (es) es.close();
  currentId = id;
  statusEl.textContent = "Connecting…";

  es = new EventSource(`/stream/${encodeURIComponent(id)}`);
  es.addEventListener("update", () => {
    statusEl.textContent = "Live";
    refresh();
  });
  es.onerror = () => {
    statusEl.textContent = "Reconnecting…";
  };
}

async function load() {
  const pasted = idInput.value.trim();
  if (!pasted) return;

  const res = await fetch(`/api/find?id=${encodeURIComponent(pasted)}`);
  if (!res.ok) {
    statusEl.textContent = "Not found";
    statsEl.textContent = "";
    container.innerHTML = "<div class='empty'>No transcript matched that id.</div>";
    return;
  }
  const data = await res.json();
  const id = data.id || pasted;
  idInput.value = id; // a pasted full path resolves down to just its id
  statusEl.textContent = data.path;

  connect(id);
  await refresh();
  scrollToBottom(false);
  updateJumpButton();

  const url = new URL(location.href);
  url.searchParams.set("id", id);
  history.replaceState(null, "", url);
}

loadBtn.addEventListener("click", load);
idInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") load();
});

jumpBtn.addEventListener("click", () => scrollToBottom(true));
window.addEventListener("scroll", updateJumpButton);
window.addEventListener("resize", updateJumpButton);

const preset = new URLSearchParams(location.search).get("id");
if (preset) {
  idInput.value = preset;
  load();
}
