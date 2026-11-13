"""GigSim: a marketplace simulator with a REST API, for developing and grading GigBid.

Freelancer endpoints need `Authorization: Bearer <token>` (tokens map to accounts through
GIGSIM_API_TOKENS). Admin endpoints need `X-Admin-Token`. Every write needs an
`Idempotency-Key` header: repeating a key with the same body replays the stored response,
with a different body it is rejected.

Faults on purpose: per-token rate limit (429 + Retry-After), random 5xx before the handler,
optional "response lost" 502 after a successful write (tests idempotency), latency jitter,
and a full outage switch. Configure them at runtime with PUT /admin/faults.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gigsim import db, sim
from gigsim.faults import FaultState, TokenBucketLimiter
from gigsim.models import (
    Account,
    Client,
    Event,
    IdempotencyRecord,
    Job,
    MarketStat,
    Message,
    PastContract,
    Proposal,
    Thread,
)
from gigsim.policy import available_versions, load_policy
from gigsim.schemas import (
    ClientHistoryOut,
    ClientSummary,
    ConnectsOut,
    EventOut,
    JobOut,
    JobPage,
    MarketStatOut,
    MessageIn,
    MessageOut,
    PastContractOut,
    ProposalCreate,
    ProposalOut,
    ThreadOut,
)
from gigsim.seed import load_world
from gigsim.settings import get_settings

EXEMPT_PREFIXES = ("/health", "/admin", "/docs", "/openapi.json", "/redoc")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    db.init_engine(s.db_url)
    db.create_all()
    with db.session_scope() as session:
        sim.state(session)
        session.commit()
    app.state.faults = FaultState(error_rate=s.error_rate, latency_ms_max=s.latency_ms_max,
                                  rate_limit_per_minute=s.rate_limit_per_minute, seed=s.seed)
    app.state.post_commit_error_rate = 0.0
    app.state.limiter = TokenBucketLimiter(s.rate_limit_per_minute)
    yield


app = FastAPI(title="GigSim", version="1.0.0", lifespan=lifespan,
              description=__doc__)


# --------------------------------------------------------------------------- faults


@app.middleware("http")
async def inject_faults(request: Request, call_next):
    path = request.url.path
    if path.startswith(EXEMPT_PREFIXES):
        return await call_next(request)
    faults: FaultState = request.app.state.faults
    if faults.down:
        return JSONResponse({"detail": "GigSim is unavailable (outage)"}, status_code=503)
    fail, status, latency = faults.draw()
    if latency:
        await asyncio.sleep(latency)
    if fail:
        return JSONResponse({"detail": "GigSim internal error (injected)"}, status_code=status)
    response = await call_next(request)
    if (request.method == "POST" and 200 <= response.status_code < 300
            and request.app.state.post_commit_error_rate > 0):
        if faults.chance(request.app.state.post_commit_error_rate):
            return JSONResponse({"detail": "Upstream timeout: response lost after commit"},
                                status_code=502)
    return response


# --------------------------------------------------------------------------- dependencies


def current_account(request: Request, authorization: str | None = Header(default=None),
                    session: Session = Depends(db.get_session)) -> Account:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    account_id = get_settings().token_map().get(token)
    if account_id is None:
        raise HTTPException(401, "Unknown token")
    allowed, retry = request.app.state.limiter.take(token)
    if not allowed:
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": str(retry)})
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(403, f"Account {account_id} is not seeded in this world")
    return account


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if x_admin_token != get_settings().admin_token:
        raise HTTPException(401, "Admin token required")


def require_idempotency_key(idempotency_key: str | None = Header(default=None)) -> str:
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(400, "Idempotency-Key header (1-128 chars) is required on writes")
    return idempotency_key


def _replay_or_run(session: Session, account_id: str, key: str, endpoint: str, body: Any,
                   run, response: Response) -> Any:
    canonical = json.dumps(body, sort_keys=True, default=str)
    request_hash = hashlib.sha256(canonical.encode()).hexdigest()
    existing = session.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.account_id == account_id, IdempotencyRecord.key == key))
    if existing is not None:
        if existing.endpoint != endpoint or existing.request_hash != request_hash:
            raise HTTPException(409, "Idempotency-Key was already used for a different request")
        response.headers["Idempotent-Replay"] = "true"
        response.status_code = existing.status_code
        return existing.response
    status_code, payload = run()
    session.add(IdempotencyRecord(account_id=account_id, key=key, endpoint=endpoint,
                                  request_hash=request_hash, status_code=status_code,
                                  response=payload))
    try:
        session.commit()
    except IntegrityError as exc:  # a concurrent retry with the same key won the race
        session.rollback()
        raise HTTPException(409, "Concurrent request with the same Idempotency-Key") from exc
    response.status_code = status_code
    return payload


# --------------------------------------------------------------------------- serialisers


def _client_summary(c: Client) -> ClientSummary:
    return ClientSummary(client_id=c.client_id, country=c.country,
                         payment_verified=c.payment_verified, total_spent=c.total_spent,
                         rating=c.rating, reviews_count=c.reviews_count, hire_rate=c.hire_rate,
                         jobs_posted=c.jobs_posted, member_since=c.member_since)


def _job_out(session: Session, job: Job) -> JobOut:
    cost, _ = sim.job_connects_cost(session, job)
    return JobOut(
        job_id=job.job_id, title=job.title, description=job.description, skills=job.skills,
        domain=job.domain, experience_level=job.experience_level, payment_type=job.payment_type,
        budget_min=job.budget_min, budget_max=job.budget_max, hourly_min=job.hourly_min,
        hourly_max=job.hourly_max, duration=job.duration,
        screening_questions=job.screening_questions, applicants=job.applicants,
        connects_cost=cost, status=job.status,
        posted_at=sim.tick_to_iso(job.release_tick, job.posted_offset_hours),
        client=_client_summary(session.get(Client, job.client_id)),
    )


def _proposal_out(session: Session, p: Proposal) -> ProposalOut:
    cost = p.connects_spent if p.status == "submitted" else sim.job_connects_cost(
        session, session.get(Job, p.job_id))[0]
    return ProposalOut(
        proposal_id=p.proposal_id, job_id=p.job_id, cover_letter=p.cover_letter,
        bid_amount=p.bid_amount, bid_type=p.bid_type, screening_answers=p.screening_answers,
        status=p.status, stage=p.stage, connects_spent=p.connects_spent, connects_cost=cost,
        created_at=sim.tick_to_iso(p.created_tick),
        submitted_at=sim.tick_to_iso(p.submitted_tick) if p.submitted_tick is not None else None,
    )


def _visible_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.status != "open" or job.release_tick > sim.state(session).tick:
        raise HTTPException(404, "Job not found")
    return job


# --------------------------------------------------------------------------- public routes


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "gigsim"}


@app.get("/me")
def me(account: Account = Depends(current_account)) -> dict[str, Any]:
    return {"account_id": account.account_id, "display_name": account.display_name}


@app.get("/me/connects", response_model=ConnectsOut)
def my_connects(account: Account = Depends(current_account),
                session: Session = Depends(db.get_session)) -> ConnectsOut:
    policy = load_policy(sim.state(session).policy_version)
    every = int(policy["connects"]["refill_every_ticks"])
    return ConnectsOut(account_id=account.account_id, balance=account.connects_balance,
                       weekly_refill=account.weekly_connects,
                       next_refill_at=sim.tick_to_iso(account.last_refill_tick + every))


@app.get("/jobs", response_model=JobPage)
def search_jobs(
    q: str | None = Query(default=None, max_length=200),
    domain: str | None = None,
    payment_type: str | None = Query(default=None, pattern="^(fixed|hourly)$"),
    min_budget: float | None = Query(default=None, ge=0),
    posted_after: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: int = Query(default=0, ge=0),
    account: Account = Depends(current_account),
    session: Session = Depends(db.get_session),
) -> JobPage:
    tick = sim.state(session).tick
    stmt = select(Job).where(Job.status == "open", Job.release_tick <= tick)
    if domain:
        stmt = stmt.where(Job.domain == domain)
    if payment_type:
        stmt = stmt.where(Job.payment_type == payment_type)
    jobs = session.scalars(stmt.order_by(Job.release_tick.desc(), Job.seq)).all()
    terms = [t for t in (q or "").lower().split() if t]
    out = []
    for j in jobs:
        hay = f"{j.title} {j.description} {' '.join(j.skills)}".lower()
        if terms and not all(t in hay for t in terms):
            continue
        if min_budget is not None and (j.budget_max or j.hourly_max or 0) < min_budget:
            continue
        if posted_after and sim.tick_to_iso(j.release_tick, j.posted_offset_hours) <= posted_after:
            continue
        out.append(j)
    page = out[cursor:cursor + limit]
    nxt = cursor + limit if cursor + limit < len(out) else None
    return JobPage(items=[_job_out(session, j) for j in page], next_cursor=nxt,
                   now=sim.tick_to_iso(tick))


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, account: Account = Depends(current_account),
            session: Session = Depends(db.get_session)) -> JobOut:
    return _job_out(session, _visible_job(session, job_id))


@app.get("/jobs/{job_id}/connects-quote")
def connects_quote(job_id: str, account: Account = Depends(current_account),
                   session: Session = Depends(db.get_session)) -> dict[str, Any]:
    """What applying would cost right now. Lets the Marketplace MCP server build dry runs."""
    job = _visible_job(session, job_id)
    cost, applied = sim.job_connects_cost(session, job)
    return {"job_id": job_id, "connects_cost": cost, "multipliers_applied": applied,
            "balance": account.connects_balance,
            "balance_after": account.connects_balance - cost,
            "policy_version": sim.state(session).policy_version}


@app.get("/clients/{client_id}/history", response_model=ClientHistoryOut)
def client_history(client_id: str, account: Account = Depends(current_account),
                   session: Session = Depends(db.get_session)) -> ClientHistoryOut:
    client = session.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Client not found")
    contracts = session.scalars(select(PastContract).where(PastContract.client_id == client_id)
                                .order_by(PastContract.id)).all()
    tick = sim.state(session).tick
    open_jobs = session.scalar(select(func.count()).select_from(Job).where(
        Job.client_id == client_id, Job.status == "open", Job.release_tick <= tick))
    return ClientHistoryOut(
        client=_client_summary(client), industry=client.industry, enterprise=client.enterprise,
        past_contracts=[PastContractOut(title=c.title, amount=c.amount,
                                        payment_type=c.payment_type, rating_given=c.rating_given,
                                        review=c.review, freelancer_feedback=c.freelancer_feedback,
                                        ended_at=c.ended_at) for c in contracts],
        open_jobs=open_jobs or 0,
    )


@app.get("/market/stats", response_model=list[MarketStatOut])
def market_stats(domain: str | None = None, experience_level: str | None = None,
                 payment_type: str | None = None, account: Account = Depends(current_account),
                 session: Session = Depends(db.get_session)) -> list[MarketStatOut]:
    stmt = select(MarketStat)
    if domain:
        stmt = stmt.where(MarketStat.domain == domain)
    if experience_level:
        stmt = stmt.where(MarketStat.experience_level == experience_level)
    if payment_type:
        stmt = stmt.where(MarketStat.payment_type == payment_type)
    rows = session.scalars(stmt.order_by(MarketStat.id)).all()
    return [MarketStatOut(domain=r.domain, experience_level=r.experience_level,
                          payment_type=r.payment_type, n=r.n, p25=r.p25, p50=r.p50, p75=r.p75,
                          unit="USD per hour" if r.payment_type == "hourly" else "USD",
                          source=r.source) for r in rows]


@app.post("/proposals", response_model=ProposalOut, status_code=201)
def create_proposal(body: ProposalCreate, response: Response,
                    key: str = Depends(require_idempotency_key),
                    account: Account = Depends(current_account),
                    session: Session = Depends(db.get_session)) -> Any:
    def run() -> tuple[int, dict]:
        job = _visible_job(session, body.job_id)
        rules = load_policy(sim.state(session).policy_version)["proposals"]
        if not rules["min_cover_letter_chars"] <= len(body.cover_letter) <= rules[
                "max_cover_letter_chars"]:
            raise HTTPException(422, "Cover letter length is outside the policy limits")
        if any(len(a) > rules["max_screening_answer_chars"] for a in body.screening_answers):
            raise HTTPException(422, "A screening answer is longer than the policy allows")
        if body.bid_type != job.payment_type:
            raise HTTPException(422, f"This job is {job.payment_type}; bid_type must match")
        p = Proposal(proposal_id=sim.next_id(session, "prop"), account_id=account.account_id,
                     job_id=job.job_id, cover_letter=body.cover_letter,
                     bid_amount=body.bid_amount, bid_type=body.bid_type,
                     screening_answers=body.screening_answers, duration_days=body.duration_days,
                     status="draft", stage="drafted", created_tick=sim.state(session).tick)
        session.add(p)
        session.flush()
        return 201, _proposal_out(session, p).model_dump()

    return _replay_or_run(session, account.account_id, key, "POST /proposals",
                          body.model_dump(), run, response)


@app.get("/proposals", response_model=list[ProposalOut])
def list_proposals(account: Account = Depends(current_account),
                   session: Session = Depends(db.get_session)) -> list[ProposalOut]:
    rows = session.scalars(select(Proposal).where(Proposal.account_id == account.account_id)
                           .order_by(Proposal.proposal_id)).all()
    return [_proposal_out(session, p) for p in rows]


@app.get("/proposals/{proposal_id}", response_model=ProposalOut)
def get_proposal(proposal_id: str, account: Account = Depends(current_account),
                 session: Session = Depends(db.get_session)) -> ProposalOut:
    p = session.get(Proposal, proposal_id)
    if p is None or p.account_id != account.account_id:
        raise HTTPException(404, "Proposal not found")
    return _proposal_out(session, p)


@app.post("/proposals/{proposal_id}/submit", response_model=ProposalOut)
def submit_proposal(proposal_id: str, response: Response,
                    key: str = Depends(require_idempotency_key),
                    account: Account = Depends(current_account),
                    session: Session = Depends(db.get_session)) -> Any:
    def run() -> tuple[int, dict]:
        p = session.get(Proposal, proposal_id)
        if p is None or p.account_id != account.account_id:
            raise HTTPException(404, "Proposal not found")
        if p.status != "draft":
            raise HTTPException(409, "Proposal was already submitted")
        job = _visible_job(session, p.job_id)
        already = session.scalar(select(Proposal).where(
            Proposal.account_id == account.account_id, Proposal.job_id == job.job_id,
            Proposal.status == "submitted"))
        if already is not None:
            raise HTTPException(409, "You already applied to this job")
        cost, applied = sim.job_connects_cost(session, job)
        if account.connects_balance < cost:
            raise HTTPException(409, f"Insufficient Connects: need {cost}, have "
                                     f"{account.connects_balance}")
        tick = sim.state(session).tick
        client = session.get(Client, job.client_id)
        responsiveness = float((client.hidden_prefs or {}).get("responsiveness", 0.7))
        account.connects_balance -= cost
        p.status, p.stage = "submitted", "submitted"
        p.status_submitted_guard = job.job_id
        p.connects_spent = cost
        p.submitted_tick = tick
        p.decide_at_tick = tick + 1 + int((1 - responsiveness) * 4)
        job.applicants += 1
        sim.emit(session, account.account_id, "proposal_submitted", tick, job_id=job.job_id,
                 proposal_id=p.proposal_id,
                 payload={"connects_spent": cost, "multipliers_applied": applied})
        session.flush()
        return 200, _proposal_out(session, p).model_dump()

    return _replay_or_run(session, account.account_id, key, f"POST /proposals/{proposal_id}/submit",
                          {"proposal_id": proposal_id}, run, response)


@app.get("/threads", response_model=list[ThreadOut])
def list_threads(account: Account = Depends(current_account),
                 session: Session = Depends(db.get_session)) -> list[ThreadOut]:
    out = []
    for t in session.scalars(select(Thread).where(Thread.account_id == account.account_id)
                             .order_by(Thread.thread_id)).all():
        msgs = session.scalars(select(Message).where(Message.thread_id == t.thread_id)
                               .order_by(Message.id)).all()
        job = session.get(Job, t.job_id)
        out.append(ThreadOut(thread_id=t.thread_id, client_id=t.client_id, job_id=t.job_id,
                             job_title=job.title, proposal_id=t.proposal_id,
                             stage=sim.thread_stage(session, t),
                             last_message_at=sim.tick_to_iso(msgs[-1].tick) if msgs else None,
                             messages=len(msgs)))
    return out


def _own_thread(session: Session, account: Account, thread_id: str) -> Thread:
    t = session.get(Thread, thread_id)
    if t is None or t.account_id != account.account_id:
        raise HTTPException(404, "Thread not found")
    return t


@app.get("/threads/{thread_id}/messages", response_model=list[MessageOut])
def list_messages(thread_id: str, account: Account = Depends(current_account),
                  session: Session = Depends(db.get_session)) -> list[MessageOut]:
    _own_thread(session, account, thread_id)
    msgs = session.scalars(select(Message).where(Message.thread_id == thread_id)
                           .order_by(Message.id)).all()
    return [MessageOut(id=m.id, sender=m.sender, body=m.body, sent_at=sim.tick_to_iso(m.tick))
            for m in msgs]


@app.post("/threads/{thread_id}/messages", response_model=MessageOut, status_code=201)
def send_message(thread_id: str, body: MessageIn, response: Response,
                 key: str = Depends(require_idempotency_key),
                 account: Account = Depends(current_account),
                 session: Session = Depends(db.get_session)) -> Any:
    def run() -> tuple[int, dict]:
        t = _own_thread(session, account, thread_id)
        if t.hidden_state == "closed":
            raise HTTPException(409, "This conversation is closed")
        tick = sim.state(session).tick
        m = Message(thread_id=thread_id, sender="freelancer", body=body.body, tick=tick)
        session.add(m)
        client = session.get(Client, t.client_id)
        responsiveness = float((client.hidden_prefs or {}).get("responsiveness", 0.7))
        t.respond_at_tick = tick + 1 + int((1 - responsiveness) * 2)
        sim.emit(session, account.account_id, "message_sent", tick, job_id=t.job_id,
                 proposal_id=t.proposal_id, thread_id=thread_id)
        session.flush()
        return 201, MessageOut(id=m.id, sender="freelancer", body=m.body,
                               sent_at=sim.tick_to_iso(tick)).model_dump()

    return _replay_or_run(session, account.account_id, key, f"POST /threads/{thread_id}/messages",
                          body.model_dump(), run, response)


@app.get("/events", response_model=list[EventOut])
def list_events(since: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1,
                                                                        le=500),
                account: Account = Depends(current_account),
                session: Session = Depends(db.get_session)) -> list[EventOut]:
    rows = session.scalars(select(Event).where(Event.account_id == account.account_id,
                                               Event.id > since)
                           .order_by(Event.id).limit(limit)).all()
    return [EventOut(id=e.id, type=e.type, at=sim.tick_to_iso(e.tick), job_id=e.job_id,
                     proposal_id=e.proposal_id, thread_id=e.thread_id, payload=e.payload)
            for e in rows]


# --------------------------------------------------------------------------- admin routes


class SeedIn(BaseModel):
    world_path: str


class TickIn(BaseModel):
    n: int = Field(default=1, ge=1, le=24 * 30)


class FaultsIn(BaseModel):
    error_rate: float | None = Field(default=None, ge=0, le=1)
    post_commit_error_rate: float | None = Field(default=None, ge=0, le=1)
    latency_ms_max: int | None = Field(default=None, ge=0, le=30000)
    down: bool | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=100000)
    reseed: bool = False


class PolicyIn(BaseModel):
    version: str


class ConnectsIn(BaseModel):
    balance: int = Field(ge=0)


@app.post("/admin/seed", dependencies=[Depends(require_admin)])
def admin_seed(body: SeedIn, session: Session = Depends(db.get_session)) -> dict[str, int]:
    return load_world(session, Path(body.world_path))


@app.post("/admin/tick", dependencies=[Depends(require_admin)])
def admin_tick(body: TickIn, session: Session = Depends(db.get_session)) -> dict[str, int]:
    return sim.advance(session, body.n)


@app.get("/admin/state", dependencies=[Depends(require_admin)])
def admin_state(request: Request, session: Session = Depends(db.get_session)) -> dict[str, Any]:
    st = sim.state(session)
    count = lambda model: session.scalar(select(func.count()).select_from(model))  # noqa: E731
    return {"tick": st.tick, "now": sim.tick_to_iso(st.tick), "policy_version": st.policy_version,
            "jobs": count(Job), "proposals": count(Proposal), "threads": count(Thread),
            "events": count(Event), "faults": request.app.state.faults.as_dict(),
            "post_commit_error_rate": request.app.state.post_commit_error_rate}


@app.get("/admin/faults", dependencies=[Depends(require_admin)])
def get_faults(request: Request) -> dict[str, Any]:
    return {**request.app.state.faults.as_dict(),
            "post_commit_error_rate": request.app.state.post_commit_error_rate}


@app.put("/admin/faults", dependencies=[Depends(require_admin)])
def set_faults(body: FaultsIn, request: Request) -> dict[str, Any]:
    f: FaultState = request.app.state.faults
    if body.error_rate is not None:
        f.error_rate = body.error_rate
    if body.latency_ms_max is not None:
        f.latency_ms_max = body.latency_ms_max
    if body.down is not None:
        f.down = body.down
    if body.rate_limit_per_minute is not None:
        f.rate_limit_per_minute = body.rate_limit_per_minute
        request.app.state.limiter.set_rate(body.rate_limit_per_minute)
    if body.post_commit_error_rate is not None:
        request.app.state.post_commit_error_rate = body.post_commit_error_rate
    if body.reseed:
        f.reseed()
    return get_faults(request)


@app.put("/admin/policy", dependencies=[Depends(require_admin)])
def set_policy(body: PolicyIn, session: Session = Depends(db.get_session)) -> dict[str, Any]:
    if body.version not in available_versions():
        raise HTTPException(404, f"Unknown policy; available: {available_versions()}")
    st = sim.state(session)
    st.policy_version = body.version
    session.commit()
    return {"policy_version": st.policy_version, "policy": load_policy(body.version)}


@app.put("/admin/accounts/{account_id}/connects", dependencies=[Depends(require_admin)])
def set_connects(account_id: str, body: ConnectsIn,
                 session: Session = Depends(db.get_session)) -> dict[str, Any]:
    acct = session.get(Account, account_id)
    if acct is None:
        raise HTTPException(404, "Account not found")
    acct.connects_balance = body.balance
    session.commit()
    return {"account_id": account_id, "balance": acct.connects_balance}


@app.get("/admin/proposals/{proposal_id}/decision", dependencies=[Depends(require_admin)])
def proposal_decision(proposal_id: str, session: Session = Depends(db.get_session)) -> dict:
    """The simulated client's hidden reasoning, for failure analysis. Never shown to agents."""
    p = session.get(Proposal, proposal_id)
    if p is None:
        raise HTTPException(404, "Proposal not found")
    return {"proposal_id": proposal_id, "stage": p.stage, "decision": p.hidden_decision}


@app.get("/admin/threads/{thread_id}/gold", dependencies=[Depends(require_admin)])
def thread_gold(thread_id: str, session: Session = Depends(db.get_session)) -> dict:
    """Gold intents of client messages, for evaluating the intent classifier."""
    msgs = session.scalars(select(Message).where(Message.thread_id == thread_id)
                           .order_by(Message.id)).all()
    return {"thread_id": thread_id,
            "messages": [{"id": m.id, "sender": m.sender, "intent": m.hidden_intent}
                         for m in msgs]}
