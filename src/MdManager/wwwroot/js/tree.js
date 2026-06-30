// Renders the folder/file tree in the drawer and wires its interactions:
// open, inline rename, lock toggle, drag-to-folder, and a per-row action menu.

const collapsed = new Set();      // folder ids the user collapsed (expanded by default)

function popupMenu(anchor, items) {
  document.querySelector('.popup-menu')?.remove();
  const menu = document.createElement('div');
  menu.className = 'popup-menu';
  Object.assign(menu.style, {
    position: 'fixed', zIndex: 90, background: 'var(--bg)', border: '1px solid var(--border)',
    borderRadius: '8px', boxShadow: 'var(--shadow)', padding: '4px', minWidth: '170px',
  });
  for (const it of items) {
    const b = document.createElement('button');
    b.textContent = it.label;
    b.disabled = !!it.disabled;
    Object.assign(b.style, {
      display: 'block', width: '100%', textAlign: 'start', border: '0', background: 'transparent',
      color: it.danger ? 'var(--danger)' : 'var(--text)', padding: '8px 10px', borderRadius: '6px',
      cursor: it.disabled ? 'not-allowed' : 'pointer', font: 'inherit', opacity: it.disabled ? .5 : 1,
    });
    b.onmouseenter = () => { if (!it.disabled) b.style.background = 'var(--bg-elev-2)'; };
    b.onmouseleave = () => { b.style.background = 'transparent'; };
    b.onclick = () => { menu.remove(); if (!it.disabled) it.onClick(); };
    menu.appendChild(b);
  }
  document.body.appendChild(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.insetInlineStart = Math.min(r.left, window.innerWidth - 190) + 'px';
  menu.style.insetBlockStart = (r.bottom + 4) + 'px';
  const off = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('mousedown', off); } };
  setTimeout(() => document.addEventListener('mousedown', off), 0);
}

function editableLabel(el, current, commit) {
  el.setAttribute('contenteditable', 'true');
  el.textContent = current;
  el.focus();
  const range = document.createRange();
  range.selectNodeContents(el);
  getSelection().removeAllRanges();
  getSelection().addRange(range);
  const finish = (save) => {
    el.removeAttribute('contenteditable');
    el.onkeydown = null; el.onblur = null;
    const val = el.textContent.trim();
    if (save && val && val !== current) commit(val);
    else el.textContent = current;
  };
  el.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  };
  el.onblur = () => finish(true);
}

export function renderTree(container, data, handlers, activeFileId) {
  const foldersByParent = new Map();
  const filesByFolder = new Map();
  for (const f of data.folders) {
    const k = f.parentId ?? 0;
    (foldersByParent.get(k) || foldersByParent.set(k, []).get(k)).push(f);
  }
  for (const f of data.files) {
    const k = f.folderId ?? 0;
    (filesByFolder.get(k) || filesByFolder.set(k, []).get(k)).push(f);
  }

  container.innerHTML = '';
  if (!data.folders.length && !data.files.length) {
    const empty = document.createElement('div');
    empty.className = 'tree-empty';
    empty.textContent = 'No files yet. Click “+ Add file” to manage one.';
    container.appendChild(empty);
  }

  const dropToFolder = (row, folderId) => {
    row.addEventListener('dragover', (e) => { e.preventDefault(); row.classList.add('drop-target'); });
    row.addEventListener('dragleave', () => row.classList.remove('drop-target'));
    row.addEventListener('drop', (e) => {
      e.preventDefault(); e.stopPropagation(); row.classList.remove('drop-target');
      const id = +e.dataTransfer.getData('text/file-id');
      if (id) handlers.moveFile(id, folderId);
    });
  };

  function fileRow(file) {
    const row = document.createElement('div');
    row.className = 'row' + (file.id === activeFileId ? ' active' : '');
    row.draggable = true;
    row.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/file-id', String(file.id)));

    const caret = document.createElement('span'); caret.className = 'caret';
    const icon = document.createElement('span'); icon.className = 'icon'; icon.textContent = '📄';
    const label = document.createElement('span'); label.className = 'label'; label.textContent = file.title; label.dir = 'auto';
    const lock = document.createElement('span'); lock.className = 'lock';
    lock.textContent = file.isLockRequested ? '🔒' : '🔓';
    lock.title = file.isLockRequested ? 'Locked — click to unlock' : 'Unlocked — click to lock';
    const kebab = document.createElement('span'); kebab.className = 'kebab'; kebab.textContent = '⋯';

    if (file.missing) { label.style.color = 'var(--danger)'; label.title = 'File is missing on disk'; }

    row.append(caret, icon, label, lock, kebab);

    row.addEventListener('click', (e) => {
      if (e.target === lock || e.target === kebab || label.isContentEditable) return;
      handlers.openFile(file.id);
    });
    label.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      editableLabel(label, file.title, (val) => handlers.renameFile(file.id, val));
    });
    lock.addEventListener('click', (e) => { e.stopPropagation(); handlers.toggleLock(file); });
    kebab.addEventListener('click', (e) => {
      e.stopPropagation();
      popupMenu(kebab, [
        { label: 'Rename', onClick: () => editableLabel(label, file.title, (v) => handlers.renameFile(file.id, v)) },
        { label: 'Move to top level', onClick: () => handlers.moveFile(file.id, null) },
        { label: file.isLockRequested ? 'Unlock' : 'Lock', onClick: () => handlers.toggleLock(file) },
        { label: 'Remove from list', onClick: () => handlers.removeFromList(file) },
        { label: 'Delete from disk…', danger: true, disabled: file.isLockRequested, onClick: () => handlers.deleteDisk(file) },
      ]);
    });
    return row;
  }

  function folderNode(folder, depth) {
    const wrap = document.createElement('div');
    wrap.className = 'tree-node';
    const row = document.createElement('div');
    row.className = 'row';
    const isOpen = !collapsed.has(folder.id);

    const caret = document.createElement('span');
    caret.className = 'caret'; caret.textContent = isOpen ? '▾' : '▸';
    const icon = document.createElement('span'); icon.className = 'icon'; icon.textContent = '📁';
    const label = document.createElement('span'); label.className = 'label'; label.textContent = folder.name; label.dir = 'auto';
    const kebab = document.createElement('span'); kebab.className = 'kebab'; kebab.textContent = '⋯';
    row.append(caret, icon, label, kebab);

    const children = document.createElement('div');
    children.className = 'children';
    children.style.display = isOpen ? '' : 'none';

    const toggle = () => {
      if (collapsed.has(folder.id)) { collapsed.delete(folder.id); children.style.display = ''; caret.textContent = '▾'; }
      else { collapsed.add(folder.id); children.style.display = 'none'; caret.textContent = '▸'; }
    };
    row.addEventListener('click', (e) => { if (e.target === kebab || label.isContentEditable) return; toggle(); });
    label.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      editableLabel(label, folder.name, (val) => handlers.renameFolder(folder.id, val));
    });
    kebab.addEventListener('click', (e) => {
      e.stopPropagation();
      popupMenu(kebab, [
        { label: 'Rename', onClick: () => editableLabel(label, folder.name, (v) => handlers.renameFolder(folder.id, v)) },
        { label: 'New subfolder', onClick: () => handlers.addFolder(folder.id) },
        { label: 'Move to top level', onClick: () => handlers.moveFolder(folder.id, null) },
        { label: 'Delete folder', danger: true, onClick: () => handlers.removeFolder(folder) },
      ]);
    });
    dropToFolder(row, folder.id);

    (foldersByParent.get(folder.id) || []).forEach((c) => children.appendChild(folderNode(c, depth + 1)));
    (filesByFolder.get(folder.id) || []).forEach((f) => children.appendChild(fileRow(f)));

    wrap.append(row, children);
    return wrap;
  }

  (foldersByParent.get(0) || []).forEach((f) => container.appendChild(folderNode(f, 0)));
  (filesByFolder.get(0) || []).forEach((f) => container.appendChild(fileRow(f)));

  // Dropping on empty drawer space moves a file to the top level.
  container.ondragover = (e) => { e.preventDefault(); };
  container.ondrop = (e) => {
    const id = +e.dataTransfer.getData('text/file-id');
    if (id) handlers.moveFile(id, null);
  };
}
