"""FastAPI entrypoint for ADHDman backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import init_db
from app.repositories import capture_to_inbox, list_inbox_items
from app.schemas import CaptureRequest, InboxItemResponse

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Initialize the local SQLite schema before serving requests."""

    init_db(settings)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint used by local and Docker verification."""

    return {"status": "ok"}


@app.get("/inbox", response_model=list[InboxItemResponse])
def get_inbox() -> list[InboxItemResponse]:
    """List open inbox items oldest first."""

    return list_inbox_items(settings=settings)


@app.post("/capture", response_model=InboxItemResponse, status_code=201)
def capture(request: CaptureRequest) -> InboxItemResponse:
    """Capture free-form text into the inbox."""

    return capture_to_inbox(request.text, settings)
