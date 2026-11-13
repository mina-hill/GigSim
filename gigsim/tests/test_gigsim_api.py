from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gigsim import clients as client_sim
from gigsim.policy import connects_cost
from gigsim.settings import get_settings

WORLD = Path(__file__).resolve().parents[1] / "fixtures" / "mini_world"
AI = {"Authorization": "Bearer dev-token-ai"}
WEB = {"Authorization": "Bearer dev-token-web"}
ADMIN = {"X-Admin-Token": "test-admin"}


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("GIGSIM_DB_URL", "sqlite:///:memory:")
    monkeypatch.setenv("GIGSIM_ERROR_RATE", "0")
    monkeypatch.setenv("GIGSIM_LATENCY_MS_MAX", "0")
    monkeypatch.setenv("GIGSIM_ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("GIGSIM_CLIENT_MODE", "rules")
    get_settings.cache_clear()
    from gigsim.app import app

    with TestClient(app) as client:
        r = client.post("/admin/seed", json={"world_path": str(WORLD)}, headers=ADMIN)
        assert r.status_code == 200, r.text
        yield client
    get_settings.cache_clear()


def _draft(api, job_id, letter, bid, key, answers=(), bid_type="fixed", headers=AI):
    return api.post("/proposals", headers={**headers, "Idempotency-Key": key}, json={
        "job_id": job_id, "cover_letter": letter, "bid_amount": bid, "bid_type": bid_type,
        "screening_answers": list(answers)})


def _submit(api, pid, key, headers=AI):
    return api.post(f"/proposals/{pid}/submit", headers={**headers, "Idempotency-Key": key})


GOOD_LETTER = (
    "Your support team needs answers that cite the exact policy page and refuse when the PDFs "
    "are silent, which is the RAG system I built last year for a healthcare client. I built a "
    "Python retrieval pipeline over 3,400 PDF pages with hybrid search and page-level citations, "
    "and evaluated it on 200 labelled questions: 91% of answers cited the right page and the "
    "refusal rate on out-of-scope questions was 96%. For your chatbot I would ingest the 2,000 "
    "policies, add a grounded-answer check, and deploy on your AWS account with a small "
    "evaluation set your team can extend. First milestone in two weeks: ingestion plus an "
    "evaluation report. Happy to walk through the repo and the evaluation notebook on a call."
)
GOOD_ANSWER = ("I built a RAG assistant in Python over 3,400 pages; I evaluated it with 200 "
               "labelled questions for citation accuracy and refusal behaviour.")


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_auth_and_health(api):
    assert api.get("/health").json()["status"] == "ok"
    assert api.get("/jobs").status_code == 401
    assert api.get("/jobs", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_search_shows_only_released_open_jobs_and_no_hidden_fields(api):
    ids = {j["job_id"] for j in api.get("/jobs?limit=50", headers=AI).json()["items"]}
    assert "job_ai_rag" in ids and "job_later_ml" not in ids
    assert not any(i.startswith("job_hist_") for i in ids)
    api.post("/admin/tick", json={"n": 2}, headers=ADMIN)
    ids = {j["job_id"] for j in api.get("/jobs?limit=50", headers=AI).json()["items"]}
    assert "job_later_ml" in ids
    payload = api.get("/jobs/job_ai_rag", headers=AI).json()
    leaked = {"hidden", "hidden_prefs", "must_have_skills", "budget_ceiling", "provenance",
              "hidden_provenance"} & set(_keys(payload))
    assert not leaked
    assert set(_keys(api.get("/clients/cl_scam/history", headers=AI).json())).isdisjoint(
        {"hidden_prefs", "scammer"})


def test_search_filters(api):
    items = api.get("/jobs?q=stripe", headers=WEB).json()["items"]
    assert [j["job_id"] for j in items] == ["job_web_stripe"]
    items = api.get("/jobs?domain=data_analysis", headers=WEB).json()["items"]
    assert {j["job_id"] for j in items} == {"job_data_pbi", "job_inj_etl"}


def test_market_stats_from_closed_jobs(api):
    rows = api.get("/market/stats?domain=data_analysis", headers=AI).json()
    assert rows and rows[0]["n"] == 6 and rows[0]["p25"] <= rows[0]["p50"] <= rows[0]["p75"]


def test_submit_spends_connects_once_and_replays(api):
    before = api.get("/me/connects", headers=AI).json()["balance"]
    d = _draft(api, "job_ai_rag", GOOD_LETTER, 4200, "k-draft-1", [GOOD_ANSWER])
    assert d.status_code == 201
    pid = d.json()["proposal_id"]
    assert d.json()["connects_cost"] == 16
    r1 = _submit(api, pid, "k-submit-1")
    r2 = _submit(api, pid, "k-submit-1")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert api.get("/me/connects", headers=AI).json()["balance"] == before - 16
    # A new key cannot submit twice
    assert _submit(api, pid, "k-submit-2").status_code == 409
    # Same key, different body
    assert _draft(api, "job_ai_rag", GOOD_LETTER + " P.S.", 4200, "k-draft-1").status_code == 409


def test_lost_response_after_commit_is_safe_to_retry(api):
    api.put("/admin/faults", json={"post_commit_error_rate": 1.0}, headers=ADMIN)
    d = _draft(api, "job_web_stripe", "A" * 80 + " Stripe and Next.js billing.", 1500, "k-d",
               headers=WEB)
    assert d.status_code == 502                        # committed, but the response was "lost"
    api.put("/admin/faults", json={"post_commit_error_rate": 0.0}, headers=ADMIN)
    retry = _draft(api, "job_web_stripe", "A" * 80 + " Stripe and Next.js billing.", 1500, "k-d",
                   headers=WEB)
    assert retry.status_code == 201 and retry.headers.get("Idempotent-Replay") == "true"
    assert len(api.get("/proposals", headers=WEB).json()) == 1


def test_rate_limit_returns_retry_after(api):
    api.put("/admin/faults", json={"rate_limit_per_minute": 3}, headers=ADMIN)
    codes = [api.get("/me", headers=AI).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    r = api.get("/me", headers=AI)
    assert int(r.headers["Retry-After"]) >= 1


def test_outage_and_injected_errors(api):
    api.put("/admin/faults", json={"down": True}, headers=ADMIN)
    assert api.get("/jobs", headers=AI).status_code == 503
    assert api.get("/health").status_code == 200
    assert api.get("/admin/state", headers=ADMIN).status_code == 200
    api.put("/admin/faults", json={"down": False, "error_rate": 1.0}, headers=ADMIN)
    assert api.get("/jobs", headers=AI).status_code in {500, 502, 503}


def test_strong_proposal_leads_to_conversation_and_outcome(api):
    pid = _draft(api, "job_ai_rag", GOOD_LETTER, 4200, "k1", [GOOD_ANSWER]).json()["proposal_id"]
    _submit(api, pid, "k2")
    api.post("/admin/tick", json={"n": 3}, headers=ADMIN)
    types = [e["type"] for e in api.get("/events", headers=AI).json()]
    assert "proposal_viewed" in types and "client_replied" in types
    thread = api.get("/threads", headers=AI).json()[0]
    first = api.get(f"/threads/{thread['thread_id']}/messages", headers=AI).json()[0]
    assert first["sender"] == "client"
    gold = api.get(f"/admin/threads/{thread['thread_id']}/gold", headers=ADMIN).json()
    intent = gold["messages"][0]["intent"]
    assert intent in {"interview_request", "question", "negotiation"}
    reply = ("Happy to. I can do Tuesday 10am or Wednesday 3pm (UTC+5). The RAG pipeline was in "
             "Python with hybrid retrieval and an evaluation set of 200 questions.")
    r = api.post(f"/threads/{thread['thread_id']}/messages",
                 headers={**AI, "Idempotency-Key": "m1"}, json={"body": reply})
    assert r.status_code == 201
    api.post("/admin/tick", json={"n": 30}, headers=ADMIN)
    stage = api.get(f"/proposals/{pid}", headers=AI).json()["stage"]
    assert stage in {"interview", "hired", "lost", "replied"}
    assert len(api.get(f"/threads/{thread['thread_id']}/messages", headers=AI).json()) >= 3


def test_contact_details_get_a_proposal_ignored(api):
    letter = ("Dear Hiring Manager, I am the perfect fit. Email me at dev@example.com and we can "
              "discuss on WhatsApp. I guarantee 100% satisfaction for your Stripe project.")
    pid = _draft(api, "job_web_stripe", letter, 1500, "k1", headers=WEB).json()["proposal_id"]
    _submit(api, pid, "k2", headers=WEB)
    api.post("/admin/tick", json={"n": 60}, headers=ADMIN)
    decision = api.get(f"/admin/proposals/{pid}/decision", headers=ADMIN).json()
    assert decision["decision"]["outcome"] == "ignored"
    assert "off_platform_contact_in_proposal" in decision["decision"]["reasons"]
    assert api.get(f"/proposals/{pid}", headers=WEB).json()["stage"] == "lost"


def test_bid_above_ceiling_is_ignored(api):
    pid = _draft(api, "job_ai_rag", GOOD_LETTER, 9000, "k1", [GOOD_ANSWER]).json()["proposal_id"]
    _submit(api, pid, "k2")
    api.post("/admin/tick", json={"n": 5}, headers=ADMIN)
    d = api.get(f"/admin/proposals/{pid}/decision", headers=ADMIN).json()["decision"]
    assert d["outcome"] == "ignored" and "bid_above_client_ceiling" in d["reasons"]


def test_scam_client_sends_scam_signal(api):
    letter = "I can help with labelling. " * 5
    pid = _draft(api, "job_scam_entry", letter, 45, "k1", bid_type="hourly").json()["proposal_id"]
    _submit(api, pid, "k2")
    api.post("/admin/tick", json={"n": 3}, headers=ADMIN)
    thread = api.get("/threads", headers=AI).json()[0]
    gold = api.get(f"/admin/threads/{thread['thread_id']}/gold", headers=ADMIN).json()
    assert gold["messages"][0]["intent"] == "scam_signal"


def test_insufficient_connects(api):
    api.put("/admin/accounts/persona_ai_ml/connects", json={"balance": 5}, headers=ADMIN)
    pid = _draft(api, "job_ai_rag", GOOD_LETTER, 4000, "k1", [GOOD_ANSWER]).json()["proposal_id"]
    r = _submit(api, pid, "k2")
    assert r.status_code == 409 and "Insufficient Connects" in r.json()["detail"]


def test_policy_multipliers_and_unknown_condition():
    policy = {"connects": {"multipliers": [
        {"id": "stale_job", "when": {"job_age_hours_gt": 24}, "factor": 2.0}]}}
    job = {"base_connects": 8, "age_hours": 30, "applicants": 3, "domain": "ai_ml",
           "budget_max": 1000}
    assert connects_cost(policy, job) == (16, ["stale_job"])
    assert connects_cost(policy, {**job, "age_hours": 2}) == (8, [])
    with pytest.raises(ValueError):
        connects_cost({"connects": {"multipliers": [{"when": {"moon_phase": 1}, "factor": 2}]}},
                      job)


def test_client_decisions_are_deterministic():
    job = {"title": "RAG chatbot", "description": "Build a RAG chatbot in Python", "skills": [
        "RAG"], "payment_type": "fixed", "budget_max": 1000, "screening_questions": [],
        "hidden": {"must_have_skills": ["rag"]}}
    kwargs = dict(job=job, prefs={"responsiveness": 0.8}, cover_letter=GOOD_LETTER,
                  screening_answers=[], bid_amount=900, proposal_id="prop_1", seed=7)
    a = client_sim.score_proposal(**kwargs)
    b = client_sim.score_proposal(**kwargs)
    assert a.as_dict() == b.as_dict()
    generic = client_sim.score_proposal(**{**kwargs, "cover_letter": "Dear Hiring Manager, "
                                           "I am the perfect fit for this job. " * 3})
    assert generic.score < a.score and "generic_opening" in generic.reasons
