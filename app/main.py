"""FastAPI entry point."""

from fastapi import FastAPI, HTTPException

from scheduler import DolphinSchedulerError, create_and_submit_incremental_update

app = FastAPI(title="Arena Backend", version="0.1.0")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/scheduler/incremental-updates", tags=["scheduler"])
def submit_incremental_update() -> dict[str, int]:
    try:
        return create_and_submit_incremental_update()
    except DolphinSchedulerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
