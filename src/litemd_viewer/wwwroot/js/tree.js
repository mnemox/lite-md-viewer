// Renders the folder/file tree in the drawer and wires its interactions:
// open, inline rename, drag-to-folder, and a per-row action menu.

const collapsed = new Set();      // folder ids the user collapsed (expanded by default)

export function popupMenu(anchor, items) {
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
  // Flip above the anchor when there isn't enough room below (avoids running off the bottom edge).
  const gap = 4;
  const menuH = menu.offsetHeight;
  const spaceBelow = window.innerHeight - r.bottom;
  let top = (spaceBelow >= menuH + gap || r.top < menuH + gap)
    ? r.bottom + gap            // open downward
    : r.top - menuH - gap;      // open upward
  top = Math.max(gap, Math.min(top, window.innerHeight - menuH - gap));
  menu.style.insetBlockStart = top + 'px';
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

// `options.pick` renders the tree as a document picker: the folder hierarchy is
// identical to the drawer, but file rows select (handlers.pickFile(id)) instead of
// opening, and the management affordances (drag, kebab menus, inline rename, folder
// drop targets) are left off. `options.excludeFileId` hides one file from the picker.
export function renderTree(container, data, handlers, activeFileId, options = {}) {
  const pick = options.pick === true;
  const excludeId = options.excludeFileId ?? null;

  const foldersByParent = new Map();
  const filesByFolder = new Map();
  for (const f of data.folders) {
    const k = f.parentId ?? 0;
    (foldersByParent.get(k) || foldersByParent.set(k, []).get(k)).push(f);
  }
  for (const f of data.files) {
    if (pick && f.id === excludeId) continue;   // never offer the document we're linking from
    const k = f.folderId ?? 0;
    (filesByFolder.get(k) || filesByFolder.set(k, []).get(k)).push(f);
  }

  container.innerHTML = '';
  const hasFiles = pick ? filesByFolder.size > 0 : data.files.length > 0;
  if (!data.folders.length && !hasFiles) {
    const empty = document.createElement('div');
    empty.className = 'tree-empty';
    empty.textContent = pick ? 'No other documents.' : 'No files yet. Click “+ Add file” to manage one.';
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

    // Files have no caret (nothing to expand): the icon takes the caret column so a
    // file's icon lines up with the arrows of folders at the same level.
    const icon = document.createElement('span'); icon.className = 'icon file-icon'; icon.textContent = '📄';
    const label = document.createElement('span'); label.className = 'label'; label.textContent = file.title; label.dir = 'auto';

    if (file.missing) { label.style.color = 'var(--danger)'; label.title = 'File is missing on disk'; }

    // Picker mode: a plain selectable row — clicking it picks the document.
    if (pick) {
      row.append(icon, label);
      row.addEventListener('click', () => handlers.pickFile(file.id));
      return row;
    }

    row.draggable = true;
    row.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/file-id', String(file.id)));
    const kebab = document.createElement('span'); kebab.className = 'kebab'; kebab.textContent = '⋯';

    row.append(icon, label, kebab);

    row.addEventListener('click', (e) => {
      if (e.target === kebab || label.isContentEditable) return;
      handlers.openFile(file.id);
    });
    label.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      editableLabel(label, file.title, (val) => handlers.renameFile(file.id, val));
    });
    kebab.addEventListener('click', (e) => {
      e.stopPropagation();
      popupMenu(kebab, [
        { label: 'Rename', onClick: () => editableLabel(label, file.title, (v) => handlers.renameFile(file.id, v)) },
        { label: 'Move to top level', onClick: () => handlers.moveFile(file.id, null) },
        { label: 'Move to folder…', onClick: () => handlers.moveFileToDisk(file.id) },
        { label: 'Remove from list', onClick: () => handlers.removeFromList(file) },
        { label: 'Delete from disk…', danger: true, onClick: () => handlers.deleteDisk(file) },
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

    const children = document.createElement('div');
    children.className = 'children';
    children.style.display = isOpen ? '' : 'none';

    const toggle = () => {
      if (collapsed.has(folder.id)) { collapsed.delete(folder.id); children.style.display = ''; caret.textContent = '▾'; }
      else { collapsed.add(folder.id); children.style.display = 'none'; caret.textContent = '▸'; }
    };

    if (pick) {
      // Picker mode: folders only expand/collapse — no kebab, rename, or drag-drop.
      row.append(caret, icon, label);
      row.addEventListener('click', toggle);
    } else {
      const kebab = document.createElement('span'); kebab.className = 'kebab'; kebab.textContent = '⋯';
      row.append(caret, icon, label, kebab);
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
    }

    (foldersByParent.get(folder.id) || []).forEach((c) => children.appendChild(folderNode(c, depth + 1)));
    (filesByFolder.get(folder.id) || []).forEach((f) => children.appendChild(fileRow(f)));

    wrap.append(row, children);
    return wrap;
  }

  (foldersByParent.get(0) || []).forEach((f) => container.appendChild(folderNode(f, 0)));
  (filesByFolder.get(0) || []).forEach((f) => container.appendChild(fileRow(f)));

  if (pick) return;

  // Dropping on empty drawer space moves a file to the top level.
  container.ondragover = (e) => { e.preventDefault(); };
  container.ondrop = (e) => {
    const id = +e.dataTransfer.getData('text/file-id');
    if (id) handlers.moveFile(id, null);
  };
}
