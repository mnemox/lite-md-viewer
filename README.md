# md-manager

A small, self-hosted local web app to **view, edit, and manage Markdown files**
(including **Mermaid** diagrams). Files you add to the app are **locked from
deletion** on disk until you explicitly unlock them here.

- Backend: **ASP.NET Core (.NET 8)**, minimal APIs, SQLite (EF Core).
- Frontend: vanilla HTML/CSS/JS (no framework), with `markdown-it` + `mermaid` +
  `highlight.js` + `DOMPurify` vendored locally (works fully offline).
- Runs over **HTTP on loopback only** (`http://127.0.0.1:5099`) — no HTTPS, no auth,
  single local user.

## Requirements

- **.NET 8 SDK** (`dotnet --list-sdks` should show an `8.x`).
- Windows + an **NTFS** volume for the delete-lock (the lock is an NTFS ACL; on
  non-NTFS volumes a file is still managed but can't be locked).

## Run

```sh
cd src/MdManager
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
  to disk. Editing works even while a file is locked.
- **Organize** managed files into **folders** (arbitrary depth) with **editable
  titles** (the on-disk filename is never changed). Drag a file onto a folder, or
  use the “⋯” menu / double-click to rename.
- **Hidden side drawer** — opens on hover at the top-left edge, or click the ☰ button
  to pin it open.
- **Dark mode** toggle (persisted); Mermaid and code themes follow it.

## How the delete-lock works

Locking adds an explicit **“Deny Delete” ACE** for the current user to the file's
NTFS permissions (`FileSystemRights.Delete`, `AccessControlType.Deny`). This:

- blocks delete / rename / move in Windows Explorer and from other apps (rename
  needs delete rights), and
- **persists across app restarts and reboots** (it lives in the file's on-disk
  security descriptor), and
- still **allows reading and writing** the content, so in-app editing keeps working.

Unlocking removes that ACE. The app's own “Delete from disk” action refuses (`409`)
while a file is locked — you must unlock first.

A background `FileLockService` reconciles state on startup (re-applies the ACE to
files that should be locked, flags missing files), and `FileWatcherService`
periodically refreshes status.

### Caveats (by design)

- The lock is a **usability guard**, not a security control. Because you own the
  files, you (or an admin) can still remove the ACE manually via Explorer/`icacls`.
  It stops accidental and casual deletion, which is the goal.
- Editors that save via *temp-file-then-rename* (e.g. VS Code) can't save a **locked**
  file in place (the rename is blocked). Unlock it first to edit externally — the
  in-app editor writes in place and is unaffected.

## Project layout

```
src/MdManager/
  Program.cs              host, DI, loopback bind, endpoint mapping
  Data/AppDbContext.cs    EF Core (Folders, ManagedFiles, Settings)
  Models/                 entities + DTOs
  Services/
    LockManager.cs        apply/remove the Deny-Delete ACE
    FileLockService.cs    startup ACL reconcile (hosted service)
    FileWatcherService.cs periodic status refresh (hosted service)
    FsBrowser.cs          server-side drive/folder/.md enumeration
  Endpoints/              minimal-API endpoint groups
  wwwroot/                static frontend + vendored JS libs
```

## API (loopback only)

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/tree` | folders + files with status |
| GET | `/api/browse?path=` | list drives / a folder's subfolders + `.md` files |
| POST | `/api/files` | add (and lock) a file by absolute path |
| PATCH | `/api/files/{id}` | rename title / move folder / reorder |
| POST | `/api/files/{id}/lock` · `/unlock` | apply / remove the lock |
| DELETE | `/api/files/{id}` | remove from management (keeps the file) |
| DELETE | `/api/files/{id}/disk` | delete the file (refused while locked) |
| GET/PUT | `/api/files/{id}/content` | read / save markdown text |
| GET/POST/PATCH/DELETE | `/api/folders[...]` | folder CRUD |
| GET/PUT | `/api/settings[...]` | theme & startup flags |

## Updating the vendored libraries

The browser libs are committed under `src/MdManager/wwwroot/vendor/`. To refresh:

```sh
curl -fsSL https://cdn.jsdelivr.net/npm/markdown-it@14/dist/markdown-it.min.js -o markdown-it.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js          -o purify.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js           -o mermaid.min.js
curl -fsSL https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/highlight.min.js          -o highlight.min.js
curl -fsSL https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/github.min.css      -o highlight-github.min.css
curl -fsSL https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/github-dark.min.css -o highlight-github-dark.min.css
```
