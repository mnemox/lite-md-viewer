// A tiny, centralized client-side router. Every top-level view the app can show --
// welcome, notes, or a file in view/edit mode -- is derived from one parsed route
// object, and every navigation (tree clicks, search results, tab toggles, ...) goes
// through navigate() instead of poking history/location directly. That keeps the URL,
// the browser back/forward stack, and what's on screen in permanent agreement.
//
// URL shapes:
//   /files/<id>       file, view mode
//   /files/<id>/edit  file, edit mode
//   /notes            notes/dashboard
//   /                 welcome
//
// Modals that overlay a route (e.g. the relations graph in relations.js) manage their
// own history entry on top of whatever route pushed it; this router only reacts when
// the parsed route itself actually changes, so closing such a modal via Back does not
// re-render the underlying route.

let onRoute = null;
let current = null;

function parseRoute(pathname) {
  const file = /^\/files\/(\d+)(\/edit)?\/?$/.exec(pathname);
  if (file) return { name: 'file', fileId: Number(file[1]), mode: file[2] ? 'edit' : 'view' };
  if (/^\/notes\/?$/.test(pathname)) return { name: 'notes' };
  return { name: 'welcome' };
}

function sameRoute(a, b) {
  if (!a || !b || a.name !== b.name) return false;
  return a.name !== 'file' || (a.fileId === b.fileId && a.mode === b.mode);
}

function toPath(route) {
  if (route.name === 'file') return `/files/${route.fileId}` + (route.mode === 'edit' ? '/edit' : '');
  if (route.name === 'notes') return '/notes';
  return '/';
}

// Register the single handler that renders whatever route is current, and start
// reacting to browser back/forward. Call startRouter() separately once the app is
// ready to actually render the initial route.
export function initRouter(handler) {
  onRoute = handler;
  window.addEventListener('popstate', () => {
    const route = parseRoute(location.pathname);
    // Only the modal hash changed (e.g. the relations graph closing) -- nothing for the
    // underlying route to re-render.
    if (sameRoute(route, current)) { current = route; return; }
    dispatch(route);
  });
}

// The one path every navigation in the app takes. Updates the URL -- pushing a new
// history entry by default, or replacing the current one in place (e.g. a view/edit
// toggle on the same file) -- then renders the route. Pushing a route identical to the
// current one replaces instead, so rapid duplicate navigations don't spam history.
export function navigate(route, { push = true } = {}) {
  const url = toPath(route);
  if (push && !sameRoute(route, current)) history.pushState(route, '', url);
  else history.replaceState(route, '', url);
  dispatch(route);
}

// Render whatever the current URL says, without touching history -- call once at
// startup so a hard refresh or a shared link lands on the right view.
export function startRouter() {
  dispatch(parseRoute(location.pathname));
}

export function currentRoute() { return current; }

function dispatch(route) {
  current = route;
  onRoute(route);
}
