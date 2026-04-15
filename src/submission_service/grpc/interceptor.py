"""gRPC server interceptor for logging and observability.

Logs every RPC call with method name, status code, and duration.
gRPC endpoints are internal (service-to-service within K8s cluster).
Security relies on K8s network policies for pod-to-pod isolation.
If mTLS is needed later (e.g., Istio), add certificate validation here.
"""

from __future__ import annotations

import time

import grpc
import structlog

logger = structlog.get_logger(__name__)


class LoggingInterceptor(grpc.aio.ServerInterceptor):
    """Logs every gRPC call with method, status, and duration."""

    async def intercept_service(self, continuation, handler_call_details):
        method = handler_call_details.method
        start = time.perf_counter()

        handler = await continuation(handler_call_details)

        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "grpc_call",
            method=method,
            duration_ms=round(duration_ms, 2),
        )

        return handler
