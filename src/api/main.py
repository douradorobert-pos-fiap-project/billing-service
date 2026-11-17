import os
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from src.api.routes import catalog, payments, quotes, webhooks
from src.application.ports.payment_gateway import (
    PaymentGateway,
    PaymentGatewayConfigurationError,
)
from src.infrastructure.database.session import SessionFactory
from src.infrastructure.mercado_pago.client import MercadoPagoClient
from src.infrastructure.observability.logging import bind_context, log_event
from src.infrastructure.observability.telemetry import record_event


def create_app(
    session_factory: sessionmaker[Session] = SessionFactory,
    *,
    payment_gateway: PaymentGateway | None = None,
    mercado_pago_webhook_secret: str | None = None,
    environment: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Billing Service", version="0.1.0")
    app.state.session_factory = session_factory
    if payment_gateway is None:
        try:
            payment_gateway = MercadoPagoClient.from_env()
        except PaymentGatewayConfigurationError:
            payment_gateway = None
    app.state.payment_gateway = payment_gateway
    app.state.mercado_pago_webhook_secret = (
        mercado_pago_webhook_secret
        if mercado_pago_webhook_secret is not None
        else os.getenv("MERCADO_PAGO_WEBHOOK_SECRET", "")
    )
    app.state.environment = environment or os.getenv("APP_ENV", "local")

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        raw_value = request.headers.get("X-Correlation-ID")
        try:
            correlation_id = UUID(raw_value) if raw_value else uuid4()
        except ValueError:
            return JSONResponse(
                status_code=400, content={"detail": "X-Correlation-ID must be a UUID"}
            )
        request.state.correlation_id = correlation_id
        started = perf_counter()
        with bind_context(correlation_id=correlation_id):
            response = await call_next(request)
            duration_ms = round((perf_counter() - started) * 1000, 2)
            log_event(
                "http.request",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            record_event(
                "HttpRequest",
                {
                    "service": "billing-service",
                    "correlation_id": str(correlation_id),
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        response.headers["X-Correlation-ID"] = str(correlation_id)
        return response

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(catalog.router)
    app.include_router(payments.router)
    app.include_router(quotes.router)
    app.include_router(webhooks.router)
    return app


app = create_app()
