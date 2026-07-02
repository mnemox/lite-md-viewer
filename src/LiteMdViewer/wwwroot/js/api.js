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
  removeFile: (id) => req('DELETE', `/api/files/${id}`),
  deleteDisk: (id) => req('DELETE', `/api/files/${id}/disk`),
  content: (id) => req('GET', `/api/files/${id}/content`),
  details: (id) => req('GET', `/api/files/${id}/details`),
  saveContent: (id, text) => req('PUT', `/api/files/${id}/content`, { text }),

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

  settings: () => req('GET', '/api/settings'),
  setSetting: (key, value) => req('PUT', `/api/settings/${encodeURIComponent(key)}`, { value }),
};
