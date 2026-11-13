"""API error shape from docs/接口契约与错误码.md."""

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None, retryable: bool = False):
        self.status, self.code, self.message = status, code, message
        self.details, self.retryable = details or {}, retryable


def not_found() -> ApiError:
    """Also used for objects outside the caller's scope, so existence is not leaked."""
    return ApiError(404, "NOT_FOUND", "resource not found")


async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": {"code": exc.code, "message": exc.message, "details": exc.details, "retryable": exc.retryable},
            "request_id": str(uuid.uuid4()),
        },
    )
