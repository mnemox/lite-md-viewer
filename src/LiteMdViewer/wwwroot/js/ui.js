// Small UI helpers: toasts and a styled confirm dialog.

export function toast(message, kind = 'info', ms = 3400) {
  const wrap = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast' + (kind === 'error' ? ' error' : kind === 'ok' ? ' ok' : '');
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .2s';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 220);
  }, ms);
}

export function confirmDialog(message, { okLabel = 'OK', danger = false } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'modal';
    overlay.innerHTML = `
      <div class="modal-card" style="width:min(420px,96vw)">
        <div class="modal-head"><strong>Confirm</strong></div>
        <p style="padding:16px 18px;margin:0"></p>
        <div class="modal-foot" style="justify-content:flex-end">
          <button class="btn" data-act="cancel">Cancel</button>
          <button class="btn ${danger ? 'danger' : 'primary'}" data-act="ok"></button>
        </div>
      </div>`;
    overlay.querySelector('p').textContent = message;
    overlay.querySelector('[data-act="ok"]').textContent = okLabel;
    const close = (val) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(val); };
    const onKey = (e) => { if (e.key === 'Escape') close(false); };
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close(false);
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (act === 'ok') close(true);
      if (act === 'cancel') close(false);
    });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    overlay.querySelector('[data-act="ok"]').focus();
  });
}

// Read-only file-details modal: shows the title, full path, and the file's
// on-disk created / updated timestamps (formatted in local time).
export function detailsDialog(d) {
  const overlay = document.createElement('div');
  overlay.className = 'modal';
  const fmt = (iso) => {
    if (!iso) return null;
    const date = new Date(iso);
    return isNaN(date) ? null : date.toLocaleString();
  };
  overlay.innerHTML = `
    <div class="modal-card" style="width:min(560px,96vw)">
      <div class="modal-head">
        <strong>File details</strong>
        <button class="icon-btn" data-act="close" aria-label="Close">✕</button>
      </div>
      <dl class="details">
        <dt>Name</dt><dd class="d-name" dir="auto"></dd>
        <dt>Path</dt><dd class="d-path mono" dir="auto"></dd>
        <dt>Created</dt><dd class="d-created"></dd>
        <dt>Updated</dt><dd class="d-updated"></dd>
      </dl>
      <div class="modal-foot" style="justify-content:flex-end">
        <button class="btn" data-act="copy">Copy path</button>
        <button class="btn primary" data-act="close">Close</button>
      </div>
    </div>`;
  overlay.querySelector('.d-name').textContent = d.title || '—';
  overlay.querySelector('.d-path').textContent = d.fullPath || '—';
  const created = overlay.querySelector('.d-created');
  const updated = overlay.querySelector('.d-updated');
  if (!d.exists) {
    for (const el of [created, updated]) { el.textContent = 'file missing on disk'; el.classList.add('muted'); }
  } else {
    created.textContent = fmt(d.createdUtc) ?? '—';
    updated.textContent = fmt(d.modifiedUtc) ?? '—';
  }
  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) return close();
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'close') close();
    if (act === 'copy') {
      navigator.clipboard?.writeText(d.fullPath || '')
        .then(() => toast('Path copied', 'ok'))
        .catch(() => toast('Could not copy path', 'error'));
    }
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
  overlay.querySelector('[data-act="close"]').focus();
}

// Styled single-line text prompt. Resolves to the trimmed value, or null if
// cancelled / left empty.
export function promptDialog(title, { okLabel = 'OK', placeholder = '', value = '' } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'modal';
    overlay.innerHTML = `
      <div class="modal-card" style="width:min(420px,96vw)">
        <div class="modal-head"><strong></strong></div>
        <div style="padding:16px 18px">
          <input class="input" type="text" dir="auto" />
        </div>
        <div class="modal-foot" style="justify-content:flex-end">
          <button class="btn" data-act="cancel">Cancel</button>
          <button class="btn primary" data-act="ok"></button>
        </div>
      </div>`;
    overlay.querySelector('.modal-head strong').textContent = title;
    overlay.querySelector('[data-act="ok"]').textContent = okLabel;
    const input = overlay.querySelector('input');
    input.placeholder = placeholder;
    input.value = value;
    const close = (val) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(val); };
    const submit = () => close(input.value.trim() || null);
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(null); } };
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) return close(null);
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (act === 'ok') submit();
      if (act === 'cancel') close(null);
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    input.focus();
  });
}
