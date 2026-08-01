"""FastAPI entry point."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from apps.backtest.views import router as backtest_router
from apps.factor.views import router as factor_router
from apps.query.views import router as query_router
from apps.tasks.services import poll_task_statuses
from apps.tasks.views import router as tasks_router
from apps.users.services import validate_security_configuration
from apps.users.views import router as users_router
from config.database import DatabaseError, check_database
from config.dolphinscheduler.workflows import ensure_all_workflows


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    validate_security_configuration()
    application.state.workflows = ensure_all_workflows()
    stop_poller = asyncio.Event()
    task_poller = asyncio.create_task(poll_task_statuses(stop_poller))
    application.state.task_poller = task_poller
    try:
        yield
    finally:
        stop_poller.set()
        await task_poller


app = FastAPI(title="Arena Backend", version="0.1.0", lifespan=lifespan)
app.include_router(users_router)
app.include_router(query_router)
app.include_router(factor_router)
app.include_router(backtest_router)
app.include_router(tasks_router)


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
