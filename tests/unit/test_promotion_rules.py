from langgraph.store.memory import InMemoryStore

from agent.memory.promotion_rules import (
    OutcomeFeature,
    OutcomeObservation,
    StyleEdit,
    delete_outcome,
    delete_style,
    promote_outcome,
    promote_style,
)


def style_edit(index, job_id, *, approved=True, supports=True):
    return StyleEdit(
        edit_id=f"edit-{index}",
        job_id=job_id,
        rule="Shorter opening",
        approved=approved,
        supports=supports,
    )


async def test_style_requires_distinct_approved_edits_and_accepts_tombstone():
    store = InMemoryStore()
    first = style_edit(1, "one")
    second = style_edit(2, "one")
    third = style_edit(3, "two")
    unapproved = style_edit(4, "two", approved=False)
    contradiction = style_edit(5, "two", supports=False)
    assert not await promote_style(store, "tenant-a", "shorter_opening", [first, first, second])
    assert not await promote_style(
        store, "tenant-a", "shorter_opening", [first, second, unapproved]
    )
    assert not await promote_style(
        store, "tenant-a", "shorter_opening", [first, second, third, contradiction]
    )
    promoted = await promote_style(store, "tenant-a", "shorter_opening", [first, second, third])
    assert promoted.source_ids == ["edit-1", "edit-2", "edit-3"]
    assert promoted.rule_id == "approved_edit_repetition"
    assert await store.aget(("tenant-b", "style"), "shorter_opening") is None
    await delete_style(store, "tenant-a", "shorter_opening")
    assert not await promote_style(store, "tenant-a", "shorter_opening", [first, second, third])
    assert (await store.aget(("tenant-a", "style"), "shorter_opening")).value == {"deleted": True}


async def test_outcome_requires_enough_observations_and_a_positive_lower_bound():
    store = InMemoryStore()
    feature = OutcomeFeature(kind="opening_style", value="evidence_first")
    observations = [
        OutcomeObservation(outcome_id=f"o{i}", feature=feature, outcome="won") for i in range(8)
    ]
    assert not await promote_outcome(
        store, "tenant-a", "web", feature, observations[:7], baseline_reply_rate=0.1
    )
    assert not await promote_outcome(
        store, "tenant-a", "web", feature, observations, baseline_reply_rate=0.8
    )
    promoted = await promote_outcome(
        store, "tenant-a", "web", feature, observations, baseline_reply_rate=0.5
    )
    assert 0.5 < promoted.reply_lower_bound < 0.8
    assert promoted.source_ids == [item.outcome_id for item in observations]
    assert await store.aget(("tenant-a", "outcome", "other"), feature.key) is None
    await delete_outcome(store, "tenant-a", "web", feature)
    assert not await promote_outcome(
        store, "tenant-a", "web", feature, observations, baseline_reply_rate=0.5
    )
