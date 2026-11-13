"""Render each persona's portfolio corpus from its bible.

    uv run python -m scripts.personas.render              # template mode, offline, deterministic
    uv run python -m scripts.personas.render --llm        # rewrite case studies with the
                                                          # data_generator model (needs a key)

Outputs, per persona, under data/personas/<persona_id>/:
    cv.md, cv.pdf                  canonical project names, one result line each
    case_studies/<id>.md           first alias as the title; implied skills never named
    repos/<repo>/README.md + code  repo name, second alias; code reveals implied skills
    reviews.jsonl                  client reviews
    past_proposals.jsonl           about 150 proposals labelled won / replied / ignored
    manifest.json                  counts and generator settings

The same project therefore appears three ways (CV, case study, README) under different names,
which is the "messy corpus" the RAG part of the brief asks for. Everything is synthetic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from scripts.personas.code_templates import TEMPLATES
from scripts.personas.proposals import generate_past_proposals

ROOT = Path(__file__).resolve().parents[2]
PERSONAS_DIR = ROOT / "data" / "personas"
GENERATOR_VERSION = "render-v1"


# --------------------------------------------------------------------------- bible schema


class Result(BaseModel):
    metric: str
    value: str

    @field_validator("value", mode="before")
    @classmethod
    def _as_text(cls, v: Any) -> str:
        return str(v)


class Repo(BaseModel):
    name: str
    kind: str
    readme_style: str = "clear"   # clear | vague


class Review(BaseModel):
    rating: float
    text: str


class Project(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(min_length=1)
    client: str | None
    client_industry: str
    type: str
    start: str
    end: str

    @field_validator("start", "end", mode="before")
    @classmethod
    def _date_text(cls, v: Any) -> str:
        return str(v)
    skills_explicit: list[str]
    skills_implied: list[str] = Field(default_factory=list)
    problem: str
    approach: list[str]
    results: list[Result]
    repo: Repo | None = None
    review: Review | None = None
    decoy_of: str | None = None


class Bible(BaseModel):
    persona_id: str
    account_id: str
    synthetic: bool
    name: str
    headline: str
    location: str
    years_experience: int
    rates: dict[str, float]
    summary: str
    education: list[dict[str, Any]]
    skills: dict[str, list[str]]
    projects: list[Project]


def load_bible(persona_dir: Path) -> Bible:
    raw = yaml.safe_load((persona_dir / "bible.yaml").read_text(encoding="utf-8"))
    bible = Bible.model_validate(raw)
    if not bible.synthetic:
        raise ValueError("Persona bibles must be marked synthetic")
    for p in bible.projects:
        if p.repo and p.repo.kind not in TEMPLATES:
            raise ValueError(f"{p.id}: unknown repo kind {p.repo.kind}")
    return bible


# --------------------------------------------------------------------------- helpers


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def front_matter(meta: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip() + "\n---\n"


def rng_for(*parts: str) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16))


def lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def results_sentence(p: Project) -> str:
    parts = [f"{r.metric}: {r.value}" for r in p.results]
    return "; ".join(parts)


# --------------------------------------------------------------------------- CV


def render_cv(b: Bible) -> str:
    lines = [f"# {b.name}", "", f"**{b.headline}**  ", f"{b.location} · "
             f"{b.years_experience} years of experience", "", "## Summary", "", b.summary.strip(),
             "", "## Skills", "", "Core: " + ", ".join(b.skills["core"]), "",
             "Also: " + ", ".join(b.skills.get("familiar", [])), "", "## Selected projects", ""]
    for p in sorted(b.projects, key=lambda x: x.start, reverse=True):
        who = f"{p.client}, {p.client_industry}" if p.client else p.client_industry
        top = p.results[0]
        lines.append(f"- **{p.name}** ({who}, {p.start[:4]}): {lower_first(p.approach[0])}. "
                     f"{top.metric.capitalize()}: {top.value}.")
    lines += ["", "## Education", ""]
    for e in b.education:
        lines.append(f"- {e['degree']}, {e['school']}, {e['year']}")
    lines += ["", "_Synthetic persona created for the GigBid project._", ""]
    return "\n".join(lines)


def write_cv_pdf(markdown: str, path: Path) -> None:
    from fpdf import FPDF

    def latin1(text: str) -> str:
        text = (text.replace("·", "-").replace("–", "-").replace("—", "-").replace("’", "'")
                .replace("“", '"').replace("”", '"').replace("×", "x"))
        return text.encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    for raw in markdown.splitlines():
        line = latin1(raw.replace("**", "").replace("_", "").rstrip())
        if raw.startswith("# "):
            pdf.set_font("Helvetica", "B", 18)
            pdf.multi_cell(0, 9, line[2:], new_x="LMARGIN", new_y="NEXT")
        elif raw.startswith("## "):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 13)
            pdf.multi_cell(0, 7, line[3:], new_x="LMARGIN", new_y="NEXT")
        elif not line.strip():
            pdf.ln(2)
        else:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, line.replace("- ", "* ", 1) if line.startswith("- ") else line,
                           new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


# --------------------------------------------------------------------------- case studies


def render_case_study(b: Bible, p: Project) -> str:
    """Three layouts, chosen per project, so documents are not uniform."""
    r = rng_for(b.persona_id, p.id, "case")
    title = p.aliases[0]
    who = f"a {p.client_industry} client" if p.client else "a personal project"
    meta = {
        "id": f"cs-{p.id}", "project_id": p.id, "type": "case_study", "persona": b.persona_id,
        "title": title, "client_industry": p.client_industry, "date": p.end,
        "skills": p.skills_explicit, "synthetic": True,
    }
    style = r.choice(["story", "bullets", "qa"])
    if p.decoy_of:
        style = "bullets"
    body: list[str] = [f"# {title}", ""]
    if style == "story":
        body += [
            f"For {who} ({p.start} to {p.end}). {p.problem}", "",
            " ".join(f"{a}." for a in p.approach), "",
            f"Outcome: {results_sentence(p)}.",
        ]
    elif style == "bullets":
        body += [f"**Context.** {p.problem}", "", "**What I did**", ""]
        body += [f"- {a}" for a in p.approach]
        body += ["", "**Results**", ""] + [f"- {x.metric}: {x.value}" for x in p.results]
        body += ["", "Tools: " + ", ".join(p.skills_explicit)]
    else:
        body += [
            "**What was the problem?**", "", p.problem, "",
            "**How did you solve it?**", "", "; ".join(lower_first(a) for a in p.approach) + ".",
            "", "**Did it work?**", "", f"Yes: {results_sentence(p)}.",
        ]
    # Mixed code and prose: some case studies quote a few lines of the repo.
    if p.repo and r.random() < 0.5:
        files = TEMPLATES[p.repo.kind]
        first_path = sorted(files)[0]
        snippet = "\n".join(fill_template(files[first_path], b, p).splitlines()[:10])
        body += ["", f"An excerpt from `{first_path}`:", "", "```", snippet, "```"]
    return front_matter(meta) + "\n".join(body) + "\n"


# --------------------------------------------------------------------------- repos


def fill_template(text: str, b: Bible, p: Project) -> str:
    out = (text.replace("{repo}", p.repo.name if p.repo else "")
           .replace("{project}", p.name).replace("{industry}", p.client_industry))
    return out.replace("{{", "{").replace("}}", "}")


def render_readme(b: Bible, p: Project) -> str:
    assert p.repo is not None
    alias = p.aliases[1] if len(p.aliases) > 1 else p.aliases[0]
    if p.repo.readme_style == "vague":
        return (f"# {p.repo.name}\n\nInternal tool from the {alias} work. Run it the usual way; "
                f"see the code for details.\n\n_Synthetic repository for the GigBid project._\n")
    lines = [f"# {p.repo.name}", "", f"{alias}: {lower_first(p.problem)}", "", "## What it does",
             ""] + [f"- {a}" for a in p.approach] + ["", "## Results", ""]
    lines += [f"- {x.metric}: {x.value}" for x in p.results]
    lines += ["", "_Synthetic repository for the GigBid project._", ""]
    return "\n".join(lines)


def write_repo(b: Bible, p: Project, out: Path) -> list[str]:
    assert p.repo is not None
    repo_dir = out / "repos" / p.repo.name
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.md").write_text(render_readme(b, p), encoding="utf-8")
    (repo_dir / "PROJECT.json").write_text(json.dumps(
        {"project_id": p.id, "persona": b.persona_id, "synthetic": True}, indent=2) + "\n",
        encoding="utf-8")
    written = ["README.md"]
    for rel, text in TEMPLATES[p.repo.kind].items():
        path = repo_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fill_template(text, b, p), encoding="utf-8")
        written.append(rel)
    return written


# --------------------------------------------------------------------------- reviews


def render_reviews(b: Bible) -> list[dict[str, Any]]:
    out = []
    for p in b.projects:
        if p.review is None:
            continue
        out.append({"id": f"rv-{p.id}", "type": "review", "persona": b.persona_id,
                    "project_id": p.id, "client_industry": p.client_industry,
                    "date": p.end, "rating": p.review.rating, "text": p.review.text,
                    "synthetic": True})
    return out


# --------------------------------------------------------------------------- LLM rewrite


def llm_rewrite_case_study(b: Bible, p: Project, text: str) -> str:
    """Optional: let the data_generator model rewrite a case study in natural prose.

    Facts must not change and implied skills must stay unnamed; rewrites that break the second
    rule are rejected and the template version is kept.
    """
    from agent.llm import get_chat_model
    from agent.prompts.loader import render_prompt

    meta_end = text.index("\n---\n", 4) + 5
    meta, body = text[:meta_end], text[meta_end:]
    prompt = render_prompt("data_case_study", "v1", case_study=body,
                           forbidden=", ".join(p.skills_implied))
    new_body = str(get_chat_model("data_generator").invoke(prompt).content).strip()
    if any(re.search(rf"\b{re.escape(s)}\b", new_body, re.IGNORECASE) for s in p.skills_implied):
        return text
    return meta + new_body + "\n"


# --------------------------------------------------------------------------- main


def render_persona(persona_dir: Path, *, use_llm: bool = False, n_proposals: int = 150) -> dict:
    b = load_bible(persona_dir)
    for sub in ("case_studies", "repos"):
        shutil.rmtree(persona_dir / sub, ignore_errors=True)
    (persona_dir / "case_studies").mkdir(parents=True)

    cv = render_cv(b)
    (persona_dir / "cv.md").write_text(cv, encoding="utf-8")
    write_cv_pdf(cv, persona_dir / "cv.pdf")

    for p in b.projects:
        text = render_case_study(b, p)
        if use_llm:
            text = llm_rewrite_case_study(b, p, text)
        (persona_dir / "case_studies" / f"{p.id}-{slug(p.aliases[0])}.md").write_text(
            text, encoding="utf-8")

    repos = {p.repo.name: write_repo(b, p, persona_dir) for p in b.projects if p.repo}

    reviews = render_reviews(b)
    with (persona_dir / "reviews.jsonl").open("w", encoding="utf-8") as fh:
        for row in reviews:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    proposals = generate_past_proposals(b, n=n_proposals)
    with (persona_dir / "past_proposals.jsonl").open("w", encoding="utf-8") as fh:
        for row in proposals:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    outcomes: dict[str, int] = {}
    for row in proposals:
        outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
    manifest = {
        "persona_id": b.persona_id, "synthetic": True, "generator": GENERATOR_VERSION,
        "llm_rewrite": use_llm, "case_studies": len(b.projects), "repos": repos,
        "reviews": len(reviews), "past_proposals": len(proposals), "outcomes": outcomes,
    }
    (persona_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                               encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--llm", action="store_true", help="rewrite case studies with an LLM")
    parser.add_argument("--persona", action="append", help="only these persona ids")
    parser.add_argument("--proposals", type=int, default=150)
    args = parser.parse_args()
    dirs = sorted(d for d in PERSONAS_DIR.iterdir() if (d / "bible.yaml").exists())
    if args.persona:
        dirs = [d for d in dirs if d.name in set(args.persona)]
    for d in dirs:
        print(json.dumps(render_persona(d, use_llm=args.llm, n_proposals=args.proposals)))


if __name__ == "__main__":
    main()
