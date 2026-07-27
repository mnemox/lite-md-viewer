// Local analysis: a chat over the open document, answered by a local Ollama model
// (run.bat --setup-ai). Entirely opt-in -- /api/ai/status decides whether the Ask button
// and this panel exist at all, so nothing changes for a reader who never set it up.
//
// The panel mirrors docnotes.js: a collapsible, resizable card, but docked to the left edge
// and full height (independent of the notes panel, which stays on the right). Answers
// stream in as NDJSON lines (see app/routers/ai.py) and are persisted server-side, so
// history survives reopening the document.

import { api } from './api.js';
import { renderMarkdown } from './render.js';
import { toast } from './ui.js';
import { revealPassage } from './search.js';

const $ = (id) => document.getElementById(id);
const COLLAPSE_KEY = 'aiPanelCollapsed';
const WIDTH_KEY = 'aiPanelWidth';

let wired = false;
let fileId = null;
let sending = false;
let aiEnabled = false;

// ---------- public API ----------

export function initAiChat() {
  if (wired) return;
  wired = true;

  const panel = $('aiPanel');
  const form = $('aiPanelForm');
  const input = $('aiPanelInput');

  // Collapsible, like the notes panel; the choice persists across reloads.
  setCollapsed(localStorage.getItem(COLLAPSE_KEY) === '1');
  $('aiPanelToggle').onclick = () => {
    const now = !panel.classList.contains('collapsed');
    setCollapsed(now);
    localStorage.setItem(COLLAPSE_KEY, now ? '1' : '0');
  };

  $('aiPanelClear').onclick = clearConversation;

  // Resizable width via the drag handle on the panel's left edge (same convention as Notes).
  const savedWidth = parseInt(localStorage.getItem(WIDTH_KEY), 10);
  if (savedWidth) panel.style.width = savedWidth + 'px';
  const handle = $('aiPanelResize');
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = panel.getBoundingClientRect().width;
    const min = parseFloat(getComputedStyle(panel).minWidth) || 220;
    const max = parseFloat(getComputedStyle(panel).maxWidth) || 720;
    handle.classList.add('active');
    handle.setPointerCapture(e.pointerId);
    const onMove = (ev) => {
      // The panel is anchored to the left edge with the handle on the right, so dragging
      // right (positive dx) grows it -- the mirror of the notes panel's own handle.
      const dx = ev.clientX - startX;
      const width = Math.min(max, Math.max(min, startWidth + dx));
      panel.style.width = width + 'px';
    };
    const onUp = () => {
      handle.classList.remove('active');
      handle.releasePointerCapture(e.pointerId);
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      localStorage.setItem(WIDTH_KEY, String(Math.round(panel.getBoundingClientRect().width)));
    };
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
  });

  form.addEventListener('submit', (e) => { e.preventDefault(); send(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  $('askBtn').onclick = () => {
    ensureExpanded();
    $('aiPanel').scrollIntoView({ block: 'nearest' });
    input.focus();
  };

  $('aiPanelMessages').addEventListener('click', (e) => {
    const chip = e.target.closest('.ai-source-chip');
    if (chip) revealPassage(chip.dataset.snippet || '');
  });

  refreshStatus();
}

// Fetch and render the conversation for a freshly-opened document.
export async function loadAiChat(id) {
  fileId = id;
  renderMessages([]);
  if (!aiEnabled) return;
  try {
    const messages = await api.aiChat(id);
    renderMessages(messages);
  } catch (e) {
    toast(e.message, 'error');
  }
}

// Reset the panel when leaving the file view (dashboard / welcome).
export function clearAiChat() {
  fileId = null;
  renderMessages([]);
}

// ---------- status ----------

async function refreshStatus() {
  let status;
  try { status = await api.aiStatus(); }
  catch { status = { enabled: false }; }

  aiEnabled = !!status.enabled;
  document.body.classList.toggle('ai-enabled', aiEnabled);
  if (!aiEnabled) return;

  const statusEl = $('aiPanelStatus');
  if (status.reachable && status.modelPresent) {
    statusEl.classList.add('hidden');
  } else if (!status.reachable) {
    statusEl.textContent = 'Ollama is not running. Start it, or run: ollama serve';
    statusEl.classList.remove('hidden');
  } else if (!status.modelPresent) {
    statusEl.textContent = `Model not found. Run: ollama pull ${status.model}`;
    statusEl.classList.remove('hidden');
  }
}

// ---------- sending ----------

async function send() {
  if (sending || fileId == null) return;
  const input = $('aiPanelInput');
  const question = input.value.trim();
  if (!question) return;
  input.value = '';
  input.style.height = '';

  appendUserMessage(question);
  const assistantEl = appendAssistantMessage();
  sending = true;
  $('aiPanelSend').disabled = true;

  try {
    const res = await api.askStream(fileId, question);
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.error || `${res.status} ${res.statusText}`);
    }
    await consumeStream(res, assistantEl);
  } catch (e) {
    setAssistantError(assistantEl, e.message);
  } finally {
    sending = false;
    $('aiPanelSend').disabled = false;
  }
}

async function consumeStream(res, assistantEl) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let answer = '';
  let sources = [];
  let pendingRender = false;

  const flush = () => {
    pendingRender = false;
    setAssistantText(assistantEl, answer, sources);
  };
  const scheduleRender = () => {
    if (pendingRender) return;
    pendingRender = true;
    requestAnimationFrame(flush);
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 1);
      if (!line.trim()) continue;
      handleLine(JSON.parse(line));
    }
  }
  if (buffer.trim()) handleLine(JSON.parse(buffer));
  flush();

  function handleLine(evt) {
    if (evt.type === 'sources') { sources = evt.sources || []; }
    else if (evt.type === 'delta') { answer += evt.text; scheduleRender(); }
    else if (evt.type === 'error') { setAssistantError(assistantEl, evt.error); }
    // 'done' needs no action: the final flush() above already shows the complete answer.
  }
}

async function clearConversation() {
  if (fileId == null) return;
  try {
    await api.clearAiChat(fileId);
    renderMessages([]);
  } catch (e) { toast(e.message, 'error'); }
}

// ---------- rendering ----------

function renderMessages(messages) {
  const list = $('aiPanelMessages');
  list.innerHTML = '';
  if (!messages.length) {
    const empty = document.createElement('p');
    empty.className = 'ai-panel-empty';
    empty.textContent = 'Ask a question about this document.';
    list.appendChild(empty);
    return;
  }
  for (const m of messages) {
    if (m.role === 'user') appendUserMessage(m.text);
    else appendAssistantMessage(m.text, m.sources);
  }
  list.scrollTop = list.scrollHeight;
}

function clearEmptyState() {
  $('aiPanelMessages').querySelector('.ai-panel-empty')?.remove();
}

function appendUserMessage(text) {
  clearEmptyState();
  const el = document.createElement('div');
  el.className = 'ai-msg user';
  const body = document.createElement('div');
  body.className = 'ai-msg-body';
  body.textContent = text;
  el.appendChild(body);
  $('aiPanelMessages').appendChild(el);
  scrollToBottom();
  return el;
}

function appendAssistantMessage(text, sources) {
  clearEmptyState();
  const el = document.createElement('div');
  el.className = 'ai-msg assistant' + (text ? '' : ' pending');
  const body = document.createElement('article');
  body.className = 'viewer markdown-body ai-msg-body';
  body.dir = 'auto';
  el.appendChild(body);
  $('aiPanelMessages').appendChild(el);
  if (text) setAssistantText(el, text, sources || []);
  scrollToBottom();
  return el;
}

function setAssistantText(el, text, sources) {
  el.classList.toggle('pending', !text);
  const body = el.querySelector('.ai-msg-body');
  renderMarkdown(text || '', body);
  el.querySelector('.ai-sources')?.remove();
  if (sources && sources.length) el.appendChild(buildSources(sources));
  scrollToBottom();
}

function setAssistantError(el, message) {
  el.classList.remove('pending');
  const body = el.querySelector('.ai-msg-body');
  if (!body.textContent.trim()) {
    const err = document.createElement('p');
    err.className = 'ai-msg-error';
    err.textContent = message;
    body.replaceChildren(err);
  } else {
    toast(message, 'error');
  }
}

function buildSources(sources) {
  const wrap = document.createElement('div');
  wrap.className = 'ai-sources';
  for (const s of sources) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'ai-source-chip';
    chip.dataset.snippet = s.snippet || '';
    chip.title = s.snippet || '';
    chip.textContent = (s.sectionPath && s.sectionPath.length)
      ? s.sectionPath.join(' › ') : 'Section';
    wrap.appendChild(chip);
  }
  return wrap;
}

function scrollToBottom() {
  const list = $('aiPanelMessages');
  list.scrollTop = list.scrollHeight;
}

// ---------- helpers ----------

function setCollapsed(on) {
  $('aiPanel').classList.toggle('collapsed', on);
  $('aiPanelToggle').setAttribute('aria-expanded', String(!on));
}

function ensureExpanded() {
  if ($('aiPanel').classList.contains('collapsed')) {
    setCollapsed(false);
    localStorage.setItem(COLLAPSE_KEY, '0');
  }
}
