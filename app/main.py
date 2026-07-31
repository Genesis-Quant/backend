"""FastAPI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.database import DatabaseError, check_database
from app.routers.scheduler import router as scheduler_router
from scheduler import ensure_all_workflows


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.workflows = ensure_all_workflows()
    yield


app = FastAPI(title="Arena Backend", version="0.1.0", lifespan=lifespan)
app.include_router(scheduler_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    try:
        database = check_database()
    except DatabaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        "status": "ok",
        "database": database.database,
        "schema": database.schema,
    }
