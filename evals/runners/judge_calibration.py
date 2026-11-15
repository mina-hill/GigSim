"""Prepare a 30-item blind grounding calibration packet and score real labels."""

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, model_validator

from agent.config import REPO_ROOT, get_config
from agent.llm import get_chat_model
from agent.prompts.loader import render_prompt, wrap_untrusted
from rag.corpus import Evidence
from rag.verify import verify_claims
from scripts.personas.render import load_bible

DEFAULT_OUTPUT = REPO_ROOT / ".cache" / "judge-calibration"
SEED = 4016
ITEMS = 30
Rating = Literal["supported", "partly_supported", "unsupported"]
RATINGS: tuple[Rating, ...] = ("supported", "partly_supported", "unsupported")
CLAIMS_WITHOUT_EVIDENCE = (
    "The system was certified for continuous use in a nuclear power station.",
    "I licensed the finished code to five national broadcasters.",
    "The product rolled out to all 210 hospitals in the region.",
    "Our work replaced an entire warehouse sorting line within three days.",
    "The prototype was used for high-altitude navigation by two airlines.",
    "The client doubled their physical manufacturing throughput within a month.",
    "My team was awarded a patent covering a new pressure sensor design.",
    "I implemented automatic language support in twelve regional dialects.",
    "The new process eliminated the client's need for manual quality inspections.",
    "I secured an agreement to distribute the product in seven foreign markets.",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CalibrationCase(StrictModel):
    case_id: str
    claim: str
    evidence: str

    @model_validator(mode="after")
    def validate_case(self) -> Self:
        if not self.claim.strip() or not self.evidence.strip():
            raise ValueError("Claims and evidence must be nonempty.")
        if self.case_id != _case_id(self.claim, self.evidence):
            raise ValueError("Case ID does not match claim and evidence.")
        return self


class Judgment(StrictModel):
    rating: Rating


class Label(StrictModel):
    case_id: str
    rating: Rating | None


class Predictions(StrictModel):
    backend: Literal["verifier", "ollama"]
    model: str
    labels: list[Label]


def _case_id(claim: str, evidence: str) -> str:
    digest = hashlib.sha256(f"{claim}\0{evidence}".encode()).hexdigest()
    return f"case-{digest[:12]}"


def build_cases() -> list[CalibrationCase]:
    personas = REPO_ROOT / "data" / "personas"
    excerpts = []
    for persona, count in (("ai_ml", 4), ("data_analyst", 3), ("fullstack", 3)):
        bible = load_bible(personas / persona)
        for project in bible.projects[:count]:
            excerpts.append(project.approach[0].rstrip(".") + ".")
    cases = []
    for evidence, unsupported in zip(excerpts, CLAIMS_WITHOUT_EVIDENCE, strict=True):
        claim = "I " + evidence[0].lower() + evidence[1:]
        mixed = claim.removesuffix(".") + " and received a federal innovation grant for it."
        for candidate in (claim, mixed, unsupported):
            cases.append(
                CalibrationCase(
                    case_id=_case_id(candidate, evidence), claim=candidate, evidence=evidence
                )
            )
    random.Random(SEED).shuffle(cases)
    return cases


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[BaseModel]) -> None:
    text = "\n".join(row.model_dump_json() for row in rows) + "\n"
    path.write_text(text, encoding="utf-8")


def _load_cases(path: Path) -> list[CalibrationCase]:
    cases = [
        CalibrationCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(cases) != ITEMS or len({case.case_id for case in cases}) != ITEMS:
        raise ValueError(f"The packet must contain {ITEMS} unique items.")
    return cases


def _require_labels(labels: list[Label], expected_ids: set[str], source: str) -> dict[str, Rating]:
    records: dict[str, Rating] = {}
    for label in labels:
        if label.case_id in records:
            raise ValueError(f"Duplicate {source} label for {label.case_id}.")
        if label.case_id not in expected_ids or label.rating is None:
            raise ValueError(f"Invalid or missing {source} rating for {label.case_id}.")
        records[label.case_id] = label.rating
    missing = expected_ids - records.keys()
    if missing:
        raise ValueError(f"Missing {source} ratings for {len(missing)} item(s).")
    return records


def prepare(output_dir: Path) -> None:
    cases = build_cases()
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "cases.jsonl", cases)
    label_path = output_dir / "reviewer-labels.jsonl"
    if not label_path.exists():
        _write_jsonl(label_path, [Label(case_id=case.case_id, rating=None) for case in cases])
    instructions = [
        "# Blind portfolio grounding review",
        "",
        "Score each claim using only its evidence. Exact wording is optional.",
        "Use `supported` if all parts follow, `partly_supported` if some parts follow,",
        "or `unsupported` if the central claim lacks evidence or contradicts it.",
        "Replace each `null` rating in `reviewer-labels.jsonl` with one of those strings.",
        "Complete the labels independently before reading any automated ratings.",
        "",
    ]
    for case in cases:
        instructions.extend(
            [
                f"## {case.case_id}",
                "",
                f"**Claim** {case.claim}",
                "",
                f"**Evidence** {case.evidence}",
                "",
            ]
        )
    (output_dir / "reviewer-packet.md").write_text("\n".join(instructions), encoding="utf-8")


def _verifier_rating(case: CalibrationCase) -> Rating:
    evidence = Evidence(
        id=case.case_id,
        persona="synthetic",
        type="case_study",
        source="calibration_packet",
        title="Calibration evidence",
        text=case.evidence,
    )
    evaluated = verify_claims(case.claim, [evidence])
    if len(evaluated) != 1:
        raise ValueError(f"Expected one claim for {case.case_id}; got {len(evaluated)}.")
    return evaluated[0].status


def _ollama_predictions(cases: list[CalibrationCase]) -> Predictions:
    config = get_config()
    ref = config.model_for_node("judge")
    if ref.provider != "ollama":
        raise ValueError("Set MODEL_PROFILE=local to use the local Ollama judge.")
    endpoint = urlsplit(config.models.providers[ref.provider].base_url or "")
    if endpoint.scheme not in {"http", "https"} or endpoint.hostname not in {
        "127.0.0.1",
        "::1",
        "localhost",
    }:
        raise ValueError("The Ollama judge must use a loopback endpoint.")
    model = get_chat_model("judge", use_cache=False, cfg=config).bind(
        response_format={"type": "json_object"}
    )
    results = []
    for case in cases:
        prompt = render_prompt(
            "judge",
            claim=wrap_untrusted(case.claim, "portfolio_claim"),
            evidence=wrap_untrusted(case.evidence, "portfolio_evidence"),
        )
        response = model.invoke([("system", prompt)])
        if not isinstance(response.content, str):
            raise ValueError(f"Expected a JSON text response for {case.case_id}.")
        judgment = Judgment.model_validate_json(response.content)
        results.append(Label(case_id=case.case_id, rating=judgment.rating))
    return Predictions(backend="ollama", model=ref.model, labels=results)


def judge(cases: list[CalibrationCase], backend: Literal["verifier", "ollama"]) -> Predictions:
    if backend == "ollama":
        return _ollama_predictions(cases)
    labels = [Label(case_id=case.case_id, rating=_verifier_rating(case)) for case in cases]
    return Predictions(backend="verifier", model="deterministic-claim-verifier", labels=labels)


def agreement(expected: dict[str, Rating], actual: dict[str, Rating]) -> dict:
    if not expected or expected.keys() != actual.keys():
        raise ValueError("Ratings must cover the same nonempty set of items.")
    n = len(expected)
    counts = Counter((rating, actual[key]) for key, rating in expected.items())
    observed = sum(counts[rating, rating] for rating in RATINGS)
    expected_counts = Counter(expected.values())
    actual_counts = Counter(actual.values())
    chance = sum(expected_counts[rating] * actual_counts[rating] for rating in RATINGS)
    kappa = None if chance == n * n else round((observed * n - chance) / (n * n - chance), 4)
    return {
        "items": n,
        "exact_agreements": observed,
        "observed_agreement": round(observed / n, 4),
        "expected_agreement": round(chance / (n * n), 4),
        "cohens_kappa": kappa,
        "confusion_matrix_human_by_automated": {
            human: {machine: counts[human, machine] for machine in RATINGS} for human in RATINGS
        },
    }


def score(cases: list[CalibrationCase], labels_path: Path, predictions: Predictions) -> dict:
    expected_ids = {case.case_id for case in cases}
    supplied = [
        Label.model_validate_json(line)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    human = _require_labels(supplied, expected_ids, "reviewer")
    machine = _require_labels(predictions.labels, expected_ids, "automated")
    return {
        "dataset": "30 synthetic portfolio grounding claims derived from 10 evidence excerpts",
        "reference_ratings": "user-supplied review labels",
        "automated_backend": predictions.backend,
        "automated_model": predictions.model,
        **agreement(human, machine),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_OUTPUT)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("prepare", help="Write the blind packet and a blank label template.")
    automated = subcommands.add_parser(
        "judge", help="Write automated ratings in the packet folder."
    )
    automated.add_argument("--backend", choices=("verifier", "ollama"), default="verifier")
    subcommands.add_parser(
        "score", help="Compare the filled reviewer labels with automated ratings."
    )
    args = parser.parse_args()
    directory = args.directory
    if args.command == "prepare":
        prepare(directory)
        print(directory / "reviewer-packet.md")
        return
    try:
        cases = _load_cases(directory / "cases.jsonl")
        machine_path = directory / "automated-ratings.json"
        if args.command == "judge":
            predictions = judge(cases, args.backend)
            _write_json(machine_path, predictions.model_dump(mode="json"))
            print(machine_path)
            return
        predictions = Predictions.model_validate_json(machine_path.read_bytes())
        result = score(cases, directory / "reviewer-labels.jsonl", predictions)
        _write_json(directory / "calibration-result.json", result)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
