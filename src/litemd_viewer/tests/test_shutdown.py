"""The graceful stop that lets a relaunch replace a running instance.

run.bat asks for this before it resorts to killing anything, because a forced kill skips
the lifespan shutdown that commits the vector index.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import system


class StubServer:
    """Stands in for the uvicorn Server that run() stashes on app.state."""

    def __init__(self) -> None:
        self.should_exit = False


@pytest.fixture()
def server():
    stub = StubServer()
    app.state.server = stub
    yield stub
    if hasattr(app.state, "server"):
        del app.state.server


def local_client() -> TestClient:
    # The default TestClient host is "testclient", which the loopback guard rejects.
    return TestClient(app, client=("127.0.0.1", 54321))


def test_shutdown_asks_the_server_to_stop(server):
    response = local_client().post("/api/shutdown")

    assert response.status_code == 200
    assert response.json() == {"stopping": True}
    assert server.should_exit is True


def test_shutdown_is_refused_from_another_machine(server):
    response = TestClient(app, client=("192.168.1.50", 54321)).post("/api/shutdown")

    assert response.status_code == 403
    assert response.json()["error"] == "Shutdown may only be requested from this machine."
    assert server.should_exit is False, "a refused request must leave the server running"


def test_ipv6_loopback_is_allowed(server):
    assert TestClient(app, client=("::1", 54321)).post("/api/shutdown").status_code == 200
    assert server.should_exit is True


def test_shutdown_falls_back_to_a_signal_without_a_server_handle(monkeypatch):
    # Started by an external uvicorn, so run() never stashed a server to flag. A SIGINT
    # unwinds the lifespan just as Ctrl+C would.
    if hasattr(app.state, "server"):
        del app.state.server
    raised: list[int] = []
    monkeypatch.setattr(system.signal, "raise_signal", raised.append)

    assert local_client().post("/api/shutdown").status_code == 200
    assert raised == [system.signal.SIGINT]


def test_shutdown_is_not_reachable_by_GET(server):
    # The SPA fallback answers unmatched GETs, so a typo in the URL must not stop the app.
    local_client().get("/api/shutdown")
    assert server.should_exit is False
