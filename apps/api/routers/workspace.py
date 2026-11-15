"""Persona-scoped settings, memory, analytics, and proposal board."""

from fastapi import APIRouter, HTTPException

from apps.api.routers.marketplace import PersonaDep, WorkflowDep
from apps.api.schemas.workspace import (
    AnalyticsView,
    MemoryEntry,
    PipelineCard,
    Preferences,
    RemoveMemory,
    WorkspaceSettings,
)
from apps.api.workflow import PersonaSession, WebWorkflow
from apps.api.workspace import weekly_connects
from mcp_servers.marketplace.port import Event

router = APIRouter(prefix="/api/v1/workspace", tags=["Workspace"])


async def view_settings(runtime: WebWorkflow, persona: PersonaSession) -> WorkspaceSettings:
    account = await runtime.verified_account(persona)
    proposals = await persona.adapter.list_proposals()
    return WorkspaceSettings(
        preferences=await runtime.workspace.preferences(runtime.tenant(persona)),
        default_weekly_budget=int(runtime.settings.connects["default_weekly_budget"]),
        default_hourly_rate=persona.view.rate,
        default_auto_skip=runtime.settings.autonomy.auto_skip_low_fit,
        account_id=account.account_id,
        weekly_connects_spent=weekly_connects(proposals, account.next_refill_at),
        connects_balance=account.balance,
    )


@router.get("/settings")
async def get_settings(runtime: WorkflowDep, persona: PersonaDep) -> WorkspaceSettings:
    return await view_settings(runtime, persona)


@router.get("/preferences")
async def get_preferences(runtime: WorkflowDep, persona: PersonaDep) -> Preferences:
    return await runtime.workspace.preferences(runtime.tenant(persona))


@router.put("/settings")
async def put_settings(
    runtime: WorkflowDep, persona: PersonaDep, preferences: Preferences
) -> WorkspaceSettings:
    view = await view_settings(runtime, persona)
    await runtime.workspace.set_preferences(runtime.tenant(persona), preferences)
    return view.model_copy(update={"preferences": preferences})


@router.get("/memory")
async def get_memory(runtime: WorkflowDep, persona: PersonaDep) -> list[MemoryEntry]:
    return await runtime.workspace.memory(runtime.tenant(persona))


@router.post("/memory/remove")
async def remove_memory(
    runtime: WorkflowDep, persona: PersonaDep, entry: RemoveMemory
) -> list[MemoryEntry]:
    tenant = runtime.tenant(persona)
    if not await runtime.workspace.remove_memory(tenant, entry):
        raise HTTPException(404, "The memory item no longer exists.")
    return await runtime.workspace.memory(tenant)


@router.get("/analytics")
async def get_analytics(runtime: WorkflowDep, persona: PersonaDep) -> AnalyticsView:
    await runtime.verified_account(persona)
    proposals = await persona.adapter.list_proposals()
    events: list[Event] = []
    cursor = 0
    history_complete = False
    for _ in range(10):
        batch = await persona.adapter.list_events(since=cursor, limit=100)
        events.extend(batch)
        if len(batch) < 100:
            history_complete = True
            break
        cursor = batch[-1].id
    return await runtime.workspace.analytics(
        runtime.tenant(persona), proposals, events, history_complete=history_complete
    )


@router.get("/pipeline")
async def get_pipeline(runtime: WorkflowDep, persona: PersonaDep) -> list[PipelineCard]:
    await runtime.verified_account(persona)
    return await runtime.workspace.pipeline(
        runtime.tenant(persona), await persona.adapter.list_proposals()
    )
