"""Load a local persona corpus with stable evidence IDs and source metadata."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

from scripts.personas.render import load_bible

from .ingest.pdf import extract_pdf


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    persona: str
    type: str
    source: str
    title: str
    text: str
    project_id: str | None = None
    skills: tuple[str, ...] = ()
    date: str | None = None
    client_industry: str | None = None
    outcome: str | None = None


def _case_metadata(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8").strip()
    first, _, remainder = text.partition("\n")
    front, marker, body = remainder.partition("\n---\n")
    if first != "---" or not marker:
        raise ValueError(f"Case study has no YAML front matter: {path}")
    return yaml.safe_load(front), body.strip()


def _rows(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def load_corpus(persona_dir: Path) -> list[Evidence]:
    """Read the committed persona layout or a compatible local portfolio directory."""
    bible = load_bible(persona_dir)
    persona = bible.persona_id
    projects = {project.id: project for project in bible.projects}

    def source(path: Path) -> str:
        return f"{persona}/{path.relative_to(persona_dir).as_posix()}"

    cv = persona_dir / "cv.pdf"
    evidence = [
        Evidence(
            id=f"cv-{persona}",
            persona=persona,
            type="cv",
            source=source(cv),
            title=f"{bible.name} CV",
            text=extract_pdf(cv),
            skills=tuple(skill for group in bible.skills.values() for skill in group),
        )
    ]

    for path in sorted((persona_dir / "case_studies").glob("*.md")):
        meta, text = _case_metadata(path)
        if meta["persona"] != persona:
            raise ValueError(f"Case study persona does not match its corpus: {path}")
        evidence.append(
            Evidence(
                id=meta["id"],
                persona=persona,
                type="case_study",
                source=source(path),
                title=meta["title"],
                text=text,
                project_id=meta["project_id"],
                skills=tuple(meta.get("skills", ())),
                date=str(meta["date"]),
                client_industry=meta["client_industry"],
            )
        )

    for project in bible.projects:
        if project.repo is None:
            continue
        repo = persona_dir / "repos" / project.repo.name
        readme = repo / "README.md"
        parts = [readme.read_text(encoding="utf-8")]
        code_suffixes = {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".sql",
            ".go",
            ".sh",
            ".yml",
            ".yaml",
            ".gs",
        }
        for path in sorted(repo.rglob("*")):
            if path.is_file() and (path.suffix in code_suffixes or path.name == "Dockerfile"):
                parts.append(
                    f"{path.relative_to(repo).as_posix()}\n{path.read_text(encoding='utf-8')}"
                )
        evidence.append(
            Evidence(
                id=f"repo-{project.repo.name}",
                persona=persona,
                type="repo",
                source=source(readme),
                title=project.repo.name,
                text="\n\n".join(parts),
                project_id=project.id,
                skills=tuple(project.skills_explicit + project.skills_implied),
                date=project.end,
                client_industry=project.client_industry,
            )
        )

    reviews = persona_dir / "reviews.jsonl"
    for item in _rows(reviews):
        project = projects[item["project_id"]]
        evidence.append(
            Evidence(
                id=item["id"],
                persona=persona,
                type="review",
                source=source(reviews),
                title=f"Review of {project.name}",
                text=item["text"],
                project_id=project.id,
                skills=tuple(project.skills_explicit),
                date=item["date"],
                client_industry=item["client_industry"],
            )
        )

    proposals = persona_dir / "past_proposals.jsonl"
    for item in _rows(proposals):
        evidence.append(
            Evidence(
                id=item["id"],
                persona=persona,
                type="past_proposal",
                source=source(proposals),
                title=item["job_title"],
                text=item["cover_letter"],
                skills=tuple(item["job_skills"]),
                date=item["date"],
                client_industry=item["client_industry"],
                outcome=item["outcome"],
            )
        )

    if len({item.id for item in evidence}) != len(evidence):
        raise ValueError(f"Duplicate evidence ids in {persona}'s corpus")
    return evidence
