import { api } from './api.js';
import { toast, confirmDialog, promptDialog, detailsDialog } from './ui.js';
import { renderDoc } from './render.js';
import { applyTheme, currentTheme } from './theme.js';
import { renderTree } from './tree.js';
import { initBrowse, openBrowse } from './browse.js';
import { openRelations, closeRelations } from './relations.js';
import { initDashboard, renderDashboardNotes } from './dashboard.js';

const $ = (id) => document.getElementById(id);

const state = {
  treeData: { folders: [], files: [] },
  active: null,        // active FileDto
  text: '',            // current file's raw text
  docPath: '',         // active file's full path (selects the render pipeline: markdown vs xml)
  mode: 'view',        // 'view' | 'edit'
  readOnly: false,     // true when showing a missing file's DB copy (locked, warning strip up)
};

// ---------- drawer (hover + click-to-pin) ----------
let pinned = false;
const drawer = () => $('drawer');
const scrim = () => $('scrim');

function openDrawer() {
  closeRelations();          // opening the side menu dismisses the relations overlay
  drawer().classList.add('open');
  drawer().setAttribute('aria-hidden', 'false');
  $('menuBtn').setAttribute('aria-expanded', 'true');
  scrim().hidden = pinned;
}
function closeDrawer() {
  drawer().classList.remove('open');
  drawer().setAttribute('aria-hidden', 'true');
  $('menuBtn').setAttribute('aria-expanded', 'false');
  scrim().hidden = true;
}
function setPinned(v) {
  pinned = v;
  document.body.classList.toggle('pinned', v);
  if (v) { openDrawer(); scrim().hidden = true; } else { closeDrawer(); }
}

// ---------- tree ----------
async function refreshTree() {
  try { state.treeData = await api.tree(); }
  catch (e) { toast(e.message, 'error'); return; }

  // keep active file's status fresh
  if (state.active) {
    const updated = state.treeData.files.find((f) => f.id === state.active.id);
    if (updated) {
      const wasMissing = !!state.active.missing;
      state.active = updated;
      updateToolbar();
      // The file appeared or vanished on disk since the last poll → re-fetch so the content
      // source (disk vs DB) and the read-only warning strip stay in sync (the strip is shown
      // until the file is back, per requirement).
      if (!!updated.missing !== wasMissing) { openFile(updated.id, { push: false }); return; }
    }
  }

  const container = $('tree');
  const scrollTop = container.scrollTop;
  renderTree(container, state.treeData, treeHandlers, state.active?.id);
  container.scrollTop = scrollTop;
}

const treeHandlers = {
  openFile,
  renameFile: async (id, title) => { try { await api.patchFile(id, { title }); await refreshTree(); } catch (e) { toast(e.message, 'error'); } },
  moveFile: async (id, folderId) => {
    try { await api.patchFile(id, folderId == null ? { moveToRoot: true } : { folderId }); await refreshTree(); }
    catch (e) { toast(e.message, 'error'); }
  },
  moveFileToDisk: (id) => {
    openBrowse(async (path) => {
      try {
        await api.moveFileToDisk(id, path);
        await refreshTree();
        toast('File moved', 'ok');
      } catch (e) { toast(e.message, 'error'); }
    }, { mode: 'folder', title: 'Move file to folder…', addLabel: 'Move here', pathPlaceholder: '…or paste a full destination folder path' });
  },
  removeFromList,
  deleteDisk,
  addFolder: (parentId) => addFolder(parentId),
  renameFolder: async (id, name) => { try { await api.patchFolder(id, { name }); await refreshTree(); } catch (e) { toast(e.message, 'error'); } },
  moveFolder: async (id, parentId) => {
    try { await api.patchFolder(id, parentId == null ? { moveToRoot: true } : { parentId }); await refreshTree(); }
    catch (e) { toast(e.message, 'error'); }
  },
  removeFolder: async (folder) => {
    if (!(await confirmDialog(`Delete folder “${folder.name}”? Its contents move up one level.`, { okLabel: 'Delete', danger: true }))) return;
    try { await api.removeFolder(folder.id); await refreshTree(); } catch (e) { toast(e.message, 'error'); }
  },
};

// ---------- dashboard ----------
// An empty screen shown in the same layout. Not a file, so the center toolbar
// (View/Edit/Details/Relations) stays hidden.
function openDashboard() {
  closeRelations();
  state.active = null; state.text = '';
  applyReadOnly(false);
  document.body.classList.remove('file-open');
  document.body.classList.add('dashboard');
  $('dashboardBtn').classList.add('active');
  $('welcome').classList.add('hidden');
  $('viewer').classList.add('hidden');
  $('editor').classList.add('hidden');
  $('loading').classList.add('hidden');
  $('dashboard').classList.remove('hidden');
  renderTree($('tree'), state.treeData, treeHandlers, null);
  renderDashboardNotes();
  if (urlFileId() != null) history.replaceState({}, '', location.pathname);
}

// ---------- file open / view / edit ----------
async function openFile(id, { push = true } = {}) {
  // leaving the dashboard for a real file
  document.body.classList.remove('dashboard');
  $('dashboardBtn').classList.remove('active');
  $('dashboard').classList.add('hidden');
  // show the loading spinner over an emptied content area while we fetch
  $('welcome').classList.add('hidden');
  $('viewer').classList.add('hidden');
  $('editor').classList.add('hidden');
  $('loading').classList.remove('hidden');

  let content;
  try { content = await api.content(id); }
  catch (e) {
    $('loading').classList.add('hidden');
    if (!state.active) $('welcome').classList.remove('hidden');
    toast(e.message, 'error'); await refreshTree(); return;
  }
  state.active = state.treeData.files.find((f) => f.id === id) || { id, title: content.title, missing: !content.onDisk };
  state.text = content.text;
  state.docPath = content.fullPath;
  document.body.classList.add('file-open');
  applyReadOnly(!content.onDisk);   // content came from the DB mirror → lock + show strip
  setMode('view');
  updateToolbar();
  renderTree($('tree'), state.treeData, treeHandlers, id);
  $('loading').classList.add('hidden');
  if (push) setFileUrl(id);
}

// ---------- deep-linking (URL + history) ----------
function setFileUrl(id) {
  const url = location.pathname + '?file=' + encodeURIComponent(id);
  if (location.pathname + location.search !== url) history.pushState({ fileId: id }, '', url);
}
function urlFileId() {
  const v = new URLSearchParams(location.search).get('file');
  return v == null || v === '' ? null : Number(v);
}

function updateToolbar() {
  if (!state.active) return;
  $('fileTitle').textContent = state.active.title;
  // The only status worth surfacing is a file that's gone missing on disk.
  const badge = $('fileStatus');
  const missing = !!state.active.missing;
  badge.textContent = missing ? 'missing' : '';
  badge.className = 'badge missing';
  badge.classList.toggle('hidden', !missing);
}

// Lock/unlock editing for a missing file served from the DB. When locked, the yellow
// warning strip is shown and the Edit toggle is disabled (view stays available).
function applyReadOnly(on) {
  state.readOnly = on;
  $('dbWarning').classList.toggle('hidden', !on);
  $('editModeBtn').disabled = on;
}

let previewTimer = null;
function setMode(mode) {
  if (mode === 'edit' && state.readOnly) return;   // can't edit the DB copy of a missing file
  state.mode = mode;
  const view = mode === 'view';
  $('viewModeBtn').classList.toggle('active', view);
  $('editModeBtn').classList.toggle('active', !view);
  $('saveBtn').classList.toggle('hidden', view);
  $('viewer').classList.toggle('hidden', !view);
  $('editor').classList.toggle('hidden', view);
  if (view) {
    renderDoc(state.docPath, state.text, $('viewer'));
  } else {
    $('editorText').value = state.text;
    renderDoc(state.docPath, state.text, $('editorPreview'));
  }
}

async function save() {
  if (!state.active) return;
  const text = $('editorText').value;
  try {
    await api.saveContent(state.active.id, text);
    state.text = text;
    toast('Saved', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

// Restore a missing file to disk from its DB copy, at its original path. Afterwards the
// file exists again, so refreshTree's missing→present transition re-opens it editable and
// drops the warning strip.
async function recreateFile() {
  if (!state.active) return;
  try {
    await api.recreateFile(state.active.id);
  } catch (e) { toast(e.message, 'error'); return; }
  toast('File recreated', 'ok');
  await refreshTree();
}

// ---------- file details ----------
async function showDetails() {
  if (!state.active) return;
  try { detailsDialog(await api.details(state.active.id)); }
  catch (e) { toast(e.message, 'error'); }
}

function showRelations() {
  if (!state.active) return;
  // The graph reloads the background document when a node is opened.
  openRelations(state.active.id, (id) => openFile(id));
}

// ---------- remove / delete ----------
async function removeFromList(file) {
  try {
    await api.removeFile(file.id);
  } catch (e) { toast(e.message, 'error'); return; }
  if (state.active && state.active.id === file.id) clearActive();
  await refreshTree();
  toast('Removed from list', 'ok');
}

async function deleteDisk(file) {
  if (!(await confirmDialog(`Delete “${file.title}” from disk? This cannot be undone.`, { okLabel: 'Delete', danger: true }))) return;
  try {
    await api.deleteDisk(file.id);
    if (state.active && state.active.id === file.id) clearActive();
    await refreshTree();
    toast('Deleted from disk', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

function clearActive() {
  state.active = null; state.text = '';
  applyReadOnly(false);
  document.body.classList.remove('file-open');
  document.body.classList.remove('dashboard');
  $('dashboardBtn').classList.remove('active');
  $('dashboard').classList.add('hidden');
  $('viewer').classList.add('hidden');
  $('editor').classList.add('hidden');
  $('loading').classList.add('hidden');
  $('welcome').classList.remove('hidden');
  if (urlFileId() != null) history.replaceState({}, '', location.pathname);
}

// ---------- add file / folder ----------
function startAddFile() {
  openBrowse(async (path) => {
    try {
      const dto = await api.addFile(path);
      toast('Added', 'ok');
      await refreshTree();
      openFile(dto.id);
    } catch (e) {
      if (e.status === 409 && e.data?.id) { toast('Already managed'); openFile(e.data.id); }
      else toast(e.message, 'error');
    }
  });
}

function startAddFolder() {
  openBrowse(async (path) => {
    try {
      const res = await api.addFolderFiles(path);
      if (res.added === 0 && res.skipped === 0) toast('No Markdown or XML files in that folder');
      else if (res.added === 0) toast('All files there are already managed');
      else toast(`Added ${res.added} file${res.added === 1 ? '' : 's'}`
        + (res.skipped ? ` (${res.skipped} already managed)` : ''), 'ok');
      await refreshTree();
    } catch (e) { toast(e.message, 'error'); }
  }, { mode: 'folder' });
}

function startNewFile() {
  openBrowse(async ({ dir, name }) => {
    try {
      const dto = await api.newFile(dir, name);
      toast('Created', 'ok');
      await refreshTree();
      openFile(dto.id);
    } catch (e) {
      if (e.status === 409 && e.data?.id) { toast('Already managed'); openFile(e.data.id); }
      else toast(e.message, 'error');
    }
  }, { mode: 'create' });
}

async function addFolder(parentId) {
  const name = await promptDialog('New folder', { okLabel: 'Create', placeholder: 'Folder name' });
  if (!name) return;
  try { await api.addFolder(name, parentId ?? null); await refreshTree(); }
  catch (e) { toast(e.message, 'error'); }
}

// ---------- "Add ▾" dropdown ----------
function closeAddMenu() {
  const m = $('addMenu');
  if (m.classList.contains('hidden')) return false;
  m.classList.add('hidden');
  $('addBtn').setAttribute('aria-expanded', 'false');
  m.parentElement.classList.remove('open');
  return true;
}
function toggleAddMenu() {
  const m = $('addMenu');
  const show = m.classList.contains('hidden');
  m.classList.toggle('hidden', !show);
  $('addBtn').setAttribute('aria-expanded', String(show));
  m.parentElement.classList.toggle('open', show);
}

// ---------- theme ----------
async function toggleTheme() {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  api.setSetting('theme', next).catch(() => {});
  // re-render current doc so Mermaid picks up the new theme
  if (state.active) {
    if (state.mode === 'view') renderDoc(state.docPath, state.text, $('viewer'));
    else renderDoc(state.docPath, $('editorText').value, $('editorPreview'));
  }
}

// ---------- init ----------
async function init() {
  // theme from server (localStorage already applied early to avoid flash)
  try { const s = await api.settings(); if (s.theme) applyTheme(s.theme); else applyTheme(currentTheme()); }
  catch { applyTheme(currentTheme()); }

  initBrowse();
  initDashboard();

  // Any header button (except Relations itself, which opens it) closes the relations modal.
  document.querySelector('.topbar').addEventListener('click', (e) => {
    if (e.target.closest('#relationsBtn')) return;
    if (e.target.closest('button')) closeRelations();
  }, true);

  $('menuBtn').onclick = () => setPinned(!pinned);
  $('menuBtn').addEventListener('mouseenter', () => { if (!pinned) openDrawer(); });
  $('edgeZone').addEventListener('mouseenter', () => { if (!pinned) openDrawer(); });
  // The drawer stays open once opened; it closes only via the X button, an
  // outside (scrim) click, or Escape — not on mouse-leave. This keeps a row's
  // popup menu usable even when the cursor moves outside the drawer bounds.
  $('drawerCloseBtn').onclick = () => setPinned(false);
  scrim().onclick = () => setPinned(false);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (closeAddMenu()) return;           // first Escape only closes the Add menu
      if (pinned) setPinned(false); else closeDrawer();
    }
  });

  $('themeBtn').onclick = toggleTheme;
  $('dashboardBtn').onclick = openDashboard;
  $('newFileBtn').onclick = startNewFile;
  $('addBtn').onclick = toggleAddMenu;
  $('addFileOpt').onclick = () => { closeAddMenu(); startAddFile(); };
  $('addFolderFilesOpt').onclick = () => { closeAddMenu(); startAddFolder(); };
  document.addEventListener('mousedown', (e) => {
    if (!$('addMenu').parentElement.contains(e.target)) closeAddMenu();
  });
  $('welcomeAdd').onclick = startAddFile;
  $('addFolderBtn').onclick = () => addFolder(null);

  $('viewModeBtn').onclick = () => setMode('view');
  $('editModeBtn').onclick = () => setMode('edit');
  $('saveBtn').onclick = save;
  $('recreateBtn').onclick = recreateFile;
  $('detailsBtn').onclick = showDetails;
  $('relationsBtn').onclick = showRelations;
  $('editorText').addEventListener('input', () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => renderDoc(state.docPath, $('editorText').value, $('editorPreview')), 300);
  });

  await refreshTree();

  // Deep-linking: open the file named in the URL (e.g. on refresh) and react to
  // browser back/forward.
  window.addEventListener('popstate', () => {
    const id = urlFileId();
    // Same file already active (e.g. Back that only closed the relations modal): nothing to do.
    if (id != null && state.active && id === state.active.id) return;
    if (id != null && state.treeData.files.some((f) => f.id === id)) openFile(id, { push: false });
    else if (state.active) clearActive();
  });
  const startId = urlFileId();
  if (startId != null && state.treeData.files.some((f) => f.id === startId)) openFile(startId, { push: false });

  // periodic status refresh (skip while inline-editing or a menu is open)
  setInterval(() => {
    if (document.activeElement?.isContentEditable) return;
    if (document.querySelector('.popup-menu')) return;
    if (!$('browseModal').classList.contains('hidden')) return;
    refreshTree();
  }, 8000);
}

init();
