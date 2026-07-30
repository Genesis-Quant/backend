"""FastAPI entry point."""

from dataclasses import asdict

from fastapi import FastAPI, HTTPException

from scheduler import create_and_submit_incremental_update
from scheduler.client import DolphinSchedulerError

app = FastAPI(
    title="Arena Backend",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Return service health."""
    return {"status": "ok"}


@app.post(
    "/api/v1/scheduler/incremental-updates",
    tags=["scheduler"],
)
def submit_incremental_update() -> dict[str, object]:
    """Create/update and immediately submit the incremental update workflow."""
    try:
        result = create_and_submit_incremental_update()
    except DolphinSchedulerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return asdict(result)
