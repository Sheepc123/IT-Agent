from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import get_engine
from .errors import ApiError, handle_api_error
from .requests_api import router as requests_router
from .auth.routes import router as local_auth_router
from .workbench import router as workbench_router

app = FastAPI(title="IT Procurement API", version="0.1.0")
app.add_exception_handler(ApiError, handle_api_error)
app.include_router(requests_router)
app.include_router(workbench_router)
app.include_router(local_auth_router)


@app.get("/api/v1/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/ready")
def ready() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, ValidationError, OSError) as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "ok"}
