from pathlib import Path

import pytest

from evals.runners.retrieval import scores
from rag.backends import HashedEmbedder
from rag.corpus import Evidence, load_corpus
from rag.retriever import VARIANTS, Retriever
from rag.verify import remove_unsupported, verify_claims


def item(evidence_id, title, text, evidence_type="case_study"):
    return Evidence(evidence_id, "demo", evidence_type, f"{evidence_id}.md", title, text)


def test_corpus_keeps_gradeable_evidence_ids_and_loads_actual_documents():
    directory = Path(__file__).resolve().parents[2] / "data" / "personas" / "ai_ml"
    corpus = {item.id: item for item in load_corpus(directory)}
    assert corpus["cv-ai_ml"].type == "cv"
    assert "Ayesha Raza" in corpus["cv-ai_ml"].text
    assert corpus["cs-ai-p01"].client_industry == "healthcare"
    assert "app/retrieve.py" in corpus["repo-policy-qa"].text
    assert corpus["repo-policy-qa"].project_id == "ai-p01"
    assert corpus["rv-ai-p01"].type == "review"
    assert corpus["pp-ai_ml-000"].outcome == "replied"


def test_variants_and_metadata_filters_keep_portfolio_evidence_scoped():
    rag = item("rag", "RAG pipeline", "Used Python and pgvector for citation retrieval.")
    ui = item("gui", "TypeScript UI", "Shipped a React interface.")
    retriever = Retriever([rag, ui])
    for variant in VARIANTS:
        hits = retriever.search(
            "Python, pgvector", variant=variant, requirements=["Python", "pgvector"]
        )
        assert hits[0].evidence == rag
    assert not retriever.search("orbital docking", variant="v8", requirements=["orbital docking"])
    assert not retriever.search("Python", filters={"type": "repo"})
    with pytest.raises(ValueError, match="Unknown evidence filters"):
        retriever.search("Python", filters={"wrong_key": "repo"})


def test_index_refresh_reuses_unchanged_vectors_and_removes_deleted_documents():
    class CountingEmbedder(HashedEmbedder):
        indexed: list[str]

        def __init__(self):
            self.indexed = []

        def encode(self, texts):
            self.indexed.extend(texts)
            return super().encode(texts)

    old = item("keep", "Python", "Python pipelines")
    updated = item("new", "SQL", "Prepared SQL pipelines")
    embedder = CountingEmbedder()
    retriever = Retriever([old], embedder=embedder)
    retriever.refresh([old, updated])
    assert len(embedder.indexed) == 2
    retriever.refresh([updated])
    assert len(embedder.indexed) == 2
    assert "keep" not in retriever.index.evidence
    assert retriever.search("SQL")[0].evidence == updated


def test_verifier_resolves_quote_and_spans_and_strips_only_unverified_text():
    evidence = item("a", "Billing", "Built Stripe billing. Reduced duplicate events by 92%.")
    proposal = "Hello.\n\nI built Stripe billing [evidence:a]. Reduced duplicate events by 99%."
    supported, altered = verify_claims(proposal, [evidence])
    assert supported.status == "supported"
    assert supported.evidence_ids == ("a",)
    assert supported.quote == "Built Stripe billing."
    assert altered.status == "partly_supported"
    assert proposal[altered.start : altered.end] == altered.text
    assert remove_unsupported(proposal, [supported, altered]) == (
        "Hello.\n\nI built Stripe billing [evidence:a]."
    )
    (false_citation,) = verify_claims("I built Stripe billing [evidence:b].", [evidence])
    assert false_citation.status == "unsupported"
    old_proposal = item("old", "Old bid", "Built Stripe billing.", "past_proposal")
    assert verify_claims("I built Stripe billing.", [old_proposal])[0].status == "unsupported"
    planned = "I would begin by reviewing the test environment."
    assert not verify_claims(planned, [evidence], planned_text=[planned])
    smuggled = "I would bring my 15 years of Stripe experience to this project."
    assert verify_claims(smuggled, [evidence], planned_text=[planned])[0].status == "unsupported"


def test_retrieval_metrics_are_document_level_and_use_graded_relevance():
    gold = {"grade-two": 2, "grade-one": 1}
    assert scores(["grade-two", "grade-one"], gold) == (1, 1, 1)
    assert scores(["noise", "grade-one"], gold)[:2] == (0.5, 0.5)
    assert scores(["nothing"], gold) == (0, 0, 0)
