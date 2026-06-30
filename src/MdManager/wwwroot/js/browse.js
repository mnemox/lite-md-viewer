// Server-backed "Add file" browser modal: navigate the real disk and pick a .md file.
import { api } from './api.js';
import { toast } from './ui.js';

let onPick = null;
let last = null;       // last BrowseResult
let mode = 'add';      // 'add' (pick an existing .md) | 'create' (name a new .md here)

const $ = (id) => document.getElementById(id);
const modal = () => $('browseModal');

export function initBrowse() {
  $('browseClose').onclick = close;
  $('browseUp').onclick = goUp;
  $('browseFilter').oninput = applyFilter;
  $('browseAddPath').onclick = submit;
  $('browsePath').addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
  modal().addEventListener('click', (e) => { if (e.target === modal()) close(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !modal().classList.contains('hidden')) close();
  });
}

export async function openBrowse(pickHandler, opts = {}) {
  onPick = pickHandler;
  mode = opts.mode === 'create' ? 'create' : 'add';
  modal().classList.remove('hidden');
  $('browseFilter').value = '';
  $('browsePath').value = '';
  $('browseTitle').textContent = mode === 'create' ? 'Create a Markdown file' : 'Add a Markdown file';
  $('browseAddPath').textContent = mode === 'create' ? 'Create here' : 'Add';
  $('browsePath').placeholder = mode === 'create'
    ? 'New file name (e.g. notes.md) — created in the open folder'
    : '…or paste a full path to a .md file';
  let start = '';
  try { start = (await api.settings()).lastBrowsedDir || ''; } catch { /* ignore */ }
  await load(start);
}

function close() { modal().classList.add('hidden'); }

async function load(path) {
  let res;
  try { res = await api.browse(path); }
  catch (e) { toast(e.message, 'error'); return; }
  last = res;
  $('browseCrumb').textContent = res.isRoot ? 'This PC' : (res.path || '');
  $('browseUp').disabled = res.isRoot;
  if (res.path) api.setSetting('lastBrowsedDir', res.path).catch(() => {});
  renderEntries(res.entries);
}

function goUp() {
  if (!last || last.isRoot) return;
  load(last.parent || '');   // null parent (drive root) -> drives list
}

function renderEntries(entries) {
  const list = $('browseList');
  list.innerHTML = '';
  const filter = $('browseFilter').value.toLowerCase();
  let shown = 0;
  for (const e of entries) {
    if (filter && !e.name.toLowerCase().includes(filter)) continue;
    shown++;
    const li = document.createElement('li');
    if (!e.accessible) li.className = 'disabled';
    const icon = document.createElement('span');
    icon.textContent = e.isDir ? '📁' : '📄';
    const name = document.createElement('span');
    name.className = 'name'; name.textContent = e.name; name.dir = 'auto';
    li.append(icon, name);
    if (e.isDir) {
      if (e.accessible) li.onclick = () => load(e.path);
    } else if (mode === 'add') {
      li.onclick = () => pick(e.path);
    } else {
      // create mode: existing files are shown (so you can avoid name clashes) but not pickable
      li.style.opacity = '.6';
    }
    list.appendChild(li);
  }
  if (!shown) {
    const li = document.createElement('li');
    li.className = 'disabled';
    li.textContent = filter ? 'No matches.' : 'No subfolders or .md files here.';
    list.appendChild(li);
  }
}

function applyFilter() { if (last) renderEntries(last.entries); }

function submit() { return mode === 'create' ? createHere() : addFromPath(); }

function addFromPath() {
  const p = $('browsePath').value.trim();
  if (p) pick(p);
}

function createHere() {
  const name = $('browsePath').value.trim();
  if (!name) { toast('Enter a file name.', 'error'); return; }
  if (!last || !last.path) { toast('Open the folder to create the file in.', 'error'); return; }
  close();
  if (onPick) onPick({ dir: last.path, name });
}

function pick(path) {
  close();
  if (onPick) onPick(path);
}
