// Shared color picker component for boards and dashboard notes.
// Renders 10 preset swatches plus a native color mixer (input type=color).

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
      mixer.value = value || (CARD_COLORS[0]);
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

  const mixerWrap = document.createElement('div');
  mixerWrap.className = 'color-mixer';
  const mixer = document.createElement('input');
  mixer.type = 'color';
  mixer.className = 'color-mixer-input';
  mixer.value = currentColor || CARD_COLORS[0];
  mixer.setAttribute('aria-label', 'Color mixer');
  const hexLabel = document.createElement('span');
  hexLabel.className = 'color-mixer-hex';
  hexLabel.textContent = currentColor || '';

  mixer.addEventListener('input', () => {
    const v = mixer.value;
    hexLabel.textContent = v;
    select(v, 'mixer');
  });

  mixerWrap.appendChild(mixer);
  mixerWrap.appendChild(hexLabel);
  container.appendChild(mixerWrap);

  // expose for outside updates if needed
  container.__colorPicker = { getValue: () => selected, setValue: (v) => select(v, 'swatch') };
}
