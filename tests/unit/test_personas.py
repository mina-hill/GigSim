import json
import re
from pathlib import Path

import pytest

from scripts.personas.render import PERSONAS_DIR, load_bible

PERSONAS = ["ai_ml", "fullstack", "data_analyst"]


@pytest.mark.parametrize("persona", PERSONAS)
def test_bible_is_valid_and_synthetic(persona):
    b = load_bible(PERSONAS_DIR / persona)
    assert b.synthetic and 15 <= len(b.projects) <= 25
    ids = [p.id for p in b.projects]
    assert len(ids) == len(set(ids))
    assert all(p.decoy_of in ids for p in b.projects if p.decoy_of)


@pytest.mark.parametrize("persona", PERSONAS)
def test_case_study_prose_never_names_implied_skills(persona):
    b = load_bible(PERSONAS_DIR / persona)
    folder = PERSONAS_DIR / persona / "case_studies"
    for p in b.projects:
        text = next(folder.glob(f"{p.id}-*.md")).read_text()
        prose = text.split("---\n", 2)[2].split("An excerpt from")[0]   # code excerpts may
        for skill in p.skills_implied:                                   # reveal them
            assert not re.search(rf"\b{re.escape(skill)}\b", prose, re.IGNORECASE), (p.id, skill)


@pytest.mark.parametrize("persona", PERSONAS)
def test_past_proposals_are_labelled(persona):
    rows = [json.loads(line) for line in
            (Path(PERSONAS_DIR) / persona / "past_proposals.jsonl").read_text().splitlines()]
    assert len(rows) == 150
    assert {r["outcome"] for r in rows} == {"won", "replied", "ignored"}
    assert all(r["synthetic"] and r["label_source"] for r in rows)
