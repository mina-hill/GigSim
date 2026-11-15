"""Review specificity and grounding before the compliance gate."""

from agent.handoffs import ClaimVerification, Critique, ProposalDraft


def critique_proposal(draft: ProposalDraft, verification: list[ClaimVerification]) -> Critique:
    feedback = []
    if not draft.evidence_ids or not any(item.supported for item in verification):
        feedback.append("Include a portfolio example supported by the supplied evidence.")
    if any(not item.supported for item in verification):
        feedback.append("Remove or substantiate the unverified text in the draft.")
    return Critique(needs_revision=bool(feedback), feedback=feedback)
