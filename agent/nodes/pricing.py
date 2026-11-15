"""Price a bid within the posted range and the freelancer's configured bounds."""

from typing import Any, Literal

from agent.handoffs import PriceQuote
from agent.nodes.analyst import job_budget


def price_job(
    job: dict[str, Any],
    *,
    minimum_project_budget: float,
    minimum_hourly_rate: float,
    preferred_hourly_rate: float,
    connects_budget: int,
) -> PriceQuote:
    bid_type: Literal["fixed", "hourly"] = job.get("payment_type", "fixed")
    lower, upper = job_budget(job)
    if lower is None:
        lower = upper
    if upper is None:
        upper = lower
    floor = minimum_hourly_rate if bid_type == "hourly" else minimum_project_budget
    cost: object = job.get("connects_cost", job.get("base_connects"))
    connects_cost = cost if type(cost) is int and cost >= 0 else None

    if lower is None or upper is None or upper < max(lower, floor) or upper <= 0:
        return PriceQuote(
            bid_type=bid_type, connects_cost=connects_cost or 0, amount=0, worth_it=False,
            rationale="The posted budget cannot cover the configured minimum.",
        )

    if connects_cost is None or connects_cost > connects_budget:
        return PriceQuote(
            bid_type=bid_type, connects_cost=connects_cost or 0, amount=0, worth_it=False,
            rationale="The Connects cost is unavailable or exceeds the configured budget.",
        )

    target = preferred_hourly_rate if bid_type == "hourly" else (lower + upper) / 2
    amount = min(max(target, floor, lower), upper)
    return PriceQuote(
        bid_type=bid_type, connects_cost=connects_cost, amount=amount, worth_it=True,
        rationale="The quote falls within the posted range and the configured minimum.",
    )
