// Shared color picker component for boards and dashboard notes.
// Preset swatches plus a custom mixer popover (saturation/value area, hue
// slider, RGB fields) with Done/Cancel *inside* the popover. Mixing only
// previews; Done commits the choice via onSelect.

export const CARD_COLORS = [
  '#f87171', '#fb923c', '#fbbf24', '#a3e635', '#34d399',
  '#22d3ee', '#60a5fa', '#a78bfa', '#e879f9', '#fb7185',
];

export function contrastColor(hex) {
  const c = (hex || '').replace('#', '');
  const r = parseInt(c.slice(0, 2), 16) || 0;
  const g = parseInt(c.slice(2, 4), 16) || 0;
  const b = parseInt(c.slice(4, 6), 16) || 0;
  const y = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return y > 0.5 ? '#111111' : '#ffffff';
}

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

function clampNum(n, lo, hi) {
  n = Number(n);
  if (!Number.isFinite(n)) return lo;
  return Math.min(hi, Math.max(lo, n));
}

function hexToRgb(hex) {
  const c = hex.replace('#', '');
  return [
    parseInt(c.slice(0, 2), 16) || 0,
    parseInt(c.slice(2, 4), 16) || 0,
    parseInt(c.slice(4, 6), 16) || 0,
  ];
}

function rgbToHex(r, g, b) {
  const h = (n) => clampNum(Math.round(n), 0, 255).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const d = max - min;
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return [h, max === 0 ? 0 : d / max, max];
}

function hsvToRgb(h, s, v) {
  h = ((h % 360) + 360) % 360;
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  let rp = 0, gp = 0, bp = 0;
  if (h < 60) [rp, gp, bp] = [c, x, 0];
  else if (h < 120) [rp, gp, bp] = [x, c, 0];
  else if (h < 180) [rp, gp, bp] = [0, c, x];
  else if (h < 240) [rp, gp, bp] = [0, x, c];
  else if (h < 300) [rp, gp, bp] = [x, 0, c];
  else [rp, gp, bp] = [c, 0, x];
  return [
    Math.round((rp + m) * 255),
    Math.round((gp + m) * 255),
    Math.round((bp + m) * 255),
  ];
}

export function renderColorSwatches(container, currentColor, onSelect) {
  container.innerHTML = '';
  container.className = 'color-picker';

  const swatchWrap = document.createElement('div');
  swatchWrap.className = 'color-swatches';

  let selected = currentColor;

  const select = (value, source) => {
    selected = value;
    onSelect(value);
    if (source !== 'mixer') {
      previewBtn.style.backgroundColor = value || '';
      previewBtn.classList.toggle('empty', !value);
    }
    updateSelectionUI();
  };

  const updateSelectionUI = () => {
    Array.from(swatchWrap.children).forEach((c) => c.classList.remove('selected'));
    const match = [...swatchWrap.children].find((c) => !c.classList.contains('color-swatch-default') && c.dataset.color === selected);
    if (match) match.classList.add('selected');
    else if (!selected) swatchWrap.querySelector('.color-swatch-default')?.classList.add('selected');
    if (hexLabel) hexLabel.textContent = selected || '';
  };

  const make = (value, label, isDefault) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'color-swatch' + (isDefault ? ' color-swatch-default' : '');
    btn.title = label;
    btn.setAttribute('aria-label', label);
    if (!isDefault) {
      btn.style.backgroundColor = value;
      btn.dataset.color = value;
    }
    if ((isDefault && !currentColor) || (!isDefault && currentColor === value)) {
      btn.classList.add('selected');
    }
    btn.addEventListener('click', () => select(isDefault ? null : value, 'swatch'));
    swatchWrap.appendChild(btn);
  };

  make(null, 'Default color', true);
  for (const c of CARD_COLORS) make(c, c, false);
  container.appendChild(swatchWrap);

  // ---- custom mixer: preview square opens a popover with Done inside ----
  const mixerWrap = document.createElement('div');
  mixerWrap.className = 'color-mixer';

  const previewBtn = document.createElement('button');
  previewBtn.type = 'button';
  previewBtn.className = 'color-mixer-input' + (currentColor ? '' : ' empty');
  previewBtn.title = 'Custom color…';
  previewBtn.setAttribute('aria-label', 'Custom color mixer');
  previewBtn.setAttribute('aria-haspopup', 'dialog');
  previewBtn.setAttribute('aria-expanded', 'false');
  if (currentColor) previewBtn.style.backgroundColor = currentColor;

  const hexLabel = document.createElement('span');
  hexLabel.className = 'color-mixer-hex';
  hexLabel.textContent = currentColor || '';

  // Pending (uncommitted) color while the popover is open.
  let ph = 0, ps = 1, pv = 1;

  const pop = document.createElement('div');
  pop.className = 'color-pop hidden';
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', 'Custom color mixer');
  pop.innerHTML = `
    <div class="color-sv" title="Saturation / brightness">
      <div class="color-sv-marker"></div>
    </div>
    <label class="color-hue-row">Hue
      <input class="color-hue" type="range" min="0" max="360" step="1" aria-label="Hue" />
    </label>
    <div class="color-rgb">
      <label>R<input class="input color-rgb-input" data-ch="0" type="number" min="0" max="255" step="1" /></label>
      <label>G<input class="input color-rgb-input" data-ch="1" type="number" min="0" max="255" step="1" /></label>
      <label>B<input class="input color-rgb-input" data-ch="2" type="number" min="0" max="255" step="1" /></label>
    </div>
    <div class="color-pop-foot">
      <button type="button" class="btn" data-act="cancel">Cancel</button>
      <button type="button" class="btn primary" data-act="done">Done</button>
    </div>`;

  const svBox = pop.querySelector('.color-sv');
  const svMarker = pop.querySelector('.color-sv-marker');
  const hueInput = pop.querySelector('.color-hue');
  const rgbInputs = [...pop.querySelectorAll('.color-rgb-input')];

  const pendingHex = () => rgbToHex(...hsvToRgb(ph, ps, pv));

  function paintPending() {
    const [r, g, b] = hsvToRgb(ph, ps, pv);
    const hex = rgbToHex(r, g, b);
    svBox.style.background =
      `linear-gradient(to top, #000 0%, transparent 100%), ` +
      `linear-gradient(to right, #fff 0%, transparent 100%), ` +
      `hsl(${Math.round(ph)} 100% 50%)`;
    svMarker.style.left = `${ps * 100}%`;
    svMarker.style.top = `${(1 - pv) * 100}%`;
    if (document.activeElement !== hueInput) hueInput.value = String(Math.round(ph));
    rgbInputs.forEach((inp, i) => {
      if (document.activeElement !== inp) inp.value = String([r, g, b][i]);
    });
    previewBtn.style.backgroundColor = hex;
    previewBtn.classList.remove('empty');
    hexLabel.textContent = hex;
  }

  function seedPending() {
    const seed = HEX_RE.test(selected || '') ? selected : (HEX_RE.test(currentColor || '') ? currentColor : CARD_COLORS[0]);
    [ph, ps, pv] = rgbToHsv(...hexToRgb(seed));
  }

  function setOpen(on) {
    const willOpen = on ?? pop.classList.contains('hidden');
    if (willOpen) {
      seedPending();
      paintPending();
      // Portal to <body>: the modal card uses overflow:hidden, which would
      // clip an in-flow popover (only the top peeked out). Fixed positioning
      // relative to the viewport keeps the whole mixer visible.
      document.body.appendChild(pop);
      placePop();
      pop.classList.remove('hidden');
      previewBtn.setAttribute('aria-expanded', 'true');
      document.addEventListener('pointerdown', onOutside, true);
      document.addEventListener('keydown', onKey);
      window.addEventListener('resize', onResize);
    } else {
      pop.classList.add('hidden');
      previewBtn.setAttribute('aria-expanded', 'false');
      document.removeEventListener('pointerdown', onOutside, true);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onResize);
      // hand the node back to the picker so re-renders don't orphan it
      mixerWrap.appendChild(pop);
      // revert preview of uncommitted mixing
      previewBtn.style.backgroundColor = selected || '';
      previewBtn.classList.toggle('empty', !selected);
      updateSelectionUI();
    }
  }

  function placePop() {
    // Measure while hidden-offscreen, then pin: below the square, flipped
    // above when there is no room, clamped horizontally to the viewport.
    pop.style.visibility = 'hidden';
    pop.style.left = '0';
    pop.style.top = '0';
    pop.classList.remove('hidden');
    const r = previewBtn.getBoundingClientRect();
    const pw = pop.offsetWidth || 232;
    const ph = pop.offsetHeight || 300;
    let left = Math.min(Math.max(8, r.left), Math.max(8, window.innerWidth - pw - 8));
    let top = r.bottom + 8;
    if (top + ph > window.innerHeight - 8) {
      top = Math.max(8, r.top - 8 - ph);
    }
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
    pop.style.visibility = '';
  }

  function onOutside(e) {
    if (!mixerWrap.contains(e.target) && !pop.contains(e.target)) setOpen(false);
  }

  function onResize() {
    setOpen(false);
  }

  function onKey(e) {
    if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); }
  }

  previewBtn.addEventListener('click', () => setOpen());

  // SV drag
  let svDrag = false;
  const svSet = (e) => {
    const rect = svBox.getBoundingClientRect();
    ps = clampNum((e.clientX - rect.left) / rect.width, 0, 1);
    pv = 1 - clampNum((e.clientY - rect.top) / rect.height, 0, 1);
    paintPending();
  };
  svBox.addEventListener('pointerdown', (e) => {
    svDrag = true;
    svBox.setPointerCapture?.(e.pointerId);
    svSet(e);
  });
  svBox.addEventListener('pointermove', (e) => { if (svDrag) svSet(e); });
  svBox.addEventListener('pointerup', () => { svDrag = false; });
  svBox.addEventListener('pointercancel', () => { svDrag = false; });

  hueInput.addEventListener('input', () => {
    ph = clampNum(Number(hueInput.value), 0, 360);
    paintPending();
  });

  rgbInputs.forEach((inp) => {
    inp.addEventListener('input', () => {
      const [r, g, b] = rgbInputs.map((el) => clampNum(el.value, 0, 255));
      [ph, ps, pv] = rgbToHsv(r, g, b);
      paintPending();
    });
  });

  pop.addEventListener('click', (e) => {
    const act = e.target.closest('[data-act]')?.dataset.act;
    if (act === 'done') {
      select(pendingHex(), 'mixer');
      setOpen(false);
    } else if (act === 'cancel') {
      setOpen(false);
    }
  });

  mixerWrap.appendChild(previewBtn);
  mixerWrap.appendChild(hexLabel);
  mixerWrap.appendChild(pop);
  container.appendChild(mixerWrap);

  // expose for outside updates if needed
  container.__colorPicker = { getValue: () => selected, setValue: (v) => select(v, 'swatch') };
}
