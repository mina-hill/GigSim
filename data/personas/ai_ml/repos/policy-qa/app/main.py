from fastapi import FastAPI
from pydantic import BaseModel

from app.answer import answer_with_citations

api = FastAPI(title="policy-qa")


class Question(BaseModel):
    text: str


@api.post("/ask")
def ask(q: Question):
    result = answer_with_citations(q.text)
    if not result.citations:
        return {"answer": None, "refused": True, "reason": "No supporting passage found"}
    return {"answer": result.text, "citations": result.citations}
