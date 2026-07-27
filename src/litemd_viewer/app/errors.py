"""The `{"error": "..."}` response envelope the frontend expects.

`req()` in wwwroot/js/api.js reads `data.error` off a failed response and falls back to a
bare status line when it is absent, so FastAPI's default `{"detail": ...}` shape would turn
every message in the UI into "400 Bad Request". Some conflicts also carry extra fields --
a 409 from the add-file endpoints includes the `id` of the already-managed document, which
app.js uses to open it instead of showing an error.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """An error that should reach the client as {"error": message, **extra}."""

    def __init__(self, status_code: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        return {"error": self.message, **self.extra}


def bad_request(message: str, **extra: Any) -> ApiError:
    return ApiError(400, message, **extra)


def forbidden(message: str = "Forbidden", **extra: Any) -> ApiError:
    return ApiError(403, message, **extra)


def not_found(message: str = "Not found", **extra: Any) -> ApiError:
    return ApiError(404, message, **extra)


def conflict(message: str, **extra: Any) -> ApiError:
    return ApiError(409, message, **extra)


def problem(message: str, **extra: Any) -> ApiError:
    """Equivalent of the .NET Results.Problem(...) 500, but in the envelope the UI reads."""
    return ApiError(500, message, **extra)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.payload())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 404s raised by the static-file mount have a plain string detail.
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else None
        if first:
            field = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
            message = f"{field}: {first.get('msg')}" if field else str(first.get("msg"))
        else:
            message = "Invalid request."
        return JSONResponse(status_code=400, content={"error": message})
