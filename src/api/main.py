from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from src.api.routes import catalog, quotes
from src.infrastructure.database.session import SessionFactory


def create_app(session_factory: sessionmaker[Session] = SessionFactory) -> FastAPI:
    app = FastAPI(title="Billing Service", version="0.1.0")
    app.state.session_factory = session_factory

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        raw_value = request.headers.get("X-Correlation-ID")
        try:
            correlation_id = UUID(raw_value) if raw_value else uuid4()
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "X-Correlation-ID must be a UUID"})
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = str(correlation_id)
        return response

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(catalog.router)
    app.include_router(quotes.router)
    return app


app = create_app()
