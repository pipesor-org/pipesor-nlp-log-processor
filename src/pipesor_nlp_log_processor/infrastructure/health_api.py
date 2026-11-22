from threading import Lock

from fastapi import FastAPI, Response, status
from fastapi.responses import JSONResponse

from .platform_identity import SigningKey, public_jwk


class RuntimeHealth:
    def __init__(self) -> None:
        self._lock = Lock()
        self._receiver_ready = False
        self._authorization_ready = True

    def receiver(self, ready: bool) -> None:
        with self._lock:
            self._receiver_ready = ready

    def authorization(self, ready: bool) -> None:
        with self._lock:
            self._authorization_ready = ready

    def ready(self) -> bool:
        with self._lock:
            return self._receiver_ready and self._authorization_ready


def create_health_app(health: RuntimeHealth, key: SigningKey) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> Response:
        if health.ready():
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not_ready"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    @app.get("/.well-known/jwks.json")
    def jwks() -> Response:
        return JSONResponse(
            {"keys": [public_jwk(key)]}, headers={"Cache-Control": "public, max-age=300"}
        )

    return app
