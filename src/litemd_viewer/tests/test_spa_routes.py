"""The SPA fallback that makes client-side routes survive a hard refresh.

The client-side router owns /notes and /files/<id> (see wwwroot/js/router.js), so a
refresh or a shared link on one of those paths has to return the app shell rather than a
404. A mount at "/" matches every path and answers its own 404, which is why the fallback
lives inside SpaStaticFiles instead of a separate route.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


def local_client() -> TestClient:
    # The default TestClient host is "testclient", which the loopback guard rejects.
    return TestClient(app, client=("127.0.0.1", 54321))


@pytest.mark.parametrize(
    "path",
    ["/", "/notes", "/files/1", "/files/1/edit", "/files/12345", "/unknown-route"],
)
def test_client_routes_serve_the_app_shell(path):
    response = local_client().get(path)

    assert response.status_code == 200
    assert 'id="aiPanel"' in response.text, "expected index.html, not some other file"


def test_static_assets_are_still_served_normally():
    response = local_client().get("/css/style.css")

    assert response.status_code == 200
    assert ".ai-panel" in response.text


def test_unknown_api_paths_stay_a_real_404():
    # Falling back to index.html here would turn a typo'd endpoint into a silent success.
    response = local_client().get("/api/definitely-not-an-endpoint")

    assert response.status_code == 404
    assert response.json() == {"error": "Not Found"}
