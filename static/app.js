/* multillm web ui - renders the SSE event stream into agent panels + channel feed.
   Event format mirrors agents.py's emit(): [kind, agent, data[]]. */

"use strict";

const PALETTE = ["#22d3ee", "#e879f9", "#fbbf24", "#60a5fa"]; // matches core.COLORS order
const HUMAN_COLOR = "#34d399";
const MAX_PANEL_CHARS = 16000;
const MAX_FEED_ITEMS = 500;

const $ = (sel) => document.querySelector(sel);
const panelsEl = $("#panels");
const feedEl = $("#feed");
const chipsEl = $("#chips");
const msgEl = $("#msg");
const sendBtn = $("#sendBtn");
const resetBtn = $("#resetBtn");
const toastEl = $("#toast");
const readoutEl = $("#readout");
const livedotEl = $("#livedot");

const S = {
  agents: [],
  model: null,
  ctxSlot: 0,
  selected: "agent1",
  connected: false,
  pan: new Map(),      // name -> {body, block, kind, caret, state, ctx}
  stickFeed: true,
  stickPanel: new Map(),
};

/* all content goes through textContent, never innerHTML */

/* ---------- panels & chips ---------- */

function accentFor(i) { return PALETTE[i % PALETTE.length]; }

function buildPanels(names) {
  panelsEl.innerHTML = "";
  chipsEl.innerHTML = "";
  S.pan.clear();
  names.forEach((n, i) => {
    const ac = accentFor(i);

    const panel = document.createElement("article");
    panel.className = "panel";
    panel.style.setProperty("--ac", ac);
    panel.dataset.agent = n;

    const head = document.createElement("div");
    head.className = "panel-head";
    const dot = el("span", "pdot");
    const name = el("span", "pname", n);
    const state = el("span", "pstate", "idle");
    const ctx = el("span", "pctx", "");
    head.append(dot, name, state, ctx);

    const body = el("div", "stream empty");
    body.setAttribute("role", "log");
    body.setAttribute("aria-live", "polite");
    body.addEventListener("scroll", () => {
      S.stickPanel.set(n, body.scrollHeight - body.scrollTop - body.clientHeight < 48);
    });
    if (S.stickPanel.get(n) === undefined) S.stickPanel.set(n, true);

    AGENT_COLORS[n] = ac;

    panel.append(head, body);
    panelsEl.appendChild(panel);

    S.pan.set(n, { body, block: null, kind: null, caret: null, state: "idle", ctx: 0, dot, stateEl: state, ctxEl: ctx });

    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = n;
    chip.style.setProperty("--ac", ac);
    chip.setAttribute("role", "tab");
    chip.setAttribute("aria-selected", String(n === S.selected));
    chip.addEventListener("click", () => selectAgent(n));
    chipsEl.appendChild(chip);
  });
  if (!S.agents.includes(S.selected)) S.selected = names[0];
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function selectAgent(n) {
  S.selected = n;
  for (const c of chipsEl.children) {
    c.setAttribute("aria-selected", String(c.textContent === n));
  }
  msgEl.placeholder = `Message ${n}…`;
  msgEl.focus();
}

function wipe() {
  for (const p of S.pan.values()) {
    p.body.innerHTML = "";
    p.body.classList.add("empty");
    p.block = null; p.kind = null; p.caret = null;
    setState(p, "idle");
    p.ctx = 0;
    p.ctxEl.textContent = "";
  }
  feedEl.innerHTML = "";
  const empty = el("li", "empty", "No messages yet. Send one below and an agent will pick it up.");
  feedEl.appendChild(empty);
  S.stickFeed = true;
}

/* ---------- stream rendering ---------- */

function trimBody(body) {
  let total = body.textContent.length;
  while (total > MAX_PANEL_CHARS && body.children.length > 1) {
    total -= body.firstElementChild.textContent.length;
    body.firstElementChild.remove();
  }
}

function closeBlock(p) {
  if (p.caret && p.caret.parentNode) p.caret.remove();
  p.caret = null;
  p.block = null;
  p.kind = null;
}

function ensureCaret(p) {
  if (!p.caret) p.caret = el("span", "caret");
  if (p.caret.parentNode !== p.block) p.block.appendChild(p.caret);
}

function scrollIfStuck(name, body) {
  if (S.stickPanel.get(name)) body.scrollTop = body.scrollHeight;
}

function appendProse(name, kind, chunk) {
  const p = S.pan.get(name);
  if (!p) return;
  if (p.kind !== kind) closeBlock(p);
  if (!p.block) {
    p.block = el("div", "blk " + (kind === "think" ? "think" : ""));
    p.body.appendChild(p.block);
    p.kind = kind;
    p.body.classList.remove("empty");
  }
  ensureCaret(p);
  p.block.insertBefore(document.createTextNode(chunk), p.caret);
  trimBody(p.body);
  scrollIfStuck(name, p.body);
}

function appendLine(name, text, cls) {
  const p = S.pan.get(name);
  if (!p) return;
  closeBlock(p);
  const line = el("div", "line " + cls, text);
  p.body.appendChild(line);
  p.body.classList.remove("empty");
  trimBody(p.body);
  scrollIfStuck(name, p.body);
}

function appendOut(name, text) {
  const p = S.pan.get(name);
  if (!p) return;
  closeBlock(p);
  const out = el("div", "out", text);
  p.body.appendChild(out);
  p.body.classList.remove("empty");
  trimBody(p.body);
  scrollIfStuck(name, p.body);
}

function setState(p, state) {
  p.state = state;
  const working = state !== "idle" && state !== "error";
  p.dot.className = "pdot" + (working ? " working" : state === "error" ? " error" : "");
  p.stateEl.className = "pstate" + (working ? " working" : state === "error" ? " error" : "");
  p.stateEl.textContent = state;
}

/* ---------- channel feed ---------- */

feedEl.addEventListener("scroll", () => {
  S.stickFeed = feedEl.scrollHeight - feedEl.scrollTop - feedEl.clientHeight < 48;
});

function pushItem(sender, to, text) {
  const empty = feedEl.querySelector(".empty");
  if (empty) empty.remove();
  const li = document.createElement("li");
  li.className = "item" + (to === "human" ? " human-in" : "");

  const ts = el("span", "ts", new Date().toLocaleTimeString([], { hour12: false }));
  const from = el("span", "who", sender);
  from.style.color = senderColor(sender);
  const arr = el("span", "arr", "→");
  const toEl = el("span", "who", to);
  if (to === "human") toEl.classList.add("human");
  const body = el("span", "body", text);

  li.append(ts, from, arr, toEl, body);
  feedEl.appendChild(li);
  while (feedEl.children.length > MAX_FEED_ITEMS) feedEl.firstElementChild.remove();
  if (S.stickFeed) feedEl.scrollTop = feedEl.scrollHeight;
}

const AGENT_COLORS = {}; // name -> accent, filled in buildPanels

function senderColor(who) {
  if (who === "human") return HUMAN_COLOR;
  return AGENT_COLORS[who] || null;
}

/* ---------- event dispatch ---------- */

function render(ev) {
  const [kind, agent, data] = ev;
  switch (kind) {
    case "hello":
      S.agents = data[0];
      S.selected = S.agents.includes(S.selected) ? S.selected : S.agents[0];
      buildPanels(S.agents);
      wipe();
      break;
    case "reset":
      wipe();
      break;
    case "wake":
      appendLine(agent, `> ${data[0]}`, "wake");
      break;
    case "think":
      appendProse(agent, "think", data[0]);
      break;
    case "text":
      appendProse(agent, "text", data[0]);
      break;
    case "call":
      appendLine(agent, data[0], "call");
      break;
    case "callout":
      appendOut(agent, data[0]);
      break;
    case "error":
      appendLine(agent, `!! ${data[0]}`, "err");
      if (S.pan.has(agent)) setState(S.pan.get(agent), "error");
      break;
    case "status": {
      const p = S.pan.get(agent);
      if (!p) break;
      setState(p, data[0]);
      if (data[0] === "idle" || data[0] === "error") closeBlock(p); // drop the caret once done
      break;
    }
    case "ctx": {
      const p = S.pan.get(agent);
      if (!p) break;
      p.ctx = data[0];
      p.ctxEl.textContent = S.ctxSlot
        ? `${(data[0] / 1000).toFixed(1)}k / ${(S.ctxSlot / 1000).toFixed(0)}k ctx`
        : `${(data[0] / 1000).toFixed(1)}k ctx`;
      break;
    }
    case "msg":
      pushItem(data[0], data[1], data[2]);
      break;
  }
}

/* ---------- SSE ---------- */

function setConnected(v) {
  S.connected = v;
  livedotEl.classList.toggle("live", v);
  livedotEl.title = v ? "stream live" : "stream disconnected";
  updateReadout();
}

const es = new EventSource("/events");
es.onopen = () => setConnected(true);
es.onerror = () => setConnected(false); // EventSource retries on its own; replay covers the gap
es.onmessage = (m) => {
  try { render(JSON.parse(m.data)); } catch (e) { /* ignore malformed frames */ }
};

/* ---------- health poll ---------- */

async function health() {
  try {
    const r = await fetch("/health");
    const h = await r.json();
    S.model = h.model;
    S.ctxSlot = h.ctx_slot || 0;
    // heal ctx readouts rendered before this first poll resolved
    for (const p of S.pan.values()) {
      if (!p.ctx) continue;
      p.ctxEl.textContent = S.ctxSlot
        ? `${(p.ctx / 1000).toFixed(1)}k / ${(S.ctxSlot / 1000).toFixed(0)}k ctx`
        : `${(p.ctx / 1000).toFixed(1)}k ctx`;
    }
    if (h.agents && h.agents.join(",") !== S.agents.join(",")) {
      S.agents = h.agents;
      buildPanels(h.agents);
    }
    readoutEl.dataset.inflight = h.in_flight;
    readoutEl.dataset.sent = h.sent;
    readoutEl.dataset.model = h.model;
    updateReadout();
  } catch (e) {
    readoutEl.textContent = "backend unreachable";
  }
}

function updateReadout() {
  if (readoutEl.dataset.model === undefined) return;
  const parts = [readoutEl.dataset.model, `${S.agents.length || "?"} agents`];
  if (+readoutEl.dataset.inflight > 0) parts.push(`${readoutEl.dataset.inflight} in flight`);
  readoutEl.textContent = parts.join(" · ");
}

setInterval(health, 3000);

/* ---------- composer ---------- */

let toastTimer = null;
function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 4200);
}

async function send() {
  const text = msgEl.value.trim();
  if (!text) return;
  sendBtn.disabled = true;
  try {
    const r = await fetch("/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to: S.selected, text }),
    });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(b.error || `server said ${r.status}`);
    msgEl.value = "";
  } catch (e) {
    toast(e.message);
  } finally {
    sendBtn.disabled = false;
    msgEl.focus();
  }
}

sendBtn.addEventListener("click", send);
msgEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

resetBtn.addEventListener("click", async () => {
  resetBtn.disabled = true;
  try {
    const r = await fetch("/reset", { method: "POST" });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(b.error || `server said ${r.status}`);
    wipe();
    toast("fresh session started");
  } catch (e) {
    toast(e.message);
  } finally {
    resetBtn.disabled = false;
  }
});

/* ---------- init ---------- */

health();
