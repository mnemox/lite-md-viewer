import { api } from './api.js';
import { toast, confirmDialog, promptDialog, detailsDialog } from './ui.js';
import { renderDoc } from './render.js';
import { applyTheme, currentTheme } from './theme.js';
import { renderTree } from './tree.js';
import { initBrowse, openBrowse } from './browse.js';
import { openRelations, closeRelations } from './relations.js';
import { initDashboard, renderDashboardNotes } from './dashboard.js';
import { initDocNotes, loadDocNotes, clearDocNotes, refreshDocHighlights } from './docnotes.js';
import { initSearch, syncSearchScope } from './search.js';
import { initAiChat, loadAiChat, clearAiChat } from './aichat.js';
import { initRouter, navigate, startRouter } from './router.js';

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
      if (!!updated.missing !== wasMissing) { loadFileContent(updated.id, state.mode); return; }
    }
  }

  const container = $('tree');
  const scrollTop = container.scrollTop;
  renderTree(container, state.treeData, treeHandlers, state.active?.id);
  container.scrollTop = scrollTop;
}

const treeHandlers = {
  openFile: goToFile,
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

// ---------- routing ----------
// The single handler every route -- welcome, notes, or a file -- renders through.
// Called by router.js; never call the render functions below directly to navigate,
// use navigate() (or the goToFile/setMode/openDashboard helpers that wrap it) instead.
async function applyRoute(route) {
  if (route.name === 'file') {
    if (state.active && state.active.id === route.fileId) {
      applyMode(route.mode);   // same file already loaded: just switch view/edit, no re-fetch
      return;
    }
    await loadFileContent(route.fileId, route.mode);
    return;
  }
  if (route.name === 'notes') { showNotes(); return; }
  showWelcome();
}

// Navigate to a file (tree clicks, search results, relations graph nodes, newly
// created/added files, ...).
function goToFile(id, mode = 'view') {
  navigate({ name: 'file', fileId: id, mode });
}

// ---------- dashboard ----------
// An empty screen shown in the same layout. Not a file, so the center toolbar
// (View/Edit/Details/Relations) stays hidden.
function openDashboard() {
  navigate({ name: 'notes' });
}

function showNotes() {
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
  clearDocNotes();
  clearAiChat();
  syncSearchScope();     // no document open: the in-document scope lapses
}

// ---------- file open / view / edit ----------
// Pure DOM + fetch: loads and renders a file's content. Never touches history/URL --
// that's navigate()'s job -- so it's also reused for in-place content refreshes (e.g.
// refreshTree's disk-status-flip) that shouldn't push or change a route.
async function loadFileContent(id, mode) {
  // leaving the dashboard for a real file
  document.body.classList.remove('dashboard');
  $('dashboardBtn').classList.remove('active');
  $('dashboard').classList.add('hidden');
  // show the loading spinner over an emptied content area while we fetch
  $('welcome').classList.add('hidden');
  $('viewer').classList.add('hidden');
  $('editor').classList.add('hidden');
  clearDocNotes();
  $('loading').classList.remove('hidden');

  let content;
  try { content = await api.content(id); }
  catch (e) {
    $('loading').classList.add('hidden');
    if (!state.active) $('welcome').classList.remove('hidden');
    toast(e.message, 'error'); await refreshTree();
    // keep the URL in agreement with whatever ended up on screen
    navigate(state.active ? { name: 'file', fileId: state.active.id, mode: state.mode } : { name: 'welcome' }, { push: false });
    return;
  }
  state.active = state.treeData.files.find((f) => f.id === id) || { id, title: content.title, missing: !content.onDisk };
  state.text = content.text;
  state.docPath = content.fullPath;
  document.body.classList.add('file-open');
  applyReadOnly(!content.onDisk);   // content came from the DB mirror → lock + show strip
  applyMode(mode);
  updateToolbar();
  renderTree($('tree'), state.treeData, treeHandlers, id);
  loadDocNotes(id);
  loadAiChat(id);
  syncSearchScope();     // the toggle names the open document, so it moves with it
  $('loading').classList.add('hidden');
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
  // The notes panel sits below the warning strip; update its top offset once the strip
  // has been laid out.
  if (on) requestAnimationFrame(updateDocNotesOffset);
  else updateDocNotesOffset();
}

function updateDocNotesOffset() {
  const warning = $('dbWarning');
  const h = warning && !warning.classList.contains('hidden') ? warning.getBoundingClientRect().height : 0;
  document.documentElement.style.setProperty('--warning-h', `${h}px`);
}

let previewTimer = null;

// Pure DOM: switches the view/edit panes for the active file. Called by applyRoute (so
// it never re-fetches content) -- never call this directly to change mode, use setMode().
function applyMode(mode) {
  if (mode === 'edit' && state.readOnly) mode = 'view'; // can't edit the DB copy of a missing file
  state.mode = mode;
  const view = mode === 'view';
  $('viewModeBtn').classList.toggle('active', view);
  $('viewModeBtn').setAttribute('aria-selected', view ? 'true' : 'false');
  $('editModeBtn').classList.toggle('active', !view);
  $('editModeBtn').setAttribute('aria-selected', !view ? 'true' : 'false');
  $('saveBtn').classList.toggle('hidden', view);
  $('viewer').classList.toggle('hidden', !view);
  $('editor').classList.toggle('hidden', view);
  if (view) {
    Promise.resolve(renderDoc(state.docPath, state.text, $('viewer'))).then(() => refreshDocHighlights());
  } else {
    $('editorText').value = state.text;
    Promise.resolve(renderDoc(state.docPath, state.text, $('editorPreview'))).then(() => refreshDocHighlights());
  }
}

// View/Edit tab clicks: an in-place mode change on the same file, so it replaces the
// current history entry rather than pushing a new one.
function setMode(mode) {
  if (!state.active || (mode === 'edit' && state.readOnly)) return;
  navigate({ name: 'file', fileId: state.active.id, mode }, { push: false });
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
  openRelations(state.active.id, (id) => goToFile(id));
}

// ---------- remove / delete ----------
async function removeFromList(file) {
  try {
    await api.removeFile(file.id);
  } catch (e) { toast(e.message, 'error'); return; }
  if (state.active && state.active.id === file.id) navigate({ name: 'welcome' });
  await refreshTree();
  toast('Removed from list', 'ok');
}

async function deleteDisk(file) {
  if (!(await confirmDialog(`Delete “${file.title}” from disk? This cannot be undone.`, { okLabel: 'Delete', danger: true }))) return;
  try {
    await api.deleteDisk(file.id);
    if (state.active && state.active.id === file.id) navigate({ name: 'welcome' });
    await refreshTree();
    toast('Deleted from disk', 'ok');
  } catch (e) { toast(e.message, 'error'); }
}

// Pure DOM: resets the layout to the empty welcome screen. Called by applyRoute --
// navigate to it (see removeFromList/deleteDisk above) rather than calling this directly.
function showWelcome() {
  state.active = null; state.text = '';
  applyReadOnly(false);
  clearDocNotes();
  clearAiChat();
  document.body.classList.remove('file-open');
  document.body.classList.remove('dashboard');
  $('dashboardBtn').classList.remove('active');
  $('dashboard').classList.add('hidden');
  $('viewer').classList.add('hidden');
  $('editor').classList.add('hidden');
  $('loading').classList.add('hidden');
  $('welcome').classList.remove('hidden');
}

// ---------- add file / folder ----------
function startAddFile() {
  openBrowse(async (path) => {
    try {
      const dto = await api.addFile(path);
      toast('Added', 'ok');
      await refreshTree();
      goToFile(dto.id);
    } catch (e) {
      if (e.status === 409 && e.data?.id) { toast('Already managed'); goToFile(e.data.id); }
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
      goToFile(dto.id);
    } catch (e) {
      if (e.status === 409 && e.data?.id) { toast('Already managed'); goToFile(e.data.id); }
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
  initDashboard(goToFile);
  initDocNotes();
  initAiChat();
  initSearch(goToFile, () => state.active);
  initRouter(applyRoute);

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

  window.addEventListener('resize', updateDocNotesOffset);

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
    previewTimer = setTimeout(() => Promise.resolve(renderDoc(state.docPath, $('editorText').value, $('editorPreview'))).then(() => refreshDocHighlights()), 300);
  });

  await refreshTree();

  // Render whatever the current URL says (e.g. a deep link or a hard refresh).
  startRouter();

  // periodic status refresh (skip while inline-editing or a menu is open)
  setInterval(() => {
    if (document.activeElement?.isContentEditable) return;
    if (document.querySelector('.popup-menu')) return;
    if (!$('browseModal').classList.contains('hidden')) return;
    refreshTree();
  }, 8000);
}

init();
