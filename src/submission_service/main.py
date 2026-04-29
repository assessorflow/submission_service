"""Assessment Submission Service — FastAPI + gRPC entry point.

Port 8060 (REST)  — Assessor/participant facing (external)
Port 9060 (gRPC)  — 13 internal endpoints for agent-to-service communication

Owns af_submission database (15 tables).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import structlog

from submission_service import config
from submission_service.db.pool import get_pool, close_pool
from submission_service.routes.external import router as external_router
from submission_service.grpc.server import start_grpc_server, stop_grpc_server
from prometheus_fastapi_instrumentator import Instrumentator

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

logger = structlog.get_logger(__name__)

_grpc_server = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _grpc_server
    # Startup
    logger.info(
        "submission_service_starting",
        rest_port=config.SERVICE_PORT,
        grpc_port=config.GRPC_PORT,
    )
    await get_pool()
    _grpc_server = await start_grpc_server()
    logger.info(
        "submission_service_ready",
        rest_port=config.SERVICE_PORT,
        grpc_port=config.GRPC_PORT,
    )
    yield
    # Shutdown
    if _grpc_server:
        await stop_grpc_server(_grpc_server)
    await close_pool()
    logger.info("submission_service_stopped")


app = FastAPI(
    title="AssessorFlow Assessment Submission Service",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(external_router)

# Prometheus metrics at /metrics
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


# ---------------------------------------------------------------------------
# Structured error handlers (M-1 fix)
# ---------------------------------------------------------------------------

def _error_response(status: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "error": error,
            "message": message,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = "; ".join(
        f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
    return _error_response(400, "Bad Request", errors)


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", error=str(exc), path=request.url.path)
    return _error_response(500, "Internal Server Error", "An unexpected error occurred")


# ---------------------------------------------------------------------------
# Health / Readiness
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "submission-service", "grpc_port": config.GRPC_PORT}


@app.get("/ready")
async def ready():
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        return {"status": "ready", "database": "connected", "grpc_port": config.GRPC_PORT}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "not_ready", "error": str(exc)})


if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        "submission_service.main:app",
        host="0.0.0.0",
        port=config.SERVICE_PORT,
        reload=os.environ.get("ENV", "prod") == "dev",
    )
