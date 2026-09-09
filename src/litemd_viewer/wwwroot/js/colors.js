// Shared color palette for boards and dashboard notes.

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
  container.className = 'color-swatches';
  const make = (value, label, isDefault) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'color-swatch' + (isDefault ? ' color-swatch-default' : '');
    btn.title = label;
    btn.setAttribute('aria-label', label);
    if (!isDefault) btn.style.backgroundColor = value;
    if ((isDefault && !currentColor) || (!isDefault && currentColor === value)) {
      btn.classList.add('selected');
    }
    btn.addEventListener('click', () => {
      onSelect(isDefault ? null : value);
      Array.from(container.children).forEach((c) => c.classList.remove('selected'));
      btn.classList.add('selected');
    });
    container.appendChild(btn);
  };
  make(null, 'Default color', true);
  for (const c of CARD_COLORS) make(c, c, false);
}
