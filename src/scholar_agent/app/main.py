"""FastAPI application entry point for the SPAR mock backend."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="SPAR Scholar Agent Mock API",
        version="0.1.0",
        description=(
            "Mock backend API for frontend integration. It does not call live "
            "academic search APIs or LLM providers."
        ),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    return app


app = create_app()

