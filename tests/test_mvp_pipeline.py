from __future__ import annotations

from pathlib import Path

from roadmaps_mvp.models import QualitySnapshot
from roadmaps_mvp.normalize import SkillResolver, build_skill_taxonomy
from roadmaps_mvp.planner import build_catalog, build_roadmap, load_program, rank_track_courses
from roadmaps_mvp.student_recommendations import build_student_skill_profile, recommend_electives
from roadmaps_mvp.validate import validate_raw_course_dir


REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAM_PATH = REPO_ROOT / "programs" / "data_science_mvp.json"
SPECIALIZATION_REQUIRED_DIR = REPO_ROOT / "student_db" / "base_subjects" / "121_ipi" / "1st_course"


def test_validate_existing_raw_courses_without_errors():
    courses, issues = validate_raw_course_dir(REPO_ROOT)
    assert len(courses) == 5
    assert [issue for issue in issues if issue.severity == "error"] == []


def test_canonical_transform_preserves_core_sections(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    canonical_courses, quality_snapshots, issues = build_catalog(REPO_ROOT, taxonomy_path)
    assert [issue for issue in issues if issue.severity == "error"] == []
    assert len(canonical_courses) == 5
    assert len(quality_snapshots) == 5

    raw_courses, _ = validate_raw_course_dir(REPO_ROOT)
    raw_by_name = {course.course_name: course for course in raw_courses}
    for canonical_course in canonical_courses.values():
        raw_course = raw_by_name[canonical_course.course_name]
        assert len(canonical_course.domain_scores) == len(raw_course.domain_scores)
        assert canonical_course.topic_tags.model_dump() == raw_course.topic_tags.model_dump()
        assert len(canonical_course.input_skill_refs) == len(raw_course.input_skills_normalized)
        assert len(canonical_course.output_skill_refs) == len(raw_course.output_skills_normalized)
        assert len(canonical_course.intra_course_relations) == len(raw_course.curricular_relations)


def test_alias_normalization_for_conflicting_skill_ids():
    raw_courses, issues = validate_raw_course_dir(REPO_ROOT)
    assert [issue for issue in issues if issue.severity == "error"] == []
    taxonomy = build_skill_taxonomy(raw_courses)
    resolver = SkillResolver(taxonomy)

    assert resolver.resolve("basic_programming").canonical_skill_id == "programming_fundamentals"
    assert resolver.resolve("programming_basics").canonical_skill_id == "programming_fundamentals"
    assert resolver.resolve("linear_algebra_basics").canonical_skill_id == "linear_algebra"
    assert resolver.resolve("data_structures_and_algorithms").canonical_skill_id == "algorithms_and_data_structures"


def test_track_matching_returns_data_science_catalog(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    canonical_courses, _, _ = build_catalog(REPO_ROOT, taxonomy_path)
    program = load_program(PROGRAM_PATH)

    matches = rank_track_courses(program, canonical_courses)

    assert len(matches) == 5
    assert {match.course_id for match in matches} == {
        "fundamentals_of_data_science",
        "data_science_technologies",
        "analysis_and_processing_of_time_series",
        "computer_vision_technologies",
        "natural_language_analysis_and_processing_nlp",
    }
    assert matches[0].affinity_score >= matches[-1].affinity_score


def test_semester_roadmap_is_grouped_by_semesters_and_respects_capacity(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    canonical_courses, quality_snapshots, _ = build_catalog(REPO_ROOT, taxonomy_path)
    program = load_program(PROGRAM_PATH)

    roadmap = build_roadmap(program, canonical_courses, quality_snapshots)

    assert roadmap.selected_track_id == "data_science"
    assert roadmap.semester_plans
    assert len(roadmap.semester_plans) <= 4
    assert all(len(plan.selected_course_ids) <= 3 for plan in roadmap.semester_plans)
    assert roadmap.slot_results == []


def test_foundations_core_and_advanced_are_scheduled_in_order(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    canonical_courses, quality_snapshots, _ = build_catalog(REPO_ROOT, taxonomy_path)
    program = load_program(PROGRAM_PATH)
    offering_stage = {offering.course_id: offering.stage for offering in program.course_offerings}

    roadmap = build_roadmap(program, canonical_courses, quality_snapshots)
    course_to_semester: dict[str, int] = {}
    for plan in roadmap.semester_plans:
        for course_id in plan.selected_course_ids:
            course_to_semester[course_id] = plan.semester_index

    foundation_terms = [course_to_semester[course_id] for course_id, stage in offering_stage.items() if stage == "foundation" and course_id in course_to_semester]
    core_terms = [course_to_semester[course_id] for course_id, stage in offering_stage.items() if stage == "core" and course_id in course_to_semester]
    advanced_terms = [course_to_semester[course_id] for course_id, stage in offering_stage.items() if stage == "advanced" and course_id in course_to_semester]

    assert foundation_terms and core_terms and advanced_terms
    assert max(foundation_terms) < min(core_terms)
    assert max(core_terms) < min(advanced_terms)


def test_planner_uses_semester_availability_and_builds_full_track_path(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    canonical_courses, quality_snapshots, _ = build_catalog(REPO_ROOT, taxonomy_path)
    program = load_program(PROGRAM_PATH)

    roadmap = build_roadmap(program, canonical_courses, quality_snapshots)

    course_to_term = {}
    for plan in roadmap.semester_plans:
        for course_id in plan.selected_course_ids:
            course_to_term[course_id] = (plan.term.course, plan.term.semester)

    assert course_to_term["fundamentals_of_data_science"] == (1, 1)
    assert course_to_term["data_science_technologies"] == (1, 2)
    assert course_to_term["computer_vision_technologies"] == (2, 2)
    assert set(roadmap.selected_course_ids) == {
        "fundamentals_of_data_science",
        "data_science_technologies",
        "analysis_and_processing_of_time_series",
        "computer_vision_technologies",
        "natural_language_analysis_and_processing_nlp",
    }


def test_summary_reports_coverage_and_remaining_gaps(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    canonical_courses, quality_snapshots, _ = build_catalog(REPO_ROOT, taxonomy_path)
    program = load_program(PROGRAM_PATH)

    roadmap = build_roadmap(program, canonical_courses, quality_snapshots)

    assert roadmap.summary is not None
    assert roadmap.summary.total_courses == 5
    assert roadmap.summary.total_semesters <= 4
    assert roadmap.achieved_target_coverage >= 0.9
    assert roadmap.summary.achieved_target_coverage == roadmap.achieved_target_coverage
    assert len(roadmap.summary.gained_target_skills) >= 10


def test_planner_signals_infeasibility_when_baseline_is_insufficient(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    canonical_courses, quality_snapshots, _ = build_catalog(REPO_ROOT, taxonomy_path)
    program = load_program(PROGRAM_PATH)

    degraded_profile = program.student_profile.model_copy(deep=True)
    degraded_profile.baseline_skill_bank = [
        skill.model_copy(update={"level": 2}) if skill.skill_id == "python_basics" else skill
        for skill in degraded_profile.baseline_skill_bank
    ]
    degraded_program = program.model_copy(update={"student_profile": degraded_profile}, deep=True)

    roadmap = build_roadmap(degraded_program, canonical_courses, quality_snapshots)

    assert "data_science_technologies" not in roadmap.selected_course_ids
    assert roadmap.achieved_target_coverage < degraded_program.tracks[0].roadmap_policy.min_target_coverage
    assert "external_prereq_gap:data_science_technologies:python_basics:3" in roadmap.unmet_constraints


def test_quality_layer_is_separate_from_canonical_course_fields(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    canonical_courses, quality_snapshots, _ = build_catalog(REPO_ROOT, taxonomy_path)
    course = canonical_courses["natural_language_analysis_and_processing_nlp"]
    snapshot = quality_snapshots[course.quality_ref]

    dumped_course = course.model_dump(mode="json")
    assert "source_coverage" not in dumped_course
    assert "confidence" not in dumped_course
    assert isinstance(snapshot, QualitySnapshot)


def test_specialization_required_courses_validate_with_course_and_semester_metadata():
    courses, issues = validate_raw_course_dir(SPECIALIZATION_REQUIRED_DIR)

    assert len(courses) == 10
    assert [issue for issue in issues if issue.severity == "error"] == []


def test_student_profile_collects_required_courses_and_curriculum_skills():
    profile = build_student_skill_profile(
        specialization_id="data_science",
        current_course=1,
        current_semester=2,
        required_subject_dir=SPECIALIZATION_REQUIRED_DIR,
        elective_raw_dir=REPO_ROOT,
    )

    assert len(profile.required_courses) == 10
    assert len([item for item in profile.required_courses if item.status == "completed"]) == 5
    assert len([item for item in profile.required_courses if item.status == "in_progress"]) == 5
    assert len([item for item in profile.required_courses if item.status == "planned"]) == 0
    assert any(skill.skill_id == "c_programming" for skill in profile.automatically_extracted_base_skills)
    assert any(skill.skill_id == "matrix_algebra" for skill in profile.current_curriculum_skills)
    assert profile.user_skills == []


def test_manual_student_skills_are_normalized_and_default_to_medium_level():
    profile = build_student_skill_profile(
        specialization_id="data_science",
        current_course=1,
        current_semester=2,
        required_subject_dir=SPECIALIZATION_REQUIRED_DIR,
        elective_raw_dir=REPO_ROOT,
        manual_skill_inputs=["basic_programming", "Python Basics", "totally unknown skill"],
    )
    skill_index = {skill.skill_id: skill for skill in profile.user_skills}

    assert skill_index["programming_fundamentals"].level == 2
    assert skill_index["python_basics"].level == 2
    assert "basic_programming" in skill_index["programming_fundamentals"].raw_inputs
    assert "Python Basics" in skill_index["python_basics"].raw_inputs
    assert profile.unrecognized_user_skill_inputs == ["totally unknown skill"]


def test_recommend_electives_respects_term_capacity_and_offerings(tmp_path: Path):
    taxonomy_path = tmp_path / "skills.json"
    canonical_courses, quality_snapshots, _ = build_catalog(REPO_ROOT, taxonomy_path)
    program = load_program(PROGRAM_PATH)
    profile = build_student_skill_profile(
        specialization_id="data_science",
        current_course=1,
        current_semester=2,
        required_subject_dir=SPECIALIZATION_REQUIRED_DIR,
        elective_raw_dir=REPO_ROOT,
        manual_skill_inputs=["Python Basics"],
    )

    recommendations = recommend_electives(
        profile=profile,
        program=program,
        courses=canonical_courses,
        quality_snapshots=quality_snapshots,
        required_subject_dir=SPECIALIZATION_REQUIRED_DIR,
        elective_raw_dir=REPO_ROOT,
        term_capacities={(1, 2): 1, (2, 1): 2, (2, 2): 1},
    )

    offering_terms = {
        offering.course_id: {(term.course, term.semester) for term in offering.available_terms}
        for offering in program.course_offerings
    }
    selected_course_ids: set[str] = set()

    assert [plan.max_electives for plan in recommendations.term_recommendations] == [1, 2, 1]
    assert recommendations.term_recommendations[0].term.model_dump(mode="json") == {"course": 1, "semester": 2}
    for plan in recommendations.term_recommendations:
        assert len(plan.recommended_course_ids) <= plan.max_electives
        for course_id in plan.recommended_course_ids:
            assert (plan.term.course, plan.term.semester) in offering_terms[course_id]
            assert course_id not in selected_course_ids
            selected_course_ids.add(course_id)

    assert selected_course_ids
