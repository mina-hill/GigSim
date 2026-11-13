"""Simulated time and what happens on each tick: Connects refills, client decisions on
submitted proposals, client replies in conversations. One tick = `tick_hours` simulated hours.
Everything is processed in id order, so a rerun from the same state gives the same result."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gigsim import clients as client_sim
from gigsim.models import (
    Account,
    Client,
    Event,
    Job,
    LLMDecisionCache,
    Message,
    Proposal,
    SimState,
    Thread,
)
from gigsim.policy import connects_cost, load_policy
from gigsim.settings import get_settings

CLOSE_AFTER_TICKS = 48   # a proposal nobody answered is "lost" two simulated days later


def state(session: Session) -> SimState:
    st = session.get(SimState, 1)
    if st is None:
        st = SimState(id=1, tick=0, policy_version=get_settings().policy_version, seq=0)
        session.add(st)
        session.flush()
    return st


def tick_to_iso(tick: int, offset_hours: int = 0) -> str:
    s = get_settings()
    base = datetime.fromisoformat(s.base_time)
    return (base + timedelta(hours=tick * s.tick_hours - offset_hours)).isoformat()


def next_id(session: Session, prefix: str) -> str:
    st = state(session)
    st.seq += 1
    return f"{prefix}_{st.seq:06d}"


def job_dict(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id, "title": job.title, "description": job.description,
        "skills": job.skills, "domain": job.domain, "payment_type": job.payment_type,
        "budget_min": job.budget_min, "budget_max": job.budget_max,
        "hourly_min": job.hourly_min, "hourly_max": job.hourly_max,
        "screening_questions": job.screening_questions, "applicants": job.applicants,
        "hidden": job.hidden or {},
    }


def job_age_hours(job: Job, now_tick: int) -> int:
    hours = max(0, (now_tick - job.release_tick) * get_settings().tick_hours)
    return hours + job.posted_offset_hours


def job_connects_cost(session: Session, job: Job) -> tuple[int, list[str]]:
    st = state(session)
    policy = load_policy(st.policy_version)
    return connects_cost(policy, {
        "base_connects": job.base_connects, "age_hours": job_age_hours(job, st.tick),
        "applicants": job.applicants, "domain": job.domain, "budget_max": job.budget_max,
    })


def emit(session: Session, account_id: str, type_: str, tick: int, **kw: Any) -> None:
    session.add(Event(account_id=account_id, type=type_, tick=tick,
                      job_id=kw.get("job_id"), proposal_id=kw.get("proposal_id"),
                      thread_id=kw.get("thread_id"), payload=kw.get("payload", {})))


# --------------------------------------------------------------------------- decisions


def decide(session: Session, proposal: Proposal) -> client_sim.Decision:
    s = get_settings()
    job = session.get(Job, proposal.job_id)
    client = session.get(Client, job.client_id)
    jd = job_dict(job)
    decision = client_sim.score_proposal(
        job=jd, prefs=client.hidden_prefs or {}, cover_letter=proposal.cover_letter,
        screening_answers=proposal.screening_answers, bid_amount=proposal.bid_amount,
        proposal_id=proposal.proposal_id, seed=s.seed, applicants=job.applicants,
    )
    if s.client_mode == "llm" and decision.outcome not in {"ignored"} and not (
        client.hidden_prefs or {}
    ).get("scammer"):
        key = client_sim.cache_key(job.job_id, proposal.cover_letter, proposal.screening_answers,
                                   proposal.bid_amount, s.llm_model)
        cached = session.get(LLMDecisionCache, key)
        if cached is None:
            score, reason = client_sim.llm_score(
                job=jd, prefs=client.hidden_prefs or {}, cover_letter=proposal.cover_letter,
                screening_answers=proposal.screening_answers, bid_amount=proposal.bid_amount,
                base_url=s.llm_base_url, model=s.llm_model, api_key_env=s.llm_api_key_env,
            )
            cached = LLMDecisionCache(key=key, score=score, reason=reason)
            session.add(cached)
        blended = 0.5 * decision.score + 0.5 * cached.score
        decision.llm_score = cached.score
        decision.score = blended
        decision.reasons.append(f"llm:{cached.reason}")
        decision.outcome = ("interview" if blended >= 0.72 else "replied" if blended >= 0.58
                            else "viewed" if blended >= 0.35 else "ignored")
    return decision


def _open_thread(session: Session, proposal: Proposal, decision: client_sim.Decision,
                 tick: int) -> None:
    s = get_settings()
    job = session.get(Job, proposal.job_id)
    client = session.get(Client, job.client_id)
    body, intent, thread_state = client_sim.first_message(
        decision, job=job_dict(job), prefs=client.hidden_prefs or {}, bid=proposal.bid_amount,
        seed=s.seed, proposal_id=proposal.proposal_id,
    )
    thread = Thread(thread_id=next_id(session, "thr"), account_id=proposal.account_id,
                    client_id=client.client_id, job_id=job.job_id,
                    proposal_id=proposal.proposal_id, hidden_state=thread_state)
    session.add(thread)
    session.flush()
    session.add(Message(thread_id=thread.thread_id, sender="client", body=body, tick=tick,
                        hidden_intent=intent))
    proposal.stage = "replied"
    emit(session, proposal.account_id, "client_replied", tick, job_id=job.job_id,
         proposal_id=proposal.proposal_id, thread_id=thread.thread_id)
    emit(session, proposal.account_id, "message_received", tick, job_id=job.job_id,
         proposal_id=proposal.proposal_id, thread_id=thread.thread_id)


def _process_decisions(session: Session, tick: int) -> int:
    due = session.scalars(
        select(Proposal).where(Proposal.status == "submitted", Proposal.decide_at_tick == tick)
        .order_by(Proposal.proposal_id)
    ).all()
    for p in due:
        d = decide(session, p)
        p.hidden_decision = {**d.as_dict(), "decided_tick": tick}
        if d.outcome == "ignored":
            p.hidden_decision["close_at"] = tick + CLOSE_AFTER_TICKS
            continue
        p.stage = "viewed"
        emit(session, p.account_id, "proposal_viewed", tick, job_id=p.job_id,
             proposal_id=p.proposal_id)
        if d.outcome == "viewed" or d.ghost:
            p.hidden_decision["close_at"] = tick + CLOSE_AFTER_TICKS
            continue
        _open_thread(session, p, d, tick)
    return len(due)


def _process_closures(session: Session, tick: int) -> int:
    open_props = session.scalars(
        select(Proposal).where(Proposal.status == "submitted",
                               Proposal.stage.in_(["submitted", "viewed"]))
        .order_by(Proposal.proposal_id)
    ).all()
    closed = 0
    for p in open_props:
        close_at = (p.hidden_decision or {}).get("close_at")
        if close_at is not None and close_at <= tick:
            p.stage = "lost"
            emit(session, p.account_id, "proposal_lost", tick, job_id=p.job_id,
                 proposal_id=p.proposal_id, payload={"reason": "no_response"})
            closed += 1
    return closed


def _process_threads(session: Session, tick: int) -> int:
    s = get_settings()
    due = session.scalars(
        select(Thread).where(Thread.respond_at_tick == tick).order_by(Thread.thread_id)
    ).all()
    for t in due:
        t.respond_at_tick = None
        msgs = session.scalars(select(Message).where(Message.thread_id == t.thread_id)
                               .order_by(Message.id)).all()
        last_freelancer = next((m for m in reversed(msgs) if m.sender == "freelancer"), None)
        if last_freelancer is None and t.hidden_state != "interview_scheduled":
            continue
        freelancer_text = last_freelancer.body if last_freelancer else ""
        proposal = session.get(Proposal, t.proposal_id)
        job = session.get(Job, t.job_id)
        client = session.get(Client, t.client_id)
        body, intent, new_state, stage = client_sim.respond_to_freelancer(
            state=t.hidden_state, freelancer_text=freelancer_text, job=job_dict(job),
            prefs=client.hidden_prefs or {},
            decision_score=float((proposal.hidden_decision or {}).get("score", 0.5)),
            bid=proposal.bid_amount, seed=s.seed, thread_id=t.thread_id, turn=len(msgs),
        )
        t.hidden_state = new_state
        session.add(Message(thread_id=t.thread_id, sender="client", body=body, tick=tick,
                            hidden_intent=intent))
        emit(session, t.account_id, "message_received", tick, job_id=t.job_id,
             proposal_id=t.proposal_id, thread_id=t.thread_id)
        if stage == "interview":
            proposal.stage = "interview"
            emit(session, t.account_id, "interview_scheduled", tick, job_id=t.job_id,
                 proposal_id=t.proposal_id, thread_id=t.thread_id)
            t.respond_at_tick = tick + 24   # the call happens; the client decides a day later
        elif stage in {"hired", "lost"}:
            proposal.stage = stage
            emit(session, t.account_id, f"proposal_{stage}", tick, job_id=t.job_id,
                 proposal_id=t.proposal_id, thread_id=t.thread_id)
    return len(due)


def _refill_connects(session: Session, tick: int) -> None:
    policy = load_policy(state(session).policy_version)
    every = int(policy["connects"]["refill_every_ticks"])
    for acct in session.scalars(select(Account).order_by(Account.account_id)).all():
        if tick - acct.last_refill_tick >= every:
            acct.connects_balance = acct.weekly_connects
            acct.last_refill_tick = tick
            emit(session, acct.account_id, "connects_refilled", tick,
                 payload={"balance": acct.connects_balance})


def advance(session: Session, n: int = 1) -> dict[str, int]:
    """Advance simulated time by n ticks and process everything that falls due."""
    st = state(session)
    summary = {"decisions": 0, "client_messages": 0, "closed": 0, "released_jobs": 0}
    for _ in range(n):
        st.tick += 1
        tick = st.tick
        _refill_connects(session, tick)
        summary["decisions"] += _process_decisions(session, tick)
        summary["client_messages"] += _process_threads(session, tick)
        summary["closed"] += _process_closures(session, tick)
        summary["released_jobs"] += len(session.scalars(
            select(Job.job_id).where(Job.release_tick == tick)).all())
        session.flush()
    session.commit()
    summary["tick"] = st.tick
    return summary


def thread_stage(session: Session, thread: Thread) -> str:
    p = session.get(Proposal, thread.proposal_id)
    return p.stage if p else "unknown"
