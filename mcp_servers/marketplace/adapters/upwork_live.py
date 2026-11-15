"""Marketplace port implementation for the official Upwork MCP server (live mode).

The graph sees the same MarketplacePort as with GigSim. Field mappings follow Upwork's own
tool reference (get_tool_help), saved by the connect command in .cache/upwork/reference.json.

Write rules in live mode:
  * create_proposal_draft asks Upwork to prepare a proposal preview (manage_proposals create).
    Upwork stores it; nothing is submitted and no Connects are spent.
  * submit_proposal never calls confirm_preview. The person confirms the preview separately,
    with the connect command's `confirm`, which shows Upwork's preview and asks them first.
  * send_message is refused. Replies approved in GigBid are sent by the person on Upwork.
Reads are paced (one call at a time, a minimum gap between calls) so GigBid never polls
Upwork the way a scraper would.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from mcp import ClientSession

from mcp_servers.marketplace.port import (
    ClientHistory,
    ClientSummary,
    ConnectsBalance,
    ConnectsQuote,
    Event,
    Job,
    JobPage,
    MarketplaceError,
    MarketStat,
    Message,
    MessageBody,
    PastContract,
    PaymentType,
    Proposal,
    ProposalInput,
    Thread,
)
from scripts.data.text import tag_domain

POLICY_VERSION = "upwork-live"
MAX_COVER_LETTER = 5000
UPWORK_PAGE_LIMIT = 10
UNTRUSTED_TAG = re.compile(r"</?untrusted_participant_content>")
MONEY = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
SAFE_ID = re.compile(r"^[\w~\-][\w.~\-]*$")
CONFIRM_HINT = (
    "uv run python -m mcp_servers.marketplace.adapters.upwork_connect confirm {preview_id}"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean(text: Any) -> str:
    return UNTRUSTED_TAG.sub("", str(text or "")).strip()


def _money(text: Any) -> float | None:
    if isinstance(text, int | float):
        return float(text)
    match = MONEY.search(str(text or ""))
    return float(match.group(1).replace(",", "")) if match else None


def _dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for inner in value.values():
            yield from _dicts(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _dicts(inner)


def _find(value: Any, *keys: str) -> Any:
    """The first value stored under any of `keys`, anywhere in a nested response."""
    for item in _dicts(value):
        for key in keys:
            if item.get(key) not in (None, ""):
                return item[key]
    return None


def parse_budget(text: str, job_type: str) -> dict[str, float | None]:
    """'$10.00–$15.00/hr', '$25.00+/hr', 'up to $30.00/hr' or '$500.00' (fixed)."""
    values = [float(v.replace(",", "")) for v in MONEY.findall(text or "")]
    low = high = None
    if values:
        if "up to" in (text or "").lower():
            high = values[0]
        elif len(values) >= 2:
            low, high = values[0], values[1]
        elif "+" in (text or ""):
            low = values[0]
        else:
            low = high = values[0]
    if job_type == "hourly":
        return {"hourly_min": low, "hourly_max": high, "budget_min": None, "budget_max": None}
    return {"budget_min": low, "budget_max": high, "hourly_min": None, "hourly_max": None}


def applicants_from_tier(row: dict[str, Any]) -> int:
    if isinstance(row.get("proposal_count"), int):
        return row["proposal_count"]
    tier = str(row.get("proposals_tier") or "").lower()
    if tier.startswith("fewer than"):
        return 0
    first = re.search(r"\d+", tier)
    return int(first.group()) if first else 0


def _experience(value: Any) -> str:
    text = str(value or "").lower().replace("_", " ")
    if "entry" in text:
        return "entry"
    if "expert" in text:
        return "expert"
    return "intermediate" if text else ""


def client_id_for(job_id: str) -> str:
    """Upwork search rows carry no client id, so a client is named by one of its jobs."""
    return f"job-{job_id}"


class UpworkAdapter:
    def __init__(
        self,
        session: ClientSession,
        org_uid: str,
        *,
        min_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session = session
        self._org = org_uid
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_call: float | None = None
        self._rows: dict[str, dict[str, Any]] = {}
        self._cursors: dict[int, tuple[str, str]] = {}
        self._drafts: dict[str, Proposal] = {}
        self._drafts_by_key: dict[str, Proposal] = {}

    async def _call(self, tool: str, action: str, params: dict[str, Any] | None = None) -> Any:
        async with self._lock:
            if self._last_call is not None:
                wait = self._min_interval - (self._clock() - self._last_call)
                if wait > 0:
                    await self._sleep(wait)
            try:
                result = await self._session.call_tool(
                    f"upwork__{tool}",
                    {"action": action, "org_uid": self._org, "params": params or {}},
                )
            finally:
                self._last_call = self._clock()
        text = "\n".join(getattr(part, "text", "") for part in result.content)
        if result.isError:
            raise MarketplaceError(f"Upwork refused {tool} {action}: {_clean(text)[:300]}")
        try:
            data = json.loads(text)
        except ValueError:
            raise MarketplaceError(f"Upwork returned an unreadable {tool} response.") from None
        if isinstance(data, dict) and data.get("status") not in (None, "ok"):
            message = data.get("message") or data.get("error") or data.get("status")
            raise MarketplaceError(f"Upwork {tool} {action} failed: {_clean(message)[:300]}")
        return data

    # ------------------------------------------------------------------ jobs

    def _job_from_row(self, row: dict[str, Any]) -> Job:
        job_id = str(row["id"])
        job_type = "hourly" if str(row.get("job_type", "")).lower() == "hourly" else "fixed"
        client = row.get("client") or {}
        title = _clean(row.get("title"))
        description = _clean(row.get("description") or row.get("description_snippet"))
        skills = [str(s) for s in row.get("skills") or []]
        return Job(
            job_id=job_id,
            title=title,
            description=description,
            skills=skills,
            domain=tag_domain(title, description, skills),
            experience_level=_experience(row.get("experience_level")),
            payment_type=job_type,
            **parse_budget(str(row.get("budget") or ""), job_type),
            duration=str(row.get("duration") or ""),
            screening_questions=[],
            applicants=applicants_from_tier(row),
            connects_cost=int(row.get("connects_cost") or 0),
            status="applied" if row.get("applied") else "open",
            posted_at=str(row.get("published_date") or row.get("created_date") or ""),
            client=ClientSummary(
                client_id=client_id_for(job_id),
                country=str(client.get("country") or ""),
                payment_verified=client.get("verification_status") == "VERIFIED",
                total_spent=_money(client.get("total_spent")) or 0.0,
                rating=client.get("rating"),
                reviews_count=int(client.get("total_reviews") or 0),
                hire_rate=None,
                jobs_posted=int(client.get("total_posted_jobs") or 0),
                member_since="",
            ),
        )

    async def search_jobs(
        self,
        *,
        q: str | None = None,
        domain: str | None = None,
        payment_type: PaymentType | None = None,
        min_budget: float | None = None,
        posted_after: str | None = None,
        limit: int = 20,
        cursor: int = 0,
    ) -> JobPage:
        """Keyword search across Upwork, or the freelancer's newest matching jobs without q.

        domain is GigBid's own label and is applied to the mapped results, not sent upstream.
        """
        params: dict[str, Any] = {"limit": max(1, min(limit, UPWORK_PAGE_LIMIT))}
        if q:
            action = "search"
            params["query"] = q
            if payment_type:
                params["job_type"] = payment_type
            if min_budget and payment_type == "hourly":
                params["rate_min"] = min_budget
            elif min_budget:
                params["budget_min"] = min_budget
        else:
            action = "smart_search"
            params["mode"] = "most_recent"
            if posted_after:
                params["from_date"] = posted_after
        if cursor:
            if cursor not in self._cursors or self._cursors[cursor][0] != action:
                raise MarketplaceError("That page of Upwork results has expired. Search again.")
            params["cursor"] = self._cursors[cursor][1]
        data = await self._call("find_jobs", action, params)
        rows = [r for r in (data.get("jobs") or []) if isinstance(r, dict) and r.get("id")]
        jobs = []
        for row in rows:
            self._rows[str(row["id"])] = row
            job = self._job_from_row(row)
            if domain and job.domain != domain:
                continue
            if payment_type and job.payment_type != payment_type:
                continue
            jobs.append(job)
        upstream = data.get("next_cursor") or _find(data.get("pageInfo"), "endCursor")
        more = data.get("hasMore") or _find(data.get("pageInfo"), "hasNextPage")
        next_cursor = None
        if upstream and more:
            next_cursor = len(self._cursors) + 1
            self._cursors[next_cursor] = (action, str(upstream))
        return JobPage(items=jobs, next_cursor=next_cursor, now=_now())

    async def _job_details(self, job_id: str) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(job_id):
            raise MarketplaceError("An Upwork job id must be one plain identifier.")
        return await self._call("find_jobs", "get", {"id": job_id})

    async def get_job(self, job_id: str) -> Job:
        details = await self._job_details(job_id)
        posting = _find(details, "marketplaceJobPosting") or {}
        content = posting.get("content") or {}
        terms = posting.get("contractTerms") or {}
        row = dict(self._rows.get(job_id) or {"id": job_id})
        row["title"] = content.get("title") or row.get("title")
        row["description"] = content.get("description") or row.get("description_snippet")
        contract_type = str(terms.get("contractType") or row.get("job_type") or "").lower()
        row["job_type"] = "hourly" if contract_type == "hourly" else "fixed"
        row["experience_level"] = terms.get("experienceLevel") or row.get("experience_level")
        row["connects_cost"] = details.get("connects_cost") or 0
        row["applied"] = details.get("applied")
        job = self._job_from_row(row)
        hourly = terms.get("hourlyContractTerms") or {}
        fixed = terms.get("fixedPriceContractTerms") or {}
        if job.payment_type == "hourly" and (
            hourly.get("hourlyBudgetMin") or hourly.get("hourlyBudgetMax")
        ):
            job.hourly_min = _money(hourly.get("hourlyBudgetMin"))
            job.hourly_max = _money(hourly.get("hourlyBudgetMax"))
        elif job.payment_type == "fixed" and fixed:
            amount = _money(_find(fixed, "rawValue", "amount", "displayValue"))
            if amount:
                job.budget_min = job.budget_max = amount
        record = details.get("client_record") or {}
        if record:
            job.client.total_spent = _money(record.get("spend_total")) or job.client.total_spent
            job.client.jobs_posted = int(record.get("jobs_posted") or job.client.jobs_posted)
            if record.get("hire_rate_percent") is not None:
                job.client.hire_rate = float(record["hire_rate_percent"]) / 100
            if record.get("feedback_count"):
                job.client.reviews_count = int(record["feedback_count"])
                job.client.rating = float(record.get("feedback_score") or 0) or job.client.rating
        category = _find(posting.get("classification"), "preferredLabel")
        if job.domain == "other" and category:
            job.domain = tag_domain(job.title, f"{job.description} {category}", job.skills)
        return job

    async def get_client_history(self, client_id: str) -> ClientHistory:
        if not client_id.startswith("job-"):
            raise MarketplaceError("Live mode names a client by one of its jobs (job-<id>).")
        job_id = client_id.removeprefix("job-")
        job = await self.get_job(job_id)
        details = await self._job_details(job_id)
        history = details.get("client_work_history") or {}
        past = []
        for contract in history.get("closed") or []:
            if not isinstance(contract, dict):
                continue
            past.append(
                PastContract(
                    title=_clean(contract.get("title") or contract.get("job_title")),
                    amount=_money(_find(contract, "rawValue", "amount", "total_charge")) or 0.0,
                    payment_type=str(contract.get("job_type") or contract.get("type") or ""),
                    rating_given=_find(contract, "feedback_score", "score", "rating"),
                    review=_clean(_find(contract, "feedback_comment", "comment") or ""),
                    freelancer_feedback="",
                    ended_at=str(_find(contract, "end_date", "ended_at") or ""),
                )
            )
        record = details.get("client_record") or {}
        return ClientHistory(
            client=job.client,
            industry="",
            enterprise=bool(details.get("enterprise_client")),
            past_contracts=past,
            open_jobs=int(record.get("open_jobs") or 0),
        )

    # -------------------------------------------------------------- connects

    async def get_connects_balance(self) -> ConnectsBalance:
        data = await self._call("get_profile", "connects_balance")
        balance = _find(data.get("balance"), "connectsBalance")
        if balance is None:
            raise MarketplaceError("Upwork did not report a Connects balance.")
        return ConnectsBalance(
            account_id=self._org, balance=int(balance), weekly_refill=0, next_refill_at=""
        )

    async def quote_connects(self, job_id: str) -> ConnectsQuote:
        details = await self._job_details(job_id)
        if details.get("connects_cost") is None:
            raise MarketplaceError("Upwork did not report this job's Connects cost.")
        cost = int(details["connects_cost"])
        balance = details.get("connects_balance")
        if balance is None:
            balance = (await self.get_connects_balance()).balance
        return ConnectsQuote(
            job_id=job_id,
            connects_cost=cost,
            multipliers_applied=[],
            balance=int(balance),
            balance_after=int(balance) - cost,
            policy_version=POLICY_VERSION,
        )

    async def get_market_stats(
        self,
        *,
        domain: str | None = None,
        experience_level: str | None = None,
        payment_type: PaymentType | None = None,
    ) -> list[MarketStat]:
        """Upwork publishes no price percentiles; pricing falls back to the persona's rates."""
        return []

    # ------------------------------------------------------------- proposals

    def _proposal_from_node(self, node: dict[str, Any]) -> Proposal:
        job = node.get("marketplaceJobPosting") or node.get("job") or {}
        terms = node.get("terms") or {}
        amount = _money(_find(terms, "rawValue", "chargeRate", "amount")) or 0.0
        status = str(node.get("status") or "")
        return Proposal(
            proposal_id=str(node.get("id")),
            job_id=str(job.get("id") or node.get("job_id") or ""),
            cover_letter=_clean(node.get("coverLetter") or node.get("cover_letter")),
            bid_amount=amount,
            bid_type=str(_find(job, "contractType") or "").lower() or "fixed",
            screening_answers=[],
            status="submitted",
            stage=str(node.get("status_label") or status or "submitted"),
            connects_spent=int(_find(node, "connectsSpent", "connects_spent") or 0),
            connects_cost=int(_find(node, "connectsSpent", "connects_spent") or 0),
            created_at=str(node.get("createdDateTime") or node.get("created") or ""),
            submitted_at=str(node.get("createdDateTime") or "") or None,
        )

    async def list_proposals(self) -> list[Proposal]:
        data = await self._call("list_freelancer_proposals", "list", {"limit": 25})
        edges = _find(data, "edges") or []
        nodes = [e.get("node", e) for e in edges if isinstance(e, dict)]
        return [*self._drafts.values(), *(self._proposal_from_node(n) for n in nodes)]

    async def get_proposal(self, proposal_id: str) -> Proposal:
        if proposal_id in self._drafts:
            return self._drafts[proposal_id]
        data = await self._call("list_freelancer_proposals", "get", {"id": proposal_id})
        node = _find(data, "vendorProposal") or data.get("proposal") or data
        return self._proposal_from_node(node)

    async def create_proposal_draft(
        self, proposal: ProposalInput, *, idempotency_key: str
    ) -> Proposal:
        """Ask Upwork to prepare a proposal preview. Nothing is submitted or spent."""
        if idempotency_key in self._drafts_by_key:
            return self._drafts_by_key[idempotency_key]
        if len(proposal.cover_letter) > MAX_COVER_LETTER:
            raise MarketplaceError("Upwork accepts cover letters up to 5,000 characters.")
        invitations = await self._call(
            "list_freelancer_proposals", "invitations", {"job_posting_id": proposal.job_id}
        )
        if _find(invitations, "edges", "invitations"):
            raise MarketplaceError(
                "You have an invitation for this job. Accept it on Upwork instead of applying."
            )
        params: dict[str, Any] = {
            "job_reference": proposal.job_id,
            "cover_letter": proposal.cover_letter,
            "charged_amount": proposal.bid_amount,
        }
        preview = await self._call("manage_proposals", "create", params)
        questions = [
            _clean(q.get("question") if isinstance(q, dict) else q)
            for q in (_find(preview, "screening_questions") or [])
        ]
        if questions:
            if len(proposal.screening_answers) != len(questions):
                raise MarketplaceError(
                    f"This job asks {len(questions)} screening questions; answer each one."
                )
            params["answers"] = [
                {"question": q, "answer": a}
                for q, a in zip(questions, proposal.screening_answers, strict=True)
            ]
            preview = await self._call("manage_proposals", "create", params)
        preview_id = _find(preview, "preview_id")
        if not preview_id or not SAFE_ID.fullmatch(str(preview_id)):
            raise MarketplaceError("Upwork did not return a usable proposal preview.")
        draft = Proposal(
            proposal_id=str(preview_id),
            job_id=proposal.job_id,
            cover_letter=proposal.cover_letter,
            bid_amount=proposal.bid_amount,
            bid_type=proposal.bid_type,
            screening_answers=proposal.screening_answers,
            status="draft",
            stage="upwork_preview",
            connects_spent=0,
            connects_cost=int(_find(preview, "connects_cost") or 0),
            created_at=_now(),
            submitted_at=None,
        )
        self._drafts[draft.proposal_id] = draft
        self._drafts_by_key[idempotency_key] = draft
        return draft

    async def submit_proposal(self, proposal_id: str, *, idempotency_key: str) -> Proposal:
        raise MarketplaceError(
            "Upwork prepared this proposal as a preview; nothing was submitted. Review it and "
            f"confirm it yourself with: {CONFIRM_HINT.format(preview_id=proposal_id)}"
        )

    # -------------------------------------------------------------- messages

    async def list_threads(self) -> list[Thread]:
        data = await self._call("get_messages", "list_rooms", {"limit": 20})
        threads = []
        for room in _find(data, "rooms") or []:
            if not isinstance(room, dict) or not room.get("id"):
                continue
            story = room.get("latestStory") or {}
            threads.append(
                Thread(
                    thread_id=str(room["id"]),
                    client_id="",
                    job_id=str(_find(room, "jobPostingId", "job_posting_id") or ""),
                    job_title=_clean(room.get("roomName") or room.get("topic") or ""),
                    proposal_id="",
                    stage=str(room.get("awaiting_reply_from") or "conversation"),
                    last_message_at=story.get("createdDateTime"),
                    messages=0,
                )
            )
        return threads

    async def list_messages(self, thread_id: str) -> list[Message]:
        if not SAFE_ID.fullmatch(thread_id):
            raise MarketplaceError("An Upwork room id must be one plain identifier.")
        data = await self._call("get_messages", "list_messages", {"room_id": thread_id})
        stories = _find(data, "messages", "stories") or []
        messages = []
        for index, story in enumerate(reversed(stories)):  # Upwork lists newest first
            if not isinstance(story, dict):
                continue
            own = story.get("from_self") or story.get("from_side") == "freelancer"
            messages.append(
                Message(
                    id=index + 1,
                    sender="freelancer" if own else "client",
                    body=_clean(story.get("message") or story.get("text")),
                    sent_at=str(story.get("createdDateTime") or story.get("created") or ""),
                )
            )
        return messages

    async def list_events(self, *, since: int = 0, limit: int = 100) -> list[Event]:
        """Upwork has no event feed; the worker reads threads on its schedule instead."""
        return []

    async def send_message(
        self, thread_id: str, body: MessageBody, *, idempotency_key: str
    ) -> Message:
        raise MarketplaceError(
            "Live mode does not send messages. Send the approved reply yourself on Upwork."
        )
