"""Isolated package/process probe, not the dashboard or production readiness.

Never import application configuration, connectors, storage or workers here.
Serve on loopback only, with no production data or credential bindings.
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/readyz")
def ready():
    return JSONResponse(
        {"ok": True, "mode": "probe-only", "production_ready": False},
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
