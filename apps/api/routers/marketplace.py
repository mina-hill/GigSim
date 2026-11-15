"""Typed simulator endpoints for the local web interface."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from agent.handoffs import ApprovalDecision
from apps.api.schemas.conversation import ConversationRunView, StartConversation
from apps.api.schemas.workflow import ErrorView, JobCard, JobRecord, PersonaView, RunView
from apps.api.workflow import PersonaSession, WebWorkflow
from mcp_servers.marketplace.port import Message, Proposal, Thread

router = APIRouter(
    prefix="/api/v1",
    tags=["Simulator workspace"],
    responses={503: {"model": ErrorView, "description": "Simulator unavailable"}},
)

NOT_FOUND = {404: {"model": ErrorView, "description": "Persona or resource not found"}}
CONFLICT = {409: {"model": ErrorView, "description": "Review the current run before retrying"}}
ResourceId = Annotated[str, Path(min_length=1, max_length=128, pattern=r"^[\w~\-][\w.~\-]*$")]


async def workflow(request: Request) -> WebWorkflow:
    return request.app.state.workflow


WorkflowDep = Annotated[WebWorkflow, Depends(workflow)]


async def selected_persona(
    runtime: WorkflowDep, persona: Annotated[str, Query(min_length=1, max_length=32)]
) -> PersonaSession:
    return runtime.persona(persona)


PersonaDep = Annotated[PersonaSession, Depends(selected_persona)]


@router.get("/personas")
async def list_personas(runtime: WorkflowDep) -> list[PersonaView]:
    return runtime.list_personas()


@router.get("/jobs", responses=NOT_FOUND)
async def list_jobs(
    runtime: WorkflowDep,
    persona: PersonaDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> list[JobCard]:
    return await runtime.list_jobs(persona, q=q, limit=limit, cursor=cursor)


@router.get("/jobs/{job_id}", responses=NOT_FOUND)
async def job_detail(runtime: WorkflowDep, persona: PersonaDep, job_id: ResourceId) -> JobRecord:
    return await runtime.job(persona, job_id)


@router.get("/jobs/{job_id}/run", responses=NOT_FOUND)
async def get_run(runtime: WorkflowDep, persona: PersonaDep, job_id: ResourceId) -> RunView:
    return await runtime.get_run(persona, job_id)


@router.post("/jobs/{job_id}/run", responses=NOT_FOUND | CONFLICT)
async def start_run(runtime: WorkflowDep, persona: PersonaDep, job_id: ResourceId) -> RunView:
    return await runtime.schedule_run(persona, job_id)


@router.post("/jobs/{job_id}/decision", responses=NOT_FOUND | CONFLICT)
async def decide(
    runtime: WorkflowDep, persona: PersonaDep, job_id: ResourceId, decision: ApprovalDecision
) -> RunView:
    return await runtime.decide(persona, job_id, decision)


@router.get("/pipeline", responses=NOT_FOUND)
async def list_proposals(runtime: WorkflowDep, persona: PersonaDep) -> list[Proposal]:
    await runtime.verified_account(persona)
    return await persona.adapter.list_proposals()


@router.get("/messages", responses=NOT_FOUND)
async def list_threads(runtime: WorkflowDep, persona: PersonaDep) -> list[Thread]:
    await runtime.verified_account(persona)
    return await persona.adapter.list_threads()


@router.get("/messages/{thread_id}", responses=NOT_FOUND)
async def list_messages(
    runtime: WorkflowDep, persona: PersonaDep, thread_id: ResourceId
) -> list[Message]:
    await runtime.verified_account(persona)
    return await persona.adapter.list_messages(thread_id)


@router.get("/messages/{thread_id}/run", responses=NOT_FOUND)
async def get_conversation(
    runtime: WorkflowDep, persona: PersonaDep, thread_id: ResourceId
) -> ConversationRunView:
    return await runtime.conversation_run(persona, thread_id)


@router.post("/messages/{thread_id}/run", responses=NOT_FOUND | CONFLICT)
async def start_conversation(
    runtime: WorkflowDep,
    persona: PersonaDep,
    thread_id: ResourceId,
    options: StartConversation | None = None,
) -> ConversationRunView:
    return await runtime.start_conversation(persona, thread_id, options or StartConversation())


@router.post("/messages/{thread_id}/decision", responses=NOT_FOUND | CONFLICT)
async def decide_conversation(
    runtime: WorkflowDep, persona: PersonaDep, thread_id: ResourceId, decision: ApprovalDecision
) -> ConversationRunView:
    return await runtime.decide_conversation(persona, thread_id, decision)
