# lite-md-viewer

A small, self-hosted local web app to **view, edit, and manage Markdown files**
(including **Mermaid** diagrams). Add files from anywhere on disk, organize them
into folders, and edit them in place.

- Backend: **Python / FastAPI**, SQLAlchemy 2.0 ORM, SQLite (WAL).
- Frontend: vanilla HTML/CSS/JS (no framework), with `markdown-it` + `mermaid` +
  `highlight.js` + `DOMPurify` + `three.js` (3D relations view) vendored locally
  (works fully offline).
- Semantic search: **fastembed** (ONNX Runtime, CPU-only) over a local
  `hnswlib` + SQLite FTS5 vector store — no network calls, no PyTorch.
- Runs over **HTTP on loopback only** (`http://127.0.0.1:5099`) — no HTTPS, no auth,
  single local user.

## Requirements

- **Python 3.11+** on `PATH`

## Run

```sh
run.bat
```

That is all that is needed on a fresh clone: it offers to create `.venv` and install the
dependencies, starts the server in the current window, and opens the browser once the port
answers. The log stays visible and Ctrl+C stops it.

```sh
run.bat 5100          # listen on a different port
run.bat --no-browser  # do not open a browser
run.bat --setup       # reinstall dependencies, then start
```

If it finds the app already listening it hands over to the running instance rather than
starting a second one that could not bind.

The first start downloads the embedding model (~90 MB) into the fastembed cache; after that
the app is fully offline. Set `LITEMD_DISABLE_EMBEDDING=1` to skip the model entirely —
everything except semantic search still works.

### Manual setup

Install from the **repository root**, since the requirements file references
`./src/local-vector` by relative path:

```sh
python -m venv .venv
.venv\Scripts\python -m pip install -r src\litemd_viewer\requirements.txt
```

Then, from `src/litemd_viewer`:

```sh
..\..\.venv\Scripts\python -m app.main
```

Open <http://127.0.0.1:5099>.

## Features

- **Add a file from anywhere on disk** — the “+ Add file” button opens a server-side
  browser (drives → folders → `.md`/`.markdown` files). The browser sandbox can't
  hand a web page a real absolute path, so selection happens server-side; you can
  also paste a full path.
- **View** rendered Markdown + Mermaid diagrams, GitHub-style tables, and syntax-
  highlighted code.
- **Edit** in-app (split source/preview with live Mermaid preview) and **Save** back
  to disk.
- **Never lose content** — a background service mirrors every managed file's content into
  the database and keeps it in sync as files change. If a file is deleted from disk, opening
  it still shows the last saved copy (read-only, behind a yellow warning strip); the
  **Recreate file** button writes that copy back to the file's original path.
- **Search across everything** — the top-bar box (or **Ctrl/Cmd+K**) runs a hybrid
  semantic + keyword search over passage-level chunks. Matching happens per passage but
  results are **one row per document**, showing its best passage and how many of its
  passages matched. Arrow keys and Enter pick a result; opening one jumps to that passage
  and flashes it. Edits are picked up automatically: saving a file re-embeds only the
  passages that actually changed, usually within a couple of seconds.
- **Organize** managed files into **folders** (arbitrary depth) with **editable
  titles** (the on-disk filename is never changed). Drag a file onto a folder, or
  use the “⋯” menu / double-click to rename.
- **Hidden side drawer** — opens on hover at the top-left edge, or click the ☰ button
  to pin it open.
- **Dark mode** toggle (persisted); Mermaid and code themes follow it.

## Project layout

```
run.bat                   launcher: venv bootstrap, server, browser

src/litemd_viewer/
  app/
    main.py               FastAPI host, lifespan, static mount, SPA fallback
    config.py             runtime paths and tunables (all env-overridable)
    db.py                 engine, session factory, schema bootstrap
    models.py             SQLAlchemy 2.0 entities
    schemas.py            Pydantic DTOs (camelCase over the wire)
    errors.py             {"error": "..."} envelope
    routers/              one module per endpoint group
    services/
      file_sync.py         watchdog observers + reconcile sweep -> content mirror
      indexing.py          chunk diffing, embedding, vector upsert, hybrid search
      chunker.py           paragraph/code-fence aware splitter
      embedder.py          lazy fastembed ONNX wrapper
      graph_service.py     document relation graphs
      fs_browser.py        server-side drive/folder enumeration
      platform_fs.py       Windows-aware filesystem helpers
  scripts/
    migrate_from_dotnet.py  one-shot import from the old EF Core database
    smoke_check.py          end-to-end check against a running server
  tests/                  pytest suite (no network, stub embedder)
  wwwroot/                static frontend + vendored JS libs

src/local-vector/         embeddable vector store (hnswlib + SQLite FTS5)
```

## How indexing stays in step with the files

1. `file_sync` watches every directory containing a managed file and debounces the burst
   of events an editor emits on save. A reconcile sweep every 10s is the safety net for
   coalesced or missed events.
2. A changed file's content is mirrored into the database; if its hash actually moved, the
   file id is queued for indexing.
3. The indexer re-chunks the document and compares each passage against the hash on its
   `file_chunks` row, re-embedding **only** the passages that changed.
4. A chunk row's primary key doubles as the vector store's `doc_id`, so a revised passage
   is updated in place rather than re-added under a new id — the index does not grow when
   you edit a file.

## Tests

```sh
cd src/litemd_viewer && ..\..\.venv\Scripts\python -m pytest tests -q
cd src/local-vector  && ..\..\.venv\Scripts\python -m pytest test_local_vector_db.py -q
```

The suite uses a stub embedder, so it needs no model and no network. To exercise the real
model and your actual documents, start the app and run:

```sh
.venv\Scripts\python src\litemd_viewer\scripts\smoke_check.py
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
| GET/PUT | `/api/files/{id}/content` | read / save markdown text (read falls back to the DB copy when the file is gone) |
| POST | `/api/files/{id}/recreate` | rewrite a deleted file to disk from its DB copy |
| GET/POST/PATCH/DELETE | `/api/folders[...]` | folder CRUD |
| GET/PUT | `/api/settings[...]` | theme & startup flags |
| GET | `/api/search?q=&k=&mode=` | hybrid (default), `vector`, or `text` search |
| GET | `/api/search/status` | model, dimension, indexed files/chunks, pending, errors |
| POST | `/api/search/reindex` | force a full re-index sweep |
| GET/POST/DELETE | `/api/files/{id}/relations`, `/graph`, `/colormaps[...]` | document relation graphs |
| POST/GET/DELETE | `/api/files/{id}/attachments[...]`, `/api/attachments/{id}[...]` | graph attachments, upload/download/export |
| GET/POST/PATCH/DELETE | `/api/dashboard/notes[...]` | dashboard sticky notes |
| GET/POST/PATCH/DELETE | `/api/files/{id}/notes[...]` | per-document notes + highlight references |

Interactive docs are at <http://127.0.0.1:5099/api/docs>.

## Updating the vendored libraries

The browser libs are committed under `src/litemd_viewer/wwwroot/vendor/`. To refresh:

```sh
curl -fsSL https://cdn.jsdelivr.net/npm/markdown-it@14/dist/markdown-it.min.js -o markdown-it.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js          -o purify.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js           -o mermaid.min.js
curl -fsSL https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/highlight.min.js          -o highlight.min.js
curl -fsSL https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/github.min.css      -o highlight-github.min.css
curl -fsSL https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/github-dark.min.css -o highlight-github-dark.min.css
curl -fsSL https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js                           -o three.min.js
```
