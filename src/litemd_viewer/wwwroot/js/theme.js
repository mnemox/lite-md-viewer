import { setMermaidTheme } from './render.js';

export function currentTheme() {
  return document.documentElement.getAttribute('data-theme') || 'light';
}

export function applyTheme(theme) {
  const t = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', t);
  const light = document.getElementById('hljs-light');
  const dark = document.getElementById('hljs-dark');
  if (light) light.disabled = (t === 'dark');
  if (dark) dark.disabled = (t !== 'dark');
  const btn = document.getElementById('themeBtn');
  if (btn) btn.textContent = (t === 'dark') ? '☀️' : '🌙';
  setMermaidTheme(t);
  try { localStorage.setItem('mdm-theme', t); } catch { /* ignore */ }
}
