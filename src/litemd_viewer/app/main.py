"""Application host.

HTTP only, loopback only: the disk-touching endpoints must never be reachable off-box.
Static assets are served from wwwroot, and any unmatched non-API path falls back to
index.html so the client-side router owns navigation.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .db import SessionLocal, get_setting, init_db
from .errors import register_error_handlers
from .routers import (
    ai,
    attachments,
    boards,
    browse,
    content,
    dashboard,
    document_notes,
    files,
    folders,
    relations,
    search,
    settings,
    system,
)
from .services.file_sync import FileSyncService
from .services.indexing import get_indexer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("litemdviewer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    init_db()

    indexer = get_indexer()
    sync = FileSyncService(indexer)
    app.state.indexer = indexer
    app.state.file_sync = sync

    # Warm the ONNX model off the event loop so the first search is not the one that pays
    # for loading it. A failure here only disables search.
    if config.EMBEDDING_ENABLED:
        threading.Thread(
            target=indexer.embedder.load, name="embedder-warmup", daemon=True
        ).start()

    sync.start()
    indexer.start()
    log.info("listening on http://%s:%s", config.HOST, config.PORT)

    try:
        yield
    finally:
        await sync.stop()
        await indexer.stop()


app = FastAPI(
    title="LiteMdViewer",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

register_error_handlers(app)

app.include_router(files.router)
app.include_router(folders.router)
app.include_router(content.router)
app.include_router(browse.router)
app.include_router(settings.router)
app.include_router(relations.router)
app.include_router(attachments.router)
app.include_router(attachments.downloads)
app.include_router(dashboard.router)
app.include_router(boards.router)
app.include_router(document_notes.router)
app.include_router(document_notes.groups)
app.include_router(search.router)
app.include_router(system.router)
app.include_router(ai.router)
app.include_router(ai.chat_router)


class SpaStaticFiles(StaticFiles):
    """Static assets, with index.html served for unmatched client-side routes.

    A mount at "/" matches every path and answers its own 404, so a separate fallback
    route would never be reached. Paths like /notes or /files/5 are owned by the
    client-side router (see wwwroot/js/router.js) and must return the app shell on a
    hard refresh; anything under /api stays a real 404.
    """

    async def get_response(self, path: str, scope):  # noqa: ANN001, ANN201
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith("api"):
                return await super().get_response("index.html", scope)
            raise


if config.STATIC_DIR.is_dir():
    # html=True serves index.html for a bare directory request, matching UseDefaultFiles.
    app.mount("/", SpaStaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")


def should_open_browser() -> bool:
    """Honour the openBrowserOnStart setting, unless a harness pinned the port."""
    if config.PORT_OVERRIDE or config.NO_BROWSER:
        return False
    try:
        with SessionLocal() as session:
            return get_setting(session, "openBrowserOnStart") == "true"
    except Exception:  # noqa: BLE001
        return False


def run() -> None:
    import uvicorn

    init_db()
    if should_open_browser():
        url = f"http://{config.HOST}:{config.PORT}"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    # Built rather than uvicorn.run(...) so the server object can be reached from a request:
    # POST /api/shutdown flags it, which is how a relaunch replaces this instance without a
    # forced kill. See routers/system.py.
    server = uvicorn.Server(uvicorn.Config(
        app, host=config.HOST, port=config.PORT, log_level="info"
    ))
    app.state.server = server
    server.run()


if __name__ == "__main__":
    run()
