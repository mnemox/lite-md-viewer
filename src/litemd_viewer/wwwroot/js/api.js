// Thin fetch wrapper around the JSON API. A FormData body is sent as-is (multipart).
async function req(method, url, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) {
    opt.body = body;
  } else if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(url, opt);
  const txt = await res.text();
  let data = null;
  if (txt) { try { data = JSON.parse(txt); } catch { data = txt; } }
  if (!res.ok) {
    const msg = (data && data.error) ? data.error
      : (typeof data === 'string' && data) ? data
      : `${res.status} ${res.statusText}`;
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  tree: () => req('GET', '/api/tree'),
  browse: (path, kind) => req('GET', '/api/browse?path=' + encodeURIComponent(path ?? '') + (kind ? '&kind=' + encodeURIComponent(kind) : '')),

  addFile: (path, folderId) => req('POST', '/api/files', { path, folderId: folderId ?? null }),
  addFolderFiles: (path, folderId) => req('POST', '/api/files/folder', { path, folderId: folderId ?? null }),
  newFile: (dir, name, folderId) => req('POST', '/api/files/new', { dir, name, folderId: folderId ?? null }),
  patchFile: (id, patch) => req('PATCH', `/api/files/${id}`, patch),
  moveFileToDisk: (id, dir, newName) => req('POST', `/api/files/${id}/move`, { dir, newName: newName ?? null }),
  removeFile: (id) => req('DELETE', `/api/files/${id}`),
  deleteDisk: (id) => req('DELETE', `/api/files/${id}/disk`),
  content: (id) => req('GET', `/api/files/${id}/content`),
  details: (id) => req('GET', `/api/files/${id}/details`),
  saveContent: (id, text) => req('PUT', `/api/files/${id}/content`, { text }),
  recreateFile: (id) => req('POST', `/api/files/${id}/recreate`),

  // Relations (graph + companions)
  graph: (id) => req('GET', `/api/files/${id}/graph`),
  addRelation: (id, otherId, kind) => req('POST', `/api/files/${id}/relations`, { otherId, kind }),
  removeRelation: (id, otherId, kind) =>
    req('DELETE', `/api/files/${id}/relations?otherId=${otherId}&kind=${encodeURIComponent(kind)}`),
  removeFromGraph: (id) => req('DELETE', `/api/files/${id}/graph`),

  // Color maps (imported JSON schemas that recolor node borders)
  colorMaps: (id) => req('GET', `/api/files/${id}/colormaps`),
  addColorMap: (id, path) => req('POST', `/api/files/${id}/colormaps`, { path }),
  removeColorMap: (id, mapId) => req('DELETE', `/api/files/${id}/colormaps/${mapId}`),

  // Attachments (graph exports, uploads, file references)
  attachments: (id) => req('GET', `/api/files/${id}/attachments`),
  export: (id, indexHtml) => req('POST', `/api/files/${id}/export`, { indexHtml }),
  addAttachmentReference: (id, path) => req('POST', `/api/files/${id}/attachments/reference`, { path }),
  uploadAttachment: (id, file) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    return req('POST', `/api/files/${id}/attachments/upload`, fd);
  },
  deleteAttachment: (attId) => req('DELETE', `/api/attachments/${attId}`),
  attachmentUrl: (attId) => `/api/attachments/${attId}/download`,

  folders: () => req('GET', '/api/folders'),
  addFolder: (name, parentId) => req('POST', '/api/folders', { name, parentId: parentId ?? null }),
  patchFolder: (id, patch) => req('PATCH', `/api/folders/${id}`, patch),
  removeFolder: (id) => req('DELETE', `/api/folders/${id}`),

  // Passage search over the vector index ("hybrid" fuses nearest-neighbour with FTS).
  // With a fileId the search is confined to that document and hits are its sections.
  search: (q, k = 10, mode, fileId) => req('GET', '/api/search?q=' + encodeURIComponent(q)
    + '&k=' + encodeURIComponent(k)
    + (mode ? '&mode=' + encodeURIComponent(mode) : '')
    + (fileId == null ? '' : '&fileId=' + encodeURIComponent(fileId))),
  searchStatus: () => req('GET', '/api/search/status'),

  settings: () => req('GET', '/api/settings'),
  setSetting: (key, value) => req('PUT', `/api/settings/${encodeURIComponent(key)}`, { value }),

  // Dashboard sticky notes
  dashboardNotes: () => req('GET', '/api/dashboard/notes'),
  dashboardNote: (id) => req('GET', `/api/dashboard/notes/${id}`),
  createNote: (body) => req('POST', '/api/dashboard/notes', body),
  patchNote: (id, patch) => req('PATCH', `/api/dashboard/notes/${id}`, patch),
  deleteNote: (id) => req('DELETE', `/api/dashboard/notes/${id}`),

  // Per-document notes (file page panel)
  docNotes: (fileId) => req('GET', `/api/files/${fileId}/notes`),
  docNote: (fileId, noteId) => req('GET', `/api/files/${fileId}/notes/${noteId}`),
  createDocNote: (fileId, text) => req('POST', `/api/files/${fileId}/notes`, { text }),
  patchDocNote: (fileId, noteId, patch) => req('PATCH', `/api/files/${fileId}/notes/${noteId}`, patch),
  deleteDocNote: (fileId, noteId) => req('DELETE', `/api/files/${fileId}/notes/${noteId}`),

  // Note ↔ highlighted text references
  noteRefs: (fileId, noteId) => req('GET', `/api/files/${fileId}/notes/${noteId}/references`),
  addNoteRef: (fileId, noteId, start, length, text) =>
    req('POST', `/api/files/${fileId}/notes/${noteId}/references`, { startOffset: start, length, text }),
  deleteNoteRef: (fileId, noteId, refId) =>
    req('DELETE', `/api/files/${fileId}/notes/${noteId}/references/${refId}`),

  // Document-note clusters on the dashboard
  documentNoteGroups: () => req('GET', '/api/dashboard/document-notes'),
  patchDocNoteGroup: (fileId, patch) => req('PATCH', `/api/dashboard/document-notes/${fileId}`, patch),

  // Boards screen
  boards: () => req('GET', '/api/boards'),
  board: (id) => req('GET', `/api/boards/${id}`),
  createBoard: (body) => req('POST', '/api/boards', body),
  patchBoard: (id, patch) => req('PATCH', `/api/boards/${id}`, patch),
  deleteBoard: (id) => req('DELETE', `/api/boards/${id}`),

  // A board's lists (columns) and their cards, à la Trello
  boardLists: (boardId) => req('GET', `/api/boards/${boardId}/lists`),
  createList: (boardId, name) => req('POST', `/api/boards/${boardId}/lists`, { name }),
  patchList: (boardId, listId, patch) => req('PATCH', `/api/boards/${boardId}/lists/${listId}`, patch),
  deleteList: (boardId, listId) => req('DELETE', `/api/boards/${boardId}/lists/${listId}`),
  reorderLists: (boardId, orderedIds) => req('PUT', `/api/boards/${boardId}/lists/reorder`, { orderedIds }),
  createCard: (boardId, listId, text) => req('POST', `/api/boards/${boardId}/lists/${listId}/cards`, { text }),
  patchCard: (boardId, listId, cardId, patch) => req('PATCH', `/api/boards/${boardId}/lists/${listId}/cards/${cardId}`, patch),
  deleteCard: (boardId, listId, cardId) => req('DELETE', `/api/boards/${boardId}/lists/${listId}/cards/${cardId}`),
  reorderCards: (boardId, listId, orderedIds) => req('PUT', `/api/boards/${boardId}/lists/${listId}/cards/reorder`, { orderedIds }),

  // Local analysis: chat over one open document, answered by a local Ollama model.
  aiStatus: () => req('GET', '/api/ai/status'),
  aiChat: (fileId) => req('GET', `/api/files/${fileId}/chat`),
  clearAiChat: (fileId) => req('DELETE', `/api/files/${fileId}/chat`),
  // Streamed as NDJSON, so it bypasses req() (which buffers the whole body) and returns the
  // raw Response for the caller to read with a reader.
  askStream: (fileId, question) => fetch(`/api/files/${fileId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  }),
};
