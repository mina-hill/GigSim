"""Simulated clients: decide what happens to a proposal and write follow-up messages.

Each client has hidden preferences (stored in Client.hidden_prefs and Job.hidden) that the
API never returns. A decision runs in two steps:

1. Hard rules: contact details or off-platform asks -> ignored; bid above the client's
   ceiling -> ignored.
2. A score from observable features (must-have skills mentioned, concrete evidence,
   personalisation, generic opening, length, price, screening answers), optionally blended
   with an LLM judgement in "llm" mode. The score maps to ignored / viewed / replied /
   interview. Hiring is decided later in the conversation.

All randomness comes from a hash of (seed, proposal id), so a rerun gives the same outcome.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from gigsim.skills import mentions

OFF_PLATFORM = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+|(?:\+?\d[\d\s().-]{8,}\d)|\b(whatsapp|telegram|skype|signal|"
    r"wechat|discord|gmail|outlook\.com|contact me (?:at|on)|reach me (?:at|on))\b",
    re.IGNORECASE,
)
GUARANTEE = re.compile(
    r"\b(guarantee[ds]?|100% (?:satisfaction|success|accuracy)|risk[- ]free|"
    r"money[- ]back)\b",
    re.IGNORECASE,
)
GENERIC_OPENINGS = re.compile(
    r"^\s*(dear (?:hiring manager|sir|madam|client)|hello sir|hi there|greetings|"
    r"i am writing to|i have (?:carefully )?read your job|i am (?:very )?interested in your job|"
    r"i am the (?:perfect|best|ideal) (?:fit|candidate|person)|i'?m a (?:passionate|dedicated|"
    r"highly skilled)|as an? (?:experienced|expert|passionate))",
    re.IGNORECASE,
)
EVIDENCE = re.compile(
    r"\d+(?:\.\d+)?\s?(?:%|x\b|ms\b|k\b|users|rows|records|hours|minutes|seconds|clients|"
    r"dashboards|models|pipelines|requests)|\b(i built|i shipped|i deployed|i designed|i led|"
    r"i migrated|i automated|case study|repo|repository|github\.com/[\w-]+/[\w-]+)\b",
    re.IGNORECASE,
)
WORD = re.compile(r"[a-z][a-z0-9+.#-]{2,}")
STOP = set(
    "the and for with that this you your are our will have from need looking who can into "
    "about project work job help using also more must should would been their them they "
    "what when where which while than then there these those some such only very just".split()
)
LENGTH_BANDS = {"short": (60, 180), "medium": (140, 320), "long": (250, 480)}


def _unit(seed: int, *parts: str) -> float:
    """Deterministic number in [0, 1) from the seed and string parts."""
    h = hashlib.sha256(("|".join([str(seed), *parts])).encode()).hexdigest()
    return int(h[:12], 16) / float(16**12)


@dataclass
class Decision:
    outcome: str                     # ignored | viewed | replied | interview
    score: float
    reasons: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)
    delay_ticks: int = 1
    ghost: bool = False              # client never answers even if outcome is positive
    llm_score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome, "score": round(self.score, 4), "reasons": self.reasons,
            "features": {k: round(v, 4) for k, v in self.features.items()},
            "delay_ticks": self.delay_ticks, "ghost": self.ghost, "llm_score": self.llm_score,
        }


def _keywords(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if w not in STOP}


def ceiling_for(job: dict[str, Any], prefs: dict[str, Any]) -> float | None:
    hidden = job.get("hidden") or {}
    if job["payment_type"] == "hourly":
        if hidden.get("hourly_ceiling") is not None:
            return float(hidden["hourly_ceiling"])
        top = job.get("hourly_max") or job.get("hourly_min")
    else:
        if hidden.get("budget_ceiling") is not None:
            return float(hidden["budget_ceiling"])
        top = job.get("budget_max") or job.get("budget_min")
    if not top:
        return None
    return float(top) * float(prefs.get("price_tolerance", 1.1))


def score_proposal(
    *, job: dict[str, Any], prefs: dict[str, Any], cover_letter: str, screening_answers: list[str],
    bid_amount: float, proposal_id: str, seed: int, applicants: int = 0,
) -> Decision:
    text = cover_letter + "\n" + "\n".join(screening_answers)
    reasons: list[str] = []
    feats: dict[str, float] = {}

    # Scam clients answer almost anything, then try to move the freelancer off-platform.
    if prefs.get("scammer"):
        return Decision("replied", 0.9, ["scam_client_baits_everyone"], {}, delay_ticks=1)

    # ---- hard rules
    if OFF_PLATFORM.search(text):
        return Decision("ignored", 0.0, ["off_platform_contact_in_proposal"], {})
    ceiling = ceiling_for(job, prefs)
    if ceiling is not None and bid_amount > ceiling:
        return Decision("ignored", 0.05, ["bid_above_client_ceiling"],
                        {"bid_over_ceiling": bid_amount / ceiling})

    # ---- soft features
    must = (job.get("hidden") or {}).get("must_have_skills") or job.get("skills", [])[:2]
    covered = [s for s in must if mentions(text, s)]
    feats["must_have_coverage"] = len(covered) / len(must) if must else 1.0
    if must and len(covered) < len(must):
        reasons.append("missing_must_have:" + ",".join(s for s in must if s not in covered))

    evidence_hits = len(EVIDENCE.findall(text))
    feats["specificity"] = min(1.0, evidence_hits / 4.0)

    job_kw = _keywords(job["title"] + " " + job["description"])
    prop_kw = _keywords(cover_letter)
    feats["personalisation"] = (min(1.0, len(job_kw & prop_kw) / max(6, len(job_kw) * 0.25))
                                if job_kw else 0.0)

    first_sentence = re.split(r"(?<=[.!?])\s|\n", cover_letter.strip(), maxsplit=1)[0]
    generic = bool(GENERIC_OPENINGS.search(first_sentence))
    feats["generic_opening"] = 1.0 if generic else 0.0

    words = len(cover_letter.split())
    lo, hi = LENGTH_BANDS.get(prefs.get("preferred_length", "medium"), (140, 320))
    feats["length_fit"] = 1.0 if lo <= words <= hi else max(0.0, 1 - min(abs(words - lo),
                                                                         abs(words - hi)) / hi)

    feats["price_position"] = (max(0.0, min(1.0, 1 - bid_amount / ceiling)) if ceiling else 0.5)

    questions = job.get("screening_questions") or []
    answered = sum(1 for a in screening_answers if len(a.split()) >= 8)
    feats["screening"] = answered / len(questions) if questions else 1.0

    score = (
        0.35 * feats["must_have_coverage"]
        + 0.20 * feats["specificity"]
        + 0.20 * feats["personalisation"]
        + 0.10 * feats["length_fit"]
        + 0.10 * feats["screening"]
        + 0.05 * feats["price_position"]
    )
    if generic:
        penalty = 0.20 if prefs.get("dislikes_generic_opening", True) else 0.05
        score -= penalty
        reasons.append("generic_opening")
    if GUARANTEE.search(text):
        score -= 0.15
        reasons.append("guarantee_language")
    if questions and feats["screening"] < 1.0:
        reasons.append("screening_incomplete")
    if applicants > 50:
        score -= 0.05
        reasons.append("crowded_job")
    strictness = float(prefs.get("strictness", 0.5))
    score -= 0.10 * (strictness - 0.5)
    score += (_unit(seed, proposal_id, "noise") - 0.5) * 0.08   # +-0.04 deterministic noise
    score = max(0.0, min(1.0, score))

    if score >= 0.72:
        outcome = "interview"
    elif score >= 0.58:
        outcome = "replied"
    elif score >= 0.35:
        outcome = "viewed"
    else:
        outcome = "ignored"

    responsiveness = float(prefs.get("responsiveness", 0.7))
    delay = 1 + int((1 - responsiveness) * 4)
    ghost = outcome in {"replied", "interview"} and _unit(seed, proposal_id, "ghost") < (
        (1 - responsiveness) * 0.3
    )
    if ghost:
        reasons.append("client_went_quiet")
    return Decision(outcome, score, reasons, feats, delay_ticks=delay, ghost=ghost)


# --------------------------------------------------------------------------- LLM mode

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "client_decision.v1.md"


def llm_score(*, job: dict[str, Any], prefs: dict[str, Any], cover_letter: str,
              screening_answers: list[str], bid_amount: float, base_url: str, model: str,
              api_key_env: str) -> tuple[float, str]:
    """Ask an LLM, playing the client, to rate the proposal 0-10. Returns (score 0-1, reason)."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = template.format(
        title=job["title"], description=job["description"][:4000],
        must_have=", ".join((job.get("hidden") or {}).get("must_have_skills", [])),
        dislikes_generic=prefs.get("dislikes_generic_opening", True),
        preferred_length=prefs.get("preferred_length", "medium"),
        bid=bid_amount, cover_letter=cover_letter[:5000],
        answers="\n".join(screening_answers)[:3000],
    )
    api_key = os.environ.get(api_key_env, "")
    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "temperature": 0, "seed": 4016,
              "messages": [{"role": "user", "content": prompt}],
              "response_format": {"type": "json_object"}},
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    return max(0.0, min(1.0, float(data["score"]) / 10.0)), str(data.get("reason", ""))[:500]


def cache_key(job_id: str, cover_letter: str, answers: list[str], bid: float, model: str) -> str:
    payload = json.dumps([job_id, cover_letter, answers, bid, model], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


# --------------------------------------------------------------------------- messages

_QUESTION = [
    "Thanks for applying. Before we go further: how have you handled {skill} on a past project, "
    "and how long would the first milestone take you?",
    "Your proposal looks relevant. Could you walk me through a similar project where you used "
    "{skill}? What was the outcome?",
    "Quick question before I shortlist: what would your approach be for the {topic} part, and "
    "what do you need from us to start?",
]
_NEGOTIATE = [
    "I like your proposal, but it's above what we planned. Would you do it for ${counter}?",
    "We're interested. Our budget is tighter than your bid; could you work with ${counter}?",
]
_INTERVIEW = [
    "Your experience looks like a good match. Can we do a 20-minute call this week? "
    "Please send two or three time slots that work for you (with your time zone).",
    "I'd like to talk before deciding. What times are you free for a short video call in the "
    "next few days?",
]
_SCAM = [
    "Great proposal! Let's continue on WhatsApp so we can move faster, my number is "
    "+1 415 555 0136. Payment will be sent outside the platform to avoid fees.",
    "You're hired for a paid trial! First please complete this small test task for free and "
    "send it to my email hr.dept.jobs@gmail.com, then we start the contract.",
    "Before we begin, please buy the software license with your card and I will reimburse you "
    "plus a bonus. Contact me on Telegram @fastpayhr.",
]
_ACCEPT_SLOT = "Perfect, {slot} works for me. I've put it in my calendar. Talk then!"
_NO_SLOT = "Thanks! Could you send specific times (day and hour with time zone)?"
_ACCEPT_PRICE = "Deal, ${price} works for us. Let's set up a quick call to kick off."
_REJECT_PRICE = "Thanks for understanding, but that's still above our budget. We'll pass for now."
_THANKS_SHORTLIST = "Thanks, that's helpful. Could we hop on a short call? Send me a few slots."
_THANKS_DECLINE = "Thanks for the details. We've decided to go with someone else this time."
_HIRED = ("We've decided to hire you. I'm sending the contract now. Looking forward to "
          "working together!")
_LOST_AFTER_CALL = "Thanks for your time on the call. We've chosen another freelancer for this one."
_OFF_PLATFORM_REPLY = ("Please keep communication on this platform; I can't take contact details "
                       "outside it. We'll pass on this proposal.")

SLOT_PATTERN = re.compile(
    r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b[^\n.;]{0,40}?\b\d{1,2}(?::\d{2})?\s?(am|pm)?",
    re.IGNORECASE,
)
PRICE_PATTERN = re.compile(r"\$\s?(\d{1,6}(?:[.,]\d{1,2})?)")


def pick(options: list[str], seed: int, *parts: str) -> str:
    return options[int(_unit(seed, *parts) * len(options)) % len(options)]


def first_message(decision: Decision, *, job: dict[str, Any], prefs: dict[str, Any],
                  bid: float, seed: int, proposal_id: str) -> tuple[str, str, str]:
    """(message body, gold intent, next thread state) for a client opening a conversation."""
    if prefs.get("scammer"):
        return pick(_SCAM, seed, proposal_id, "scam"), "scam_signal", "scam"
    must = (job.get("hidden") or {}).get("must_have_skills") or job.get("skills", ["this"])
    skill = must[0] if must else "this"
    ceiling = ceiling_for(job, prefs)
    if ceiling and bid > 0.92 * ceiling and _unit(seed, proposal_id, "neg") < 0.6:
        counter = int(round(min(bid * 0.85, ceiling * 0.9), -1)) or int(bid * 0.85)
        return (pick(_NEGOTIATE, seed, proposal_id, "n").format(counter=counter),
                "negotiation", "negotiating")
    if decision.outcome == "interview":
        return pick(_INTERVIEW, seed, proposal_id, "i"), "interview_request", "awaiting_slots"
    topic = job["title"].split(" for ")[0].lower()
    return (pick(_QUESTION, seed, proposal_id, "q").format(skill=skill, topic=topic),
            "question", "awaiting_answer")


def respond_to_freelancer(*, state: str, freelancer_text: str, job: dict[str, Any],
                          prefs: dict[str, Any], decision_score: float, bid: float,
                          seed: int, thread_id: str, turn: int) -> tuple[str, str, str, str | None]:
    """Client reply to the freelancer's latest message.

    Returns (body, gold intent, new state, stage event or None).
    """
    if OFF_PLATFORM.search(freelancer_text) and not prefs.get("scammer"):
        return _OFF_PLATFORM_REPLY, "other", "closed", "lost"
    if state == "scam":
        return pick(_SCAM, seed, thread_id, str(turn)), "scam_signal", "scam", None
    if state == "awaiting_slots":
        m = SLOT_PATTERN.search(freelancer_text)
        if m:
            return (_ACCEPT_SLOT.format(slot=m.group(0).strip()), "other",
                    "interview_scheduled", "interview")
        return _NO_SLOT, "interview_request", "awaiting_slots", None
    if state == "negotiating":
        prices = [float(p.replace(",", "")) for p in PRICE_PATTERN.findall(freelancer_text)]
        ceiling = ceiling_for(job, prefs) or bid
        if prices and min(prices) <= ceiling * 0.95:
            return (_ACCEPT_PRICE.format(price=int(min(prices))), "interview_request",
                    "awaiting_slots", None)
        if prices:
            return _REJECT_PRICE, "other", "closed", "lost"
        return (pick(_NEGOTIATE, seed, thread_id, str(turn)).format(counter=int(ceiling * 0.9)),
                "negotiation", "negotiating", None)
    if state == "awaiting_answer":
        must = (job.get("hidden") or {}).get("must_have_skills") or []
        good = (not must or any(mentions(freelancer_text, s) for s in must)) and len(
            freelancer_text.split()) >= 25
        if good:
            return _THANKS_SHORTLIST, "interview_request", "awaiting_slots", None
        return _THANKS_DECLINE, "other", "closed", "lost"
    if state == "interview_scheduled":
        hire = decision_score >= 0.70 and _unit(seed, thread_id, "hire") < 0.75
        if hire:
            return _HIRED, "other", "closed", "hired"
        return _LOST_AFTER_CALL, "other", "closed", "lost"
    return "Thanks for the message.", "other", state, None
