"""Marketplace capabilities available to the local bid workflow."""

from typing import Protocol

from mcp_servers.marketplace.port import (
    ConnectsQuote,
    Job,
    MarketplacePort,
    Proposal,
    ProposalInput,
)


class BidPreview(ConnectsQuote):
    account_id: str


class BidMarketplace(Protocol):
    async def get_job(self, job_id: str) -> Job: ...

    async def preview(self, proposal: ProposalInput) -> BidPreview: ...

    async def submit(
        self,
        proposal: ProposalInput,
        *,
        idempotency_key: str,
        approved_quote: BidPreview,
    ) -> Proposal: ...


class ConnectsQuoteChanged(RuntimeError):
    """The approved Connects quote can no longer be honored."""


class LocalBidMarketplace:
    """Run the graph against the simulator port in local development."""

    def __init__(self, port: MarketplacePort) -> None:
        self.port = port

    async def get_job(self, job_id: str) -> Job:
        return await self.port.get_job(job_id)

    async def preview(self, proposal: ProposalInput) -> BidPreview:
        account = await self.port.get_connects_balance()
        quote = await self.port.quote_connects(proposal.job_id)
        return BidPreview(account_id=account.account_id, **quote.model_dump())

    async def submit(
        self,
        proposal: ProposalInput,
        *,
        idempotency_key: str,
        approved_quote: BidPreview,
    ) -> Proposal:
        if approved_quote.job_id != proposal.job_id:
            raise ValueError("The Connects quote is for a different job.")
        account = await self.port.get_connects_balance()
        if account.account_id != approved_quote.account_id:
            raise ConnectsQuoteChanged("The approval belongs to a different marketplace account.")

        # GigSim returns the original response when the draft was already created.
        # If submit previously committed, use the same key to recover its receipt.
        draft = await self.port.create_proposal_draft(
            proposal,
            idempotency_key=f"{idempotency_key}:draft",
        )
        saved = await self.port.get_proposal(draft.proposal_id)
        if saved.status == "draft":
            current = await self.port.quote_connects(proposal.job_id)
            if (
                current.policy_version != approved_quote.policy_version
                or current.connects_cost != approved_quote.connects_cost
                or current.balance_after < 0
            ):
                raise ConnectsQuoteChanged(
                    "Connects changed. Review a new quote before submitting."
                )
        return await self.port.submit_proposal(
            draft.proposal_id,
            idempotency_key=f"{idempotency_key}:submit",
        )
