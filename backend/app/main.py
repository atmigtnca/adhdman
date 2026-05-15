"""FastAPI entrypoint for ADHDman backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import ensure_database_parent

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Prepare the local SQLite directory without creating domain schema yet."""

    ensure_database_parent(settings)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint used by local and Docker verification."""

    return {"status": "ok"}
