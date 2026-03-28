from __future__ import annotations

from pathlib import Path

import pytest

from roadmaps_mvp.models import AcademicTerm
from roadmaps_mvp.planner import build_catalog, load_program
from roadmaps_mvp.student_recommendations import (
    build_runtime_skill_resolver,
    build_student_skill_profile,
    recommend_electives,
    run_manual_skill_input_session,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAM_PATH = REPO_ROOT / "programs" / "data_science_mvp.json"
REQUIRED_SUBJECT_DIR = REPO_ROOT / "student_db" / "base_subjects" / "121_ipi"


def _term_result(result, course: int, semester: int):
    for term_result in result.term_recommendations:
        if term_result.term.course == course and term_result.term.semester == semester:
            return term_result
    raise AssertionError(f"Missing term result for {course}:{semester}")


def _candidate_score(term_result, course_id: str) -> float:
    for candidate in term_result.candidate_courses:
        if candidate.course_id == course_id:
            return candidate.score
    raise AssertionError(f"Missing candidate {course_id}")


def test_student_skill_profile_contains_curriculum_and_combined_skills():
    profile = build_student_skill_profile(
        specialization_id="data_science",
        current_course=1,
        current_semester=1,
        required_subject_dir=REQUIRED_SUBJECT_DIR,
        elective_raw_dir=REPO_ROOT,
    )

    assert profile.specialization_id == "data_science"
    assert profile.current_course == 1
    assert profile.current_semester == 1
    assert len(profile.required_courses) == 10
    assert {course.status for course in profile.required_courses} == {"in_progress", "planned"}
    assert profile.automatically_extracted_base_skills
    assert profile.current_curriculum_skills
    assert profile.planned_curriculum_skills
    assert profile.user_skills == []
    assert {skill.skill_id for skill in profile.combined_skill_profile} == {
        skill.skill_id for skill in profile.automatically_extracted_base_skills
    }


def test_student_skill_profile_rejects_out_of_range_term():
    with pytest.raises(ValueError):
        build_student_skill_profile(
            specialization_id="data_science",
            current_course=5,
            current_semester=1,
            required_subject_dir=REQUIRED_SUBJECT_DIR,
            elective_raw_dir=REPO_ROOT,
        )


def test_manual_skills_are_normalized_default_to_medium_and_track_unknown_inputs():
    profile = build_student_skill_profile(
        specialization_id="data_science",
        current_course=1,
        current_semester=1,
        required_subject_dir=REQUIRED_SUBJECT_DIR,
        manual_skill_inputs=["programming basics", "linear algebra basics", "mystery skill"],
        elective_raw_dir=REPO_ROOT,
    )

    user_skills = {skill.skill_id: skill for skill in profile.user_skills}
    assert set(user_skills) == {"linear_algebra", "programming_fundamentals"}
    assert all(skill.level == 2 for skill in user_skills.values())
    assert all(skill.level_label == "medium" for skill in user_skills.values())
    assert profile.unrecognized_user_skill_inputs == ["mystery skill"]
    assert "programming_fundamentals" in {skill.skill_id for skill in profile.combined_skill_profile}


def test_manual_skill_session_stops_on_none():
    auto_profile = build_student_skill_profile(
        specialization_id="data_science",
        current_course=1,
        current_semester=1,
        required_subject_dir=REQUIRED_SUBJECT_DIR,
        elective_raw_dir=REPO_ROOT,
    )
    resolver = build_runtime_skill_resolver(REQUIRED_SUBJECT_DIR, REPO_ROOT)

    session = run_manual_skill_input_session(
        auto_skills=auto_profile.automatically_extracted_base_skills,
        manual_skill_rounds=[["programming basics"], ["None", "linear algebra basics"]],
        resolver=resolver,
    )

    assert len(session.rounds) == 2
    assert session.rounds[1].stop_requested is True
    assert {skill.skill_id for skill in session.collected_user_skills} == {"programming_fundamentals"}


def test_recommendations_respect_external_capacity_and_elective_start_term(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    courses, quality_snapshots, _ = build_catalog(REPO_ROOT, taxonomy_path)
    program = load_program(PROGRAM_PATH)
    profile = build_student_skill_profile(
        specialization_id="data_science",
        current_course=1,
        current_semester=1,
        required_subject_dir=REQUIRED_SUBJECT_DIR,
        elective_raw_dir=REPO_ROOT,
    )

    result = recommend_electives(
        profile=profile,
        program=program,
        courses=courses,
        quality_snapshots=quality_snapshots,
        required_subject_dir=REQUIRED_SUBJECT_DIR,
        elective_raw_dir=REPO_ROOT,
        term_capacities={(1, 1): 2, (1, 2): 1},
        electives_start_term=AcademicTerm(course=1, semester=2),
    )

    first_term = _term_result(result, 1, 1)
    second_term = _term_result(result, 1, 2)
    assert first_term.recommended_course_ids == []
    assert first_term.candidate_courses == []
    assert first_term.blocked_course_reasons
    assert len(second_term.recommended_course_ids) <= 1
    assert second_term.max_electives == 1


def test_recommendations_change_when_skill_profile_changes(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    courses, quality_snapshots, _ = build_catalog(REPO_ROOT, taxonomy_path)
    program = load_program(PROGRAM_PATH)

    base_profile = build_student_skill_profile(
        specialization_id="data_science",
        current_course=1,
        current_semester=1,
        required_subject_dir=REQUIRED_SUBJECT_DIR,
        elective_raw_dir=REPO_ROOT,
    )
    manual_profile = build_student_skill_profile(
        specialization_id="data_science",
        current_course=1,
        current_semester=1,
        required_subject_dir=REQUIRED_SUBJECT_DIR,
        manual_skill_inputs=["text vectorization", "lemmatization"],
        elective_raw_dir=REPO_ROOT,
    )

    base_result = recommend_electives(
        profile=base_profile,
        program=program,
        courses=courses,
        quality_snapshots=quality_snapshots,
        required_subject_dir=REQUIRED_SUBJECT_DIR,
        elective_raw_dir=REPO_ROOT,
        term_capacities={(1, 1): 0, (1, 2): 0, (2, 1): 0},
        electives_start_term=AcademicTerm(course=2, semester=1),
    )
    manual_result = recommend_electives(
        profile=manual_profile,
        program=program,
        courses=courses,
        quality_snapshots=quality_snapshots,
        required_subject_dir=REQUIRED_SUBJECT_DIR,
        elective_raw_dir=REPO_ROOT,
        term_capacities={(1, 1): 0, (1, 2): 0, (2, 1): 0},
        electives_start_term=AcademicTerm(course=2, semester=1),
    )

    base_term = _term_result(base_result, 2, 1)
    manual_term = _term_result(manual_result, 2, 1)
    assert _candidate_score(
        manual_term,
        "natural_language_analysis_and_processing_nlp",
    ) < _candidate_score(
        base_term,
        "natural_language_analysis_and_processing_nlp",
    )
