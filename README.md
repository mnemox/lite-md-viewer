# lite-md-viewer

A small, self-hosted local web app to **view, edit, and manage Markdown files**
(including **Mermaid** diagrams). Add files from anywhere on disk, organize them
into folders, and edit them in place.

- Backend: **ASP.NET Core (.NET 8)**, minimal APIs, SQLite (EF Core).
- Frontend: vanilla HTML/CSS/JS (no framework), with `markdown-it` + `mermaid` +
  `highlight.js` + `DOMPurify` vendored locally (works fully offline).
- Runs over **HTTP on loopback only** (`http://127.0.0.1:5099`) — no HTTPS, no auth,
  single local user.

## Requirements

- **.NET 8 SDK** (`dotnet --list-sdks` should show an `8.x`).

## Run

```sh
cd src/LiteMdViewer
dotnet run
```

Then open <http://127.0.0.1:5099>. (A `global.json` pins the build to the .NET 8 SDK.)

## Features

- **Add a file from anywhere on disk** — the “+ Add file” button opens a server-side
  browser (drives → folders → `.md`/`.markdown` files). The browser sandbox can't
  hand a web page a real absolute path, so selection happens server-side; you can
  also paste a full path.
- **View** rendered Markdown + Mermaid diagrams, GitHub-style tables, and syntax-
  highlighted code.
- **Edit** in-app (split source/preview with live Mermaid preview) and **Save** back
  to disk.
- **Organize** managed files into **folders** (arbitrary depth) with **editable
  titles** (the on-disk filename is never changed). Drag a file onto a folder, or
  use the “⋯” menu / double-click to rename.
- **Hidden side drawer** — opens on hover at the top-left edge, or click the ☰ button
  to pin it open.
- **Dark mode** toggle (persisted); Mermaid and code themes follow it.

## Project layout

```
src/LiteMdViewer/
  Program.cs              host, DI, loopback bind, endpoint mapping
  Data/AppDbContext.cs    EF Core (Folders, ManagedFiles, Settings)
  Models/                 entities + DTOs
  Services/
    FsBrowser.cs          server-side drive/folder/.md enumeration
  Endpoints/              minimal-API endpoint groups
  wwwroot/                static frontend + vendored JS libs
```

## API (loopback only)

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/tree` | folders + files (with missing-on-disk flag) |
| GET | `/api/browse?path=` | list drives / a folder's subfolders + `.md` files |
| POST | `/api/files` | add a file by absolute path |
| PATCH | `/api/files/{id}` | rename title / move folder / reorder |
| DELETE | `/api/files/{id}` | remove from management (keeps the file) |
| DELETE | `/api/files/{id}/disk` | delete the file from disk |
| GET/PUT | `/api/files/{id}/content` | read / save markdown text |
| GET/POST/PATCH/DELETE | `/api/folders[...]` | folder CRUD |
| GET/PUT | `/api/settings[...]` | theme & startup flags |

## Updating the vendored libraries

The browser libs are committed under `src/LiteMdViewer/wwwroot/vendor/`. To refresh:

```sh
curl -fsSL https://cdn.jsdelivr.net/npm/markdown-it@14/dist/markdown-it.min.js -o markdown-it.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js          -o purify.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js           -o mermaid.min.js
curl -fsSL https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/highlight.min.js          -o highlight.min.js
curl -fsSL https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/github.min.css      -o highlight-github.min.css
curl -fsSL https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/github-dark.min.css -o highlight-github-dark.min.css
```
