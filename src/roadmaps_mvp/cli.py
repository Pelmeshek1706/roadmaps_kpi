from __future__ import annotations

import json
from pathlib import Path

import typer

from roadmaps_mvp.io import repo_root, write_json
from roadmaps_mvp.models import AcademicTerm
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
from roadmaps_mvp.student_recommendations import (
    build_runtime_skill_resolver,
    build_student_skill_profile,
    normalize_manual_student_skills,
    recommend_electives,
)
from roadmaps_mvp.validate import validate_raw_course_dir


app = typer.Typer(no_args_is_help=True, add_completion=False)


def _default_raw_dir() -> Path:
    return repo_root()


def _collect_manual_skill_inputs(manual_skill_inputs: list[str], interactive: bool) -> list[str]:
    collected = [item for item in manual_skill_inputs if item and item.strip()]
    if not interactive:
        return collected
    while True:
        value = typer.prompt("Введите дополнительный навык или None", default="None")
        if value.strip().lower() == "none":
            break
        if value.strip():
            collected.append(value.strip())
    return collected


def _auto_skill_preview(profile) -> list[dict]:
    return [
        {
            "skill_id": skill.skill_id,
            "skill_label": skill.skill_label,
            "level": skill.level,
            "level_label": skill.level_label,
        }
        for skill in profile.automatically_extracted_base_skills
    ]


def _interactive_manual_skill_inputs(required_subject_dir: Path, elective_raw_dir: Path, profile) -> list[str]:
    resolver = build_runtime_skill_resolver(required_subject_dir, elective_raw_dir)
    typer.echo(json.dumps({"auto_detected_skills": _auto_skill_preview(profile)}, ensure_ascii=False, indent=2))
    collected: list[str] = []
    while True:
        value = typer.prompt("Введите дополнительный навык или None", default="None")
        candidate = value.strip()
        if candidate.lower() == "none":
            break
        if not candidate:
            continue
        recognized_skills, unrecognized = normalize_manual_student_skills([candidate], resolver)
        typer.echo(
            json.dumps(
                {
                    "recognized_skills": [skill.model_dump(mode="json") for skill in recognized_skills],
                    "unrecognized_inputs": unrecognized,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        collected.append(candidate)
    return collected


def _parse_term_capacities(values: list[str]) -> dict[tuple[int, int], int]:
    capacities: dict[tuple[int, int], int] = {}
    for value in values:
        course_semester, raw_capacity = value.split("=")
        course_str, semester_str = course_semester.split(":")
        capacities[(int(course_str), int(semester_str))] = int(raw_capacity)
    return capacities


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


@app.command("build-student-profile")
def build_student_profile_command(
    specialization_id: str = typer.Option(..., "--specialization-id", help="Student specialization / track id."),
    current_course: int = typer.Option(..., help="Current course number."),
    current_semester: int = typer.Option(..., help="Current semester number inside the course."),
    required_subject_dir: Path = typer.Option(..., help="Directory with required specialization subjects."),
    elective_raw_dir: Path = typer.Option(_default_raw_dir(), help="Directory with elective raw course JSON files."),
    manual_skill_inputs: list[str] = typer.Option(None, "--manual-skill", help="Additional student skill to normalize."),
    interactive_manual_skills: bool = typer.Option(False, "--interactive-manual-skills", help="Ask for manual skills until 'None'."),
    output: Path = typer.Option(Path("reports/student_skill_profile.json"), help="Output JSON path."),
) -> None:
    preview_profile = build_student_skill_profile(
        specialization_id=specialization_id,
        current_course=current_course,
        current_semester=current_semester,
        required_subject_dir=required_subject_dir,
        elective_raw_dir=elective_raw_dir,
    )
    collected_inputs = _collect_manual_skill_inputs(manual_skill_inputs or [], False)
    if interactive_manual_skills:
        collected_inputs.extend(_interactive_manual_skill_inputs(required_subject_dir, elective_raw_dir, preview_profile))
    profile = (
        preview_profile
        if not collected_inputs
        else build_student_skill_profile(
            specialization_id=specialization_id,
            current_course=current_course,
            current_semester=current_semester,
            required_subject_dir=required_subject_dir,
            manual_skill_inputs=collected_inputs,
            elective_raw_dir=elective_raw_dir,
        )
    )
    write_json(output, profile.model_dump(mode="json"))
    typer.echo(json.dumps({"output": str(output), **profile.model_dump(mode="json")}, ensure_ascii=False, indent=2))


@app.command("recommend-electives")
def recommend_electives_command(
    specialization_id: str = typer.Option(..., "--specialization-id", help="Student specialization / track id."),
    track_id: str | None = typer.Option(None, "--track-id", help="Program track id if it differs from specialization."),
    current_course: int = typer.Option(..., help="Current course number."),
    current_semester: int = typer.Option(..., help="Current semester number inside the course."),
    electives_start_course: int | None = typer.Option(None, help="Course when electives become available."),
    electives_start_semester: int | None = typer.Option(None, help="Semester when electives become available."),
    required_subject_dir: Path = typer.Option(..., help="Directory with required specialization subjects."),
    program_path: Path = typer.Option(Path("programs/data_science_mvp.json"), help="Program JSON path."),
    raw_dir: Path = typer.Option(_default_raw_dir(), help="Directory containing elective raw course JSON files."),
    taxonomy_path: Path = typer.Option(Path("taxonomies/skills.json"), help="Skill taxonomy path."),
    canonical_dir: Path = typer.Option(Path("canonical_courses"), help="Canonical course directory."),
    quality_path: Path = typer.Option(Path("quality/course_quality.jsonl"), help="Quality snapshot JSONL path."),
    manual_skill_inputs: list[str] = typer.Option(None, "--manual-skill", help="Additional student skill to normalize."),
    interactive_manual_skills: bool = typer.Option(False, "--interactive-manual-skills", help="Ask for manual skills until 'None'."),
    term_capacities: list[str] = typer.Option(
        None,
        "--term-capacity",
        help="Capacity per term in course:semester=max format, for example 2:1=3.",
    ),
    output: Path = typer.Option(Path("reports/elective_recommendations.json"), help="Output JSON path."),
) -> None:
    courses, quality_snapshots, issues = resolve_catalog(raw_dir, taxonomy_path, canonical_dir, quality_path)
    errors = [issue.__dict__ for issue in issues if issue.severity == "error"]
    if errors:
        typer.echo(json.dumps({"errors": errors}, ensure_ascii=False, indent=2))
        raise typer.Exit(code=1)
    preview_profile = build_student_skill_profile(
        specialization_id=specialization_id,
        current_course=current_course,
        current_semester=current_semester,
        required_subject_dir=required_subject_dir,
        elective_raw_dir=raw_dir,
    )
    collected_inputs = _collect_manual_skill_inputs(manual_skill_inputs or [], False)
    if interactive_manual_skills:
        collected_inputs.extend(_interactive_manual_skill_inputs(required_subject_dir, raw_dir, preview_profile))
    profile = (
        preview_profile
        if not collected_inputs
        else build_student_skill_profile(
            specialization_id=specialization_id,
            current_course=current_course,
            current_semester=current_semester,
            required_subject_dir=required_subject_dir,
            manual_skill_inputs=collected_inputs,
            elective_raw_dir=raw_dir,
        )
    )
    program = load_program(program_path)
    recommendations = recommend_electives(
        profile=profile,
        program=program,
        courses=courses,
        quality_snapshots=quality_snapshots,
        required_subject_dir=required_subject_dir,
        elective_raw_dir=raw_dir,
        term_capacities=_parse_term_capacities(term_capacities or []),
        track_id=track_id,
        electives_start_term=(
            None
            if electives_start_course is None or electives_start_semester is None
            else AcademicTerm(course=electives_start_course, semester=electives_start_semester)
        ),
    )
    write_json(output, recommendations.model_dump(mode="json"))
    typer.echo(json.dumps({"output": str(output), **recommendations.model_dump(mode="json")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
