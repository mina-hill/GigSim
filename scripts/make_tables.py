"""Check or regenerate evaluation tables from explicit report artifacts."""

import argparse
from html import escape
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ("README.md", "docs/EVALUATION_RESULTS.md")
BEGIN = "<!-- BEGIN EVALUATION RESULTS -->"
END = "<!-- END EVALUATION RESULTS -->"


class _ReportModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, str_strip_whitespace=True, hide_input_in_errors=True
    )


class Metric(_ReportModel):
    name: Annotated[str, Field(min_length=1)]
    value: Annotated[int | float, Field(allow_inf_nan=False)]
    denominator: Annotated[int, Field(gt=0)] | None = None
    unit: Annotated[str, Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def validate_count(self) -> "Metric":
        if self.denominator is not None and (
            type(self.value) is not int or not 0 <= self.value <= self.denominator
        ):
            raise ValueError("A metric with a denominator must be a count within that total")
        return self


class ReportResult(_ReportModel):
    schema_version: Literal[1]
    experiment: Annotated[str, Field(min_length=1)]
    scope: Annotated[str, Field(min_length=1)]
    command: Annotated[str, Field(min_length=1)]
    metrics: Annotated[list[Metric], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_metric_names(self) -> "ReportResult":
        if len({metric.name for metric in self.metrics}) != len(self.metrics):
            raise ValueError("Metric names must be unique within a result")
        return self


def load_results(result_dir: Path) -> list[tuple[Path, ReportResult]]:
    results = []
    for path in sorted(result_dir.glob("*.report.json")):
        try:
            result = ReportResult.model_validate_json(path.read_bytes())
        except ValidationError as error:
            raise ValueError(f"{path.name} has an invalid report schema\n{error}") from error
        results.append((path, result))
    return results


def _md_cell(value: str) -> str:
    value = escape(" ".join(value.split()), quote=False)
    for char in ("\\", "|", "`", "*", "_", "[", "]", "~"):
        value = value.replace(char, "\\" + char)
    return value


def _display_value(metric: Metric) -> str:
    value = str(metric.value)
    if metric.denominator is not None:
        value += f" / {metric.denominator}"
    if metric.unit is not None:
        value += f" {_md_cell(metric.unit)}"
    return value


def render_results(results: list[tuple[Path, ReportResult]], source_prefix: str) -> str:
    if not results:
        return "Evaluation results are pending."

    sections = []
    for path, result in results:
        rows = [
            f"| {_md_cell(metric.name)} | {_display_value(metric)} |" for metric in result.metrics
        ]
        sections.append("\n".join([
            f"### {_md_cell(result.experiment)}",
            "",
            _md_cell(result.scope),
            "",
            "| Metric | Result |",
            "| --- | ---: |",
            *rows,
            "",
            f"[Result artifact]({quote(source_prefix + path.name)})",
            "",
            f"Reproduce with <code>{escape(result.command)}</code>.",
        ]))
    return "\n\n".join(sections)


def replace_block(current: str, table: str, path: Path) -> str:
    if current.count(BEGIN) != 1 or current.count(END) != 1:
        raise ValueError(f"{path} must contain exactly one pair of evaluation result markers")
    before, _, rest = current.partition(BEGIN)
    _, end, after = rest.partition(END)
    if not end:
        raise ValueError(f"{path} has evaluation result markers in the wrong order")
    return f"{before}{BEGIN}\n\n{table}\n\n{END}{after}"


def sync_results(root: Path, *, check: bool) -> list[Path]:
    results = load_results(root / "evals" / "results")
    updates = {}
    for document in DOCUMENTS:
        path = root / document
        prefix = "../" if "/" in document else ""
        table = render_results(results, prefix + "evals/results/")
        current = path.read_text(encoding="utf-8")
        new = replace_block(current, table, Path(document))
        if current != new:
            updates[path] = new

    if not check:
        for path, new in updates.items():
            path.write_text(new, encoding="utf-8", newline="\n")
    return list(updates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the tables need regeneration")
    args = parser.parse_args()
    try:
        changed = sync_results(ROOT, check=args.check)
    except (OSError, ValueError) as error:
        print(error)
        return 1
    if args.check and changed:
        paths = ", ".join(path.relative_to(ROOT).as_posix() for path in changed)
        print(f"Stale evaluation tables in {paths}. Run python -m scripts.make_tables.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
