from __future__ import annotations

import json
from pathlib import Path

import typer

from roadmaps_mvp.io import repo_root, write_json
from roadmaps_mvp.normalize import SkillResolver, build_skill_taxonomy, ensure_taxonomy, normalization_report
from roadmaps_mvp.planner import (
    build_catalog,
    build_roadmap,
    load_program,
    rank_track_courses,
    rank_slot_options,
    resolve_catalog,
    write_catalog,
)
from roadmaps_mvp.schema_tools import write_schema_snapshots
from roadmaps_mvp.validate import validate_raw_course_dir


app = typer.Typer(no_args_is_help=True, add_completion=False)


def _default_raw_dir() -> Path:
    return repo_root()


@app.command("generate-schemas")
def generate_schemas(output_dir: Path = typer.Option(Path("schemas"), help="Directory for generated JSON Schema snapshots.")) -> None:
    written = write_schema_snapshots(output_dir)
    typer.echo(json.dumps({"written": [str(path) for path in written]}, ensure_ascii=False, indent=2))


@app.command("bootstrap-skills-taxonomy")
def bootstrap_skills_taxonomy(
    raw_dir: Path = typer.Option(_default_raw_dir(), help="Directory containing raw course JSON files."),
    output: Path = typer.Option(Path("taxonomies/skills.json"), help="Output taxonomy file."),
) -> None:
    raw_courses, issues = validate_raw_course_dir(raw_dir)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        typer.echo(json.dumps({"errors": [issue.__dict__ for issue in errors]}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)
    taxonomy = build_skill_taxonomy(raw_courses)
    write_json(output, taxonomy.model_dump(mode="json"))
    typer.echo(json.dumps({"taxonomy_path": str(output), "skills": len(taxonomy.skills)}, ensure_ascii=False, indent=2))


@app.command("validate-courses")
def validate_courses(raw_dir: Path = typer.Option(_default_raw_dir(), help="Directory containing raw course JSON files.")) -> None:
    courses, issues = validate_raw_course_dir(raw_dir)
    errors = [issue.__dict__ for issue in issues if issue.severity == "error"]
    warnings = [issue.__dict__ for issue in issues if issue.severity == "warning"]
    payload = {
        "validated_courses": len(courses),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if errors:
        raise typer.Exit(code=1)


@app.command("normalize-skills")
def normalize_skills(
    raw_dir: Path = typer.Option(_default_raw_dir(), help="Directory containing raw course JSON files."),
    taxonomy_path: Path = typer.Option(Path("taxonomies/skills.json"), help="Skill taxonomy path."),
    output: Path = typer.Option(Path("reports/skill_normalization_report.json"), help="Normalization report output path."),
) -> None:
    raw_courses, issues = validate_raw_course_dir(raw_dir)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        typer.echo(json.dumps({"errors": [issue.__dict__ for issue in errors]}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)
    taxonomy = ensure_taxonomy(raw_dir, taxonomy_path)
    report = normalization_report(raw_courses, SkillResolver(taxonomy))
    write_json(output, report)
    typer.echo(json.dumps({"report_path": str(output), **report}, ensure_ascii=False, indent=2))


@app.command("build-canonical-courses")
def build_canonical_courses(
    raw_dir: Path = typer.Option(_default_raw_dir(), help="Directory containing raw course JSON files."),
    taxonomy_path: Path = typer.Option(Path("taxonomies/skills.json"), help="Skill taxonomy path."),
    canonical_dir: Path = typer.Option(Path("canonical_courses"), help="Canonical course output directory."),
    quality_output: Path = typer.Option(Path("quality/course_quality.jsonl"), help="Quality snapshot JSONL output."),
) -> None:
    canonical_courses, quality_snapshots, issues = build_catalog(raw_dir, taxonomy_path)
    errors = [issue.__dict__ for issue in issues if issue.severity == "error"]
    warnings = [issue.__dict__ for issue in issues if issue.severity == "warning"]
    if errors:
        typer.echo(json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)
    write_catalog(canonical_courses, quality_snapshots, canonical_dir, quality_output)
    typer.echo(
        json.dumps(
            {
                "canonical_course_count": len(canonical_courses),
                "quality_snapshot_count": len(quality_snapshots),
                "canonical_dir": str(canonical_dir),
                "quality_output": str(quality_output),
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("rank-slot-options")
def rank_slot_options_command(
    slot_id: str = typer.Argument(..., help="Program slot id to rank."),
    program_path: Path = typer.Option(Path("programs/data_science_mvp.json"), help="Program JSON path."),
    raw_dir: Path = typer.Option(_default_raw_dir(), help="Directory containing raw course JSON files."),
    taxonomy_path: Path = typer.Option(Path("taxonomies/skills.json"), help="Skill taxonomy path."),
    canonical_dir: Path = typer.Option(Path("canonical_courses"), help="Canonical course directory."),
    quality_path: Path = typer.Option(Path("quality/course_quality.jsonl"), help="Quality snapshot JSONL path."),
    selected_course_ids: list[str] = typer.Option(None, "--selected-course-id", help="Already selected course ids."),
) -> None:
    courses, quality_snapshots, issues = resolve_catalog(raw_dir, taxonomy_path, canonical_dir, quality_path)
    errors = [issue.__dict__ for issue in issues if issue.severity == "error"]
    if errors:
        typer.echo(json.dumps({"errors": errors}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)
    program = load_program(program_path)
    ranked = rank_slot_options(program, slot_id, courses, quality_snapshots, selected_course_ids or [])
    typer.echo(json.dumps([item.model_dump(mode="json") for item in ranked], ensure_ascii=False, indent=2))


@app.command("rank-track-courses")
def rank_track_courses_command(
    program_path: Path = typer.Option(Path("programs/data_science_mvp.json"), help="Program JSON path."),
    raw_dir: Path = typer.Option(_default_raw_dir(), help="Directory containing raw course JSON files."),
    taxonomy_path: Path = typer.Option(Path("taxonomies/skills.json"), help="Skill taxonomy path."),
    canonical_dir: Path = typer.Option(Path("canonical_courses"), help="Canonical course directory."),
    quality_path: Path = typer.Option(Path("quality/course_quality.jsonl"), help="Quality snapshot JSONL path."),
) -> None:
    courses, _, issues = resolve_catalog(raw_dir, taxonomy_path, canonical_dir, quality_path)
    errors = [issue.__dict__ for issue in issues if issue.severity == "error"]
    if errors:
        typer.echo(json.dumps({"errors": errors}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)
    program = load_program(program_path)
    ranked = rank_track_courses(program, courses)
    typer.echo(json.dumps([item.model_dump(mode="json") for item in ranked], ensure_ascii=False, indent=2))


@app.command("build-roadmap")
def build_roadmap_command(
    program_path: Path = typer.Option(Path("programs/data_science_mvp.json"), help="Program JSON path."),
    raw_dir: Path = typer.Option(_default_raw_dir(), help="Directory containing raw course JSON files."),
    taxonomy_path: Path = typer.Option(Path("taxonomies/skills.json"), help="Skill taxonomy path."),
    canonical_dir: Path = typer.Option(Path("canonical_courses"), help="Canonical course directory."),
    quality_path: Path = typer.Option(Path("quality/course_quality.jsonl"), help="Quality snapshot JSONL path."),
    output: Path = typer.Option(Path("reports/roadmap_result.json"), help="Roadmap result output file."),
) -> None:
    courses, quality_snapshots, issues = resolve_catalog(raw_dir, taxonomy_path, canonical_dir, quality_path)
    errors = [issue.__dict__ for issue in issues if issue.severity == "error"]
    if errors:
        typer.echo(json.dumps({"errors": errors}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)
    program = load_program(program_path)
    roadmap = build_roadmap(program, courses, quality_snapshots)
    write_json(output, roadmap.model_dump(mode="json"))
    typer.echo(json.dumps({"output": str(output), **roadmap.model_dump(mode="json")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
