"""Loopback-only development API. No user-data writes or provider integrations."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

from shiros.adapters.database.connection import check_database, make_engine
from shiros.config import Settings


def create_app() -> FastAPI:
    app = FastAPI(title="ShirOS", version="0.5.0")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok", "service": "ShirOS", "stage": "trusted-review"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        engine = None
        try:
            engine = make_engine(Settings().database_url())
            health = check_database(engine)
            return JSONResponse(
                status_code=200 if health.ready else 503, content=health.model_dump()
            )
        except Exception:
            # Driver exceptions can contain credentials or connection details.
            return JSONResponse(status_code=503, content={"ready": False})
        finally:
            if engine is not None:
                engine.dispose()

    return app


app = create_app()
