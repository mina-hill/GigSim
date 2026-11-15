"""Public view of a checkpointed client reply."""

from pydantic import BaseModel, Field

from agent.graphs.conversation_graph import ConversationPreview, ConversationStatus
from agent.handoffs import ApprovalDecision, ClaimVerification, ComplianceVerdict
from agent.nodes.client_liaison import ConversationIntent, InterviewSlot, ReplyDraft
from mcp_servers.marketplace.port import Message


class StartConversation(BaseModel):
    availability: list[InterviewSlot] = Field(default_factory=list, max_length=8)


class ConversationRunView(BaseModel):
    status: ConversationStatus = "idle"
    resumable: bool = False
    pending: ConversationPreview | None = None
    incoming: Message | None = None
    intent: ConversationIntent | None = None
    draft: ReplyDraft | None = None
    claim_verification: list[ClaimVerification] = Field(default_factory=list)
    compliance_verdict: ComplianceVerdict | None = None
    approval_status: str | None = None
    approval_decision: ApprovalDecision | None = None
    outcome: Message | None = None
    errors: list[str] = Field(default_factory=list)
