"""FastAPI entry point."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import ArenaSettings, MCPSettings
from core.apps.admin.services import AdminService
from core.apps.admin.views import router as admin_router
from core.apps.backtest.views import router as backtest_router
from core.apps.mcp.views import router as mcp_router
from core.apps.factor.views import router as factor_router
from core.mcp.server import mcp_app, mcp_server
from core.apps.query.views import router as query_router
from core.apps.tasks.views import router as tasks_router
from core.apps.users.services import validate_security_configuration
from core.apps.users.views import router as users_router
from core.apps.workflows.services import poll_workflow_statuses
from core.apps.workflows.views import router as workflows_router
from core.database.health import DatabaseError, check_database
from core.utils.dsl import initialize_dsl_catalog


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    validate_security_configuration()
    ArenaSettings.validate()
    application.state.dsl_catalog = initialize_dsl_catalog()
    application.state.workflows = AdminService.ensure_workflows()
    stop_poller = asyncio.Event()
    workflow_poller = asyncio.create_task(poll_workflow_statuses(stop_poller))
    application.state.workflow_poller = workflow_poller
    async with mcp_server.session_manager.run():
        try:
            yield
        finally:
            stop_poller.set()
            await workflow_poller


app = FastAPI(title="Arena Backend", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)
app.include_router(users_router)
app.include_router(admin_router)
app.include_router(query_router)
app.include_router(factor_router)
app.include_router(backtest_router)
app.include_router(mcp_router)
app.include_router(workflows_router)
app.include_router(tasks_router)


@app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
def mcp_protected_resource_metadata() -> dict[str, object]:
    return {
        "resource": MCPSettings.ENDPOINT_URL,
        "authorization_servers": [f"{MCPSettings.PUBLIC_URL}/"],
        "scopes_supported": ["arena"],
        "bearer_methods_supported": ["header"],
    }


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


app.mount("/", mcp_app)
