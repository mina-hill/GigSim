"""Text helpers shared by preprocessing and the world builder: cleaning, PII masking,
number parsing, domain tagging and near-duplicate detection."""

from __future__ import annotations

import hashlib
import html
import math
import re
from collections.abc import Iterable

from datasketch import MinHash, MinHashLSH

URL = re.compile(r"https?://\S+|www\.\S+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?<!\d)\+?\d[\d\s().-]{8,}\d(?!\d)")
TAG = re.compile(r"<[^>]+>")
SPACES = re.compile(r"[ \t]+")


def clean_text(text: object) -> str:
    """Strip HTML, mask links, emails and phone numbers, normalise whitespace."""
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return ""
    s = html.unescape(str(text))
    s = TAG.sub(" ", s)
    s = URL.sub("[link removed]", s)
    s = EMAIL.sub("[email removed]", s)
    s = PHONE.sub("[phone removed]", s)
    s = SPACES.sub(" ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


_EN_STOP = set("the a an and or to of for in on with we you your our is are be will this that "
               "it as at by from need looking have has can who".split())


def mostly_english(text: str, threshold: float = 0.9) -> bool:
    """ASCII letters plus a share of common English function words (catches Spanish etc.)."""
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isascii() for c in letters) / len(letters) < threshold:
        return False
    words = re.findall(r"[a-z']+", text.lower())
    if len(words) < 8:
        return True
    return sum(w in _EN_STOP for w in words) / len(words) >= 0.06


_NUM = re.compile(r"(-?\d+(?:[.,]\d+)*)\s*([kKmM])?")


def parse_money(value: object) -> float | None:
    """'$1,200' -> 1200.0, '$10K+' -> 10000.0, '' / NaN / 'N/A' -> None."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return None if (isinstance(value, float) and math.isnan(value)) else float(value)
    m = _NUM.search(str(value).replace("$", ""))
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").lower()
    return number * {"k": 1_000, "m": 1_000_000}.get(suffix, 1)


def parse_bool(value: object) -> bool | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "verified", "payment verified", "hourly"}:
        return True
    if s in {"0", "false", "no", "n", "unverified", "payment unverified", "fixed",
             "fixed-price", "fixed price"}:
        return False
    return None


def norm_level(value: object) -> str:
    s = str(value or "").lower()
    if "entry" in s or "beginner" in s:
        return "entry"
    if "expert" in s or "senior" in s:
        return "expert"
    return "intermediate"


DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "ai_ml": [
        "machine learning", "deep learning", "nlp", "natural language", "llm", "gpt", "chatgpt",
        "chatbot", "rag", "langchain", "langgraph", "computer vision", "pytorch", "tensorflow",
        "ai agent", "ai model", "data scientist", "data science", "neural", "openai",
        "hugging face", "fine-tun", "prediction model", "recommendation system", "ml engineer",
        "generative ai", "object detection", "image classification", "forecasting model",
    ],
    "fullstack_web": [
        "react", "next.js", "nextjs", "node.js", "nodejs", "full stack", "full-stack",
        "fullstack", "frontend", "front-end", "backend", "back-end", "web app", "django",
        "laravel", "shopify app", "stripe", "saas", "typescript", "javascript", "vue",
        "angular", "mern", "web developer", "rest api", "graphql", "supabase", "firebase",
    ],
    "data_analysis": [
        "data analyst", "data analysis", "excel", "power bi", "powerbi", "tableau",
        "dashboard", "sql", "looker", "data visualization", "spreadsheet", "google sheets",
        "statistics", "statistical", "reporting", "business intelligence", "etl", "dbt",
        "data cleaning", "analytics", "pivot table", "bigquery", "data entry analysis",
    ],
}
_DOMAIN_RE = {d: [re.compile(r"(?<![a-z])" + re.escape(k)) for k in kws]
              for d, kws in DOMAIN_KEYWORDS.items()}


def tag_domain(title: str, body: str = "", skills: Iterable[str] = ()) -> str:
    """Keyword vote; the title counts double. Returns ai_ml, fullstack_web, data_analysis
    or other."""
    t, b = title.lower(), (body + " " + " ".join(skills)).lower()
    scores = {d: sum(2 * bool(p.search(t)) + bool(p.search(b)) for p in pats)
              for d, pats in _DOMAIN_RE.items()}
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] >= 2 else "other"


def exact_key(*parts: str) -> str:
    joined = " ".join(re.sub(r"\W+", " ", p.lower()).strip() for p in parts)
    return hashlib.sha1(joined.encode()).hexdigest()


def _shingles(text: str, k: int = 5) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i:i + k]) for i in range(max(1, len(words) - k + 1))}


def near_duplicate_groups(texts: list[str], threshold: float = 0.9,
                          num_perm: int = 64) -> list[int]:
    """For each text, the index of the first earlier text it nearly duplicates, else itself."""
    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    keep_of: list[int] = []
    for i, text in enumerate(texts):
        m = MinHash(num_perm=num_perm)
        for sh in _shingles(text):
            m.update(sh.encode())
        hits = lsh.query(m)
        if hits:
            keep_of.append(min(int(h) for h in hits))
        else:
            lsh.insert(str(i), m)
            keep_of.append(i)
    return keep_of
