from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from roadmaps_mvp.io import iter_raw_course_paths, load_json
from roadmaps_mvp.models import RawCourse


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    path: str


def validate_raw_course_dict(payload: dict, path: str) -> tuple[RawCourse | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    schema = RawCourse.model_json_schema()
    schema_errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=str)
    for error in schema_errors:
        issues.append(
            ValidationIssue(
                severity="error",
                code="schema_validation_failed",
                message=error.message,
                path=path,
            )
        )
    if schema_errors:
        return None, issues

    try:
        course = RawCourse.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="pydantic_validation_failed",
                    message=error["msg"],
                    path=path,
                )
            )
        return None, issues

    known_skills = {skill.skill_id for skill in course.input_skills_normalized} | {
        skill.skill_id for skill in course.output_skills_normalized
    }
    for relation in course.curricular_relations:
        if relation.from_id not in known_skills:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="missing_relation_from_skill",
                    message=f"Relation references missing from_id={relation.from_id}",
                    path=path,
                )
            )
        if relation.to_id not in known_skills:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="missing_relation_to_skill",
                    message=f"Relation references missing to_id={relation.to_id}",
                    path=path,
                )
            )

    input_duplicates = _duplicates(skill.skill_id for skill in course.input_skills_normalized)
    output_duplicates = _duplicates(skill.skill_id for skill in course.output_skills_normalized)
    for duplicate_id in input_duplicates:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="duplicate_input_skill",
                message=f"Duplicate input skill_id={duplicate_id}",
                path=path,
            )
        )
    for duplicate_id in output_duplicates:
        issues.append(
            ValidationIssue(
                severity="warning",
                code="duplicate_output_skill",
                message=f"Duplicate output skill_id={duplicate_id}",
                path=path,
            )
        )

    return course, issues


def validate_raw_course_file(path: Path) -> tuple[RawCourse | None, list[ValidationIssue]]:
    return validate_raw_course_dict(load_json(path), str(path))


def validate_raw_course_dir(raw_dir: Path) -> tuple[list[RawCourse], list[ValidationIssue]]:
    courses: list[RawCourse] = []
    issues: list[ValidationIssue] = []
    for path in iter_raw_course_paths(raw_dir):
        course, course_issues = validate_raw_course_file(path)
        issues.extend(course_issues)
        if course is not None:
            courses.append(course)
    return courses, issues


def _duplicates(items) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    return sorted(duplicates)
