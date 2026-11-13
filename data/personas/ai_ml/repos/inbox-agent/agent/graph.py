from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class EmailState(TypedDict, total=False):
    email: str
    intent: str
    draft: str
    approved: bool


def classify(state: EmailState) -> EmailState: ...
def draft_reply(state: EmailState) -> EmailState: ...


def approve(state: EmailState) -> EmailState:
    decision = interrupt({"draft": state["draft"]})
    return {"approved": decision == "approve"}


g = StateGraph(EmailState)
g.add_node("classify", classify)
g.add_node("draft", draft_reply)
g.add_node("approve", approve)
g.add_edge(START, "classify")
g.add_edge("classify", "draft")
g.add_edge("draft", "approve")
g.add_edge("approve", END)
