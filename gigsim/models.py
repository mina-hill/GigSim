"""GigSim tables. Columns named hidden_* are never returned by the public API."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from gigsim.db import Base


class SimState(Base):
    __tablename__ = "sim_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tick: Mapped[int] = mapped_column(Integer, default=0)
    policy_version: Mapped[str] = mapped_column(String(32), default="v1")
    seq: Mapped[int] = mapped_column(Integer, default=0)  # id counter for proposals/threads


class Account(Base):
    """A freelancer account (one per persona, plus any user-created ones)."""

    __tablename__ = "accounts"
    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    persona: Mapped[str] = mapped_column(String(64), default="")
    connects_balance: Mapped[int] = mapped_column(Integer, default=80)
    weekly_connects: Mapped[int] = mapped_column(Integer, default=80)
    last_refill_tick: Mapped[int] = mapped_column(Integer, default=0)


class Client(Base):
    __tablename__ = "clients"
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(64), default="")
    industry: Mapped[str] = mapped_column(String(64), default="")
    payment_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    member_since: Mapped[str] = mapped_column(String(32), default="")
    total_spent: Mapped[float] = mapped_column(Float, default=0.0)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    reviews_count: Mapped[int] = mapped_column(Integer, default=0)
    hire_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    jobs_posted: Mapped[int] = mapped_column(Integer, default=0)
    enterprise: Mapped[bool] = mapped_column(Boolean, default=False)
    # Hidden preferences: dislikes_generic_opening, preferred_length, responsiveness,
    # price_tolerance, strictness, scammer, scam_script
    hidden_prefs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PastContract(Base):
    __tablename__ = "past_contracts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.client_id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    payment_type: Mapped[str] = mapped_column(String(16), default="fixed")
    rating_given: Mapped[float | None] = mapped_column(Float, nullable=True)
    review: Mapped[str] = mapped_column(Text, default="")
    freelancer_feedback: Mapped[str] = mapped_column(Text, default="")
    ended_at: Mapped[str] = mapped_column(String(32), default="")


class Job(Base):
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.client_id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    domain: Mapped[str] = mapped_column(String(32), default="other", index=True)
    experience_level: Mapped[str] = mapped_column(String(32), default="intermediate")
    payment_type: Mapped[str] = mapped_column(String(16), default="fixed")
    budget_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    hourly_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    hourly_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration: Mapped[str] = mapped_column(String(64), default="")
    screening_questions: Mapped[list[str]] = mapped_column(JSON, default=list)
    applicants: Mapped[int] = mapped_column(Integer, default=0)
    base_connects: Mapped[int] = mapped_column(Integer, default=8)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    release_tick: Mapped[int] = mapped_column(Integer, default=0, index=True)
    posted_offset_hours: Mapped[int] = mapped_column(Integer, default=2)
    # Hidden: must_have_skills, budget_ceiling (fixed) / hourly_ceiling (hourly)
    hidden: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    hidden_provenance: Mapped[str] = mapped_column(String(128), default="")


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("account_id", "job_id", "status_submitted_guard"),)
    proposal_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), index=True)
    cover_letter: Mapped[str] = mapped_column(Text)
    bid_amount: Mapped[float] = mapped_column(Float)
    bid_type: Mapped[str] = mapped_column(String(16), default="fixed")
    screening_answers: Mapped[list[str]] = mapped_column(JSON, default=list)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | submitted
    # Only one submitted proposal per account and job: the guard is the job id when submitted
    status_submitted_guard: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage: Mapped[str] = mapped_column(String(16), default="drafted")
    connects_spent: Mapped[int] = mapped_column(Integer, default=0)
    created_tick: Mapped[int] = mapped_column(Integer, default=0)
    submitted_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decide_at_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hidden_decision: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Thread(Base):
    __tablename__ = "threads"
    thread_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.account_id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.client_id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"))
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.proposal_id"))
    # state machine for the simulated client: awaiting_answer | awaiting_slots |
    # negotiating | interview_scheduled | closed
    hidden_state: Mapped[str] = mapped_column(String(32), default="awaiting_answer")
    respond_at_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.thread_id"), index=True)
    sender: Mapped[str] = mapped_column(String(16))  # client | freelancer
    body: Mapped[str] = mapped_column(Text)
    tick: Mapped[int] = mapped_column(Integer)
    # Gold intent of client messages, for evaluating the intent classifier (admin only)
    hidden_intent: Mapped[str] = mapped_column(String(32), default="")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(32))
    tick: Mapped[int] = mapped_column(Integer)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency"
    __table_args__ = (UniqueConstraint("account_id", "key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(128))
    endpoint: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)


class MarketStat(Base):
    __tablename__ = "market_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    experience_level: Mapped[str] = mapped_column(String(32))
    payment_type: Mapped[str] = mapped_column(String(16))
    n: Mapped[int] = mapped_column(Integer)
    p25: Mapped[float] = mapped_column(Float)
    p50: Mapped[float] = mapped_column(Float)
    p75: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(64), default="gigsim_history")


class LLMDecisionCache(Base):
    """Caches LLM client judgements so reply rate is reproducible across runs."""

    __tablename__ = "llm_decision_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
