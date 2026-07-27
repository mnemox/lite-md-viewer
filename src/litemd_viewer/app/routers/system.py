"""Process control.

Exists so a relaunch can replace a running instance without killing it. A forced kill
skips the lifespan shutdown, and `Indexer.stop()` is where the vector index is committed --
`LocalVectorDB._save_indexes()` writes each `.hnsw` in place, so being terminated mid-write
leaves a truncated file that `load_index` then refuses, silently disabling search until the
vector directory is deleted by hand. Asking the server to stop itself avoids all of that.
"""

from __future__ import annotations

import logging
import signal

from fastapi import APIRouter, BackgroundTasks, FastAPI, Request

from ..errors import forbidden

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["system"])

# The app binds loopback-only anyway; this is the second lock on the same door.
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


@router.post("/shutdown")
def shutdown(request: Request, background: BackgroundTasks) -> dict:
    """Stop the server gracefully, after this response has been sent.

    The work happens in a background task rather than inline so the caller gets its answer
    first -- run.bat waits for the port to go quiet, and a dropped response would leave it
    unable to tell a clean stop from a hung one.
    """
    client = request.client.host if request.client else ""
    if client not in LOOPBACK:
        raise forbidden("Shutdown may only be requested from this machine.")

    log.info("shutdown requested by %s", client)
    background.add_task(_stop, request.app)
    return {"stopping": True}


def _stop(app: FastAPI) -> None:
    server = getattr(app.state, "server", None)
    if server is not None:
        # Uvicorn drains in-flight connections, then unwinds the lifespan, which is what
        # commits the index.
        server.should_exit = True
        return
    # Started by an external uvicorn/gunicorn, so there is no server object to flag. A
    # SIGINT is the same thing Ctrl+C would deliver, and unwinds the lifespan just as well.
    log.info("no server handle; raising SIGINT to stop")
    signal.raise_signal(signal.SIGINT)
