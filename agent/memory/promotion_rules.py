"""The only writers of promoted, tenant-scoped bid memory."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Literal

from langgraph.store.base import BaseStore
from pydantic import BaseModel, Field, field_validator, model_validator

StyleRule = Literal["shorter_opening", "omit_greeting", "concise_closing", "avoid_passionate"]
FeatureKind = Literal["opening_style", "length_bucket", "evidence_type", "price_position"]

_FEATURE_VALUES: dict[FeatureKind, set[str]] = {
    "opening_style": {"evidence_first", "question_first", "direct"},
    "length_bucket": {"short", "medium", "long"},
    "evidence_type": {"case_study", "code", "review"},
    "price_position": {"low", "middle", "high"},
}


class StyleEdit(BaseModel):
    edit_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=128)
    rule: StyleRule
    approved: bool
    supports: bool = True

    @field_validator("rule", mode="before")
    @classmethod
    def normalize_rule(cls, rule: str) -> str:
        if isinstance(rule, str):
            rule = "_".join(rule.strip().lower().replace("-", " ").split())
        return rule


class OutcomeFeature(BaseModel):
    kind: FeatureKind
    value: str

    @model_validator(mode="after")
    def allowed_value(self) -> OutcomeFeature:
        if self.value not in _FEATURE_VALUES[self.kind]:
            raise ValueError("Choose a predefined outcome feature value.")
        return self

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.value}"


class OutcomeObservation(BaseModel):
    outcome_id: str = Field(min_length=1, max_length=128)
    feature: OutcomeFeature
    outcome: Literal["replied", "won", "ignored"]


class PromotionRecord(BaseModel):
    subject: str
    rule_id: Literal["approved_edit_repetition", "wilson_against_baseline"]
    version: Literal[1] = 1
    source_ids: list[str]
    reply_lower_bound: float | None = None
    category_baseline: float | None = None


def should_promote_style_edit(edit_count: int, consistent_pattern: bool) -> bool:
    return edit_count >= 3 and consistent_pattern


def wilson_lower_bound(successes: int, observations: int) -> float:
    """Lower bound of a two-sided 95 percent Wilson interval."""
    if observations <= 0 or not 0 <= successes <= observations:
        raise ValueError("Outcome counts must satisfy 0 <= successes <= observations.")
    z = 1.959963984540054
    rate = successes / observations
    variance = rate * (1 - rate) / observations + z**2 / (4 * observations**2)
    return (rate + z**2 / (2 * observations) - z * math.sqrt(variance)) / (1 + z**2 / observations)


def _namespace(
    tenant: str, kind: Literal["style", "outcome"], category: str = ""
) -> tuple[str, ...]:
    if not tenant.strip() or (kind == "outcome" and not category.strip()):
        raise ValueError("Memory requires a tenant and an outcome category where applicable.")
    return (tenant, "style") if kind == "style" else (tenant, "outcome", category)


async def _promote(
    store: BaseStore, namespace: tuple[str, ...], record: PromotionRecord
) -> PromotionRecord | None:
    existing = await store.aget(namespace, record.subject)
    if existing is not None and existing.value.get("deleted"):
        return None
    await store.aput(namespace, record.subject, record.model_dump(mode="json"))
    return record


async def promote_style(
    store: BaseStore, tenant: str, rule: StyleRule, edits: Iterable[StyleEdit]
) -> PromotionRecord | None:
    """Use the last ten distinct approved edits, supplied oldest first."""
    namespace = _namespace(tenant, "style")
    approved = {edit.edit_id: edit for edit in edits if edit.approved}
    matching = [edit for edit in list(approved.values())[-10:] if edit.rule == rule]
    supporting = [edit for edit in matching if edit.supports]
    consistent = len(supporting) == len(matching) and len({edit.job_id for edit in supporting}) >= 2
    if not should_promote_style_edit(len(supporting), consistent):
        return None
    return await _promote(
        store,
        namespace,
        PromotionRecord(
            subject=rule,
            rule_id="approved_edit_repetition",
            source_ids=[edit.edit_id for edit in supporting],
        ),
    )


async def promote_outcome(
    store: BaseStore,
    tenant: str,
    category: str,
    feature: OutcomeFeature,
    outcomes: Iterable[OutcomeObservation],
    *,
    baseline_reply_rate: float,
) -> PromotionRecord | None:
    """Promote a feature only when its lower bound exceeds the category baseline."""
    namespace = _namespace(tenant, "outcome", category)
    if not 0 <= baseline_reply_rate <= 1:
        raise ValueError("The category reply rate must be between zero and one.")
    matching = {item.outcome_id: item for item in outcomes if item.feature == feature}
    if len(matching) < 8:
        return None
    replied = sum(item.outcome in {"replied", "won"} for item in matching.values())
    lower_bound = wilson_lower_bound(replied, len(matching))
    if lower_bound <= baseline_reply_rate:
        return None
    return await _promote(
        store,
        namespace,
        PromotionRecord(
            subject=feature.key,
            rule_id="wilson_against_baseline",
            source_ids=list(matching),
            reply_lower_bound=lower_bound,
            category_baseline=baseline_reply_rate,
        ),
    )


async def delete_style(store: BaseStore, tenant: str, rule: StyleRule) -> None:
    await store.aput(_namespace(tenant, "style"), rule, {"deleted": True})


async def delete_outcome(
    store: BaseStore, tenant: str, category: str, feature: OutcomeFeature
) -> None:
    await store.aput(_namespace(tenant, "outcome", category), feature.key, {"deleted": True})
