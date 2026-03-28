from __future__ import annotations

from graphlib import TopologicalSorter
from pathlib import Path

from roadmaps_mvp.io import iter_raw_course_paths, load_json, load_model, read_jsonl, write_jsonl, write_model
from roadmaps_mvp.models import (
    CanonicalCourse,
    CertificationProgram,
    IntraCourseRelation,
    InputSkillRef,
    OutputSkillRef,
    ProgramSkillLevel,
    ProgramSlot,
    QualitySnapshot,
    RawCourse,
    RoadmapResult,
    SlotSelection,
)
from roadmaps_mvp.normalize import ResolvedSkill, SkillResolver, ensure_taxonomy, load_taxonomy
from roadmaps_mvp.semester_planner import build_semester_roadmap
from roadmaps_mvp.scoring import build_skill_bank, rank_course_option, target_coverage
from roadmaps_mvp.tracks import match_courses_to_track
from roadmaps_mvp.validate import ValidationIssue, validate_raw_course_dir


def course_id_from_path(path: Path) -> str:
    return path.stem


def quality_ref_for(course_id: str) -> str:
    return f"course:{course_id}"


def quality_score_placeholder(raw_course: RawCourse) -> float:
    confidence_weight = {"high": 1.0, "medium": 0.75, "low": 0.5}[raw_course.confidence]
    source = raw_course.source_coverage
    source_score = 0.0
    if source.syllabus_used:
        source_score += 0.3
    source_score += min(source.lab_works_used_count, 10) / 25
    source_score += min(source.lectures_used_count, 10) / 25
    source_score += min(source.extra_materials_used_count, 10) / 25
    if source.single_document_used:
        source_score *= 0.9
    return round(min(1.0, 0.6 * confidence_weight + 0.4 * min(1.0, source_score)), 4)


def build_quality_snapshot(raw_course: RawCourse, course_id: str, raw_path: Path) -> QualitySnapshot:
    return QualitySnapshot(
        entity_id=quality_ref_for(course_id),
        entity_type="course",
        raw_source_file=str(raw_path),
        extraction_confidence=raw_course.confidence,
        source_coverage=raw_course.source_coverage,
        review_status="placeholder",
        evidence_count=len(raw_course.evidence),
        quality_score_placeholder=quality_score_placeholder(raw_course),
    )


def _resolve_input_skill(skill, resolver: SkillResolver) -> InputSkillRef:
    resolved: ResolvedSkill = resolver.resolve(skill.skill_id)
    return InputSkillRef(
        canonical_skill_id=resolved.canonical_skill_id,
        source_skill_id=skill.skill_id,
        skill_label=resolver.canonical_label(skill.skill_id, fallback=skill.skill_label),
        min_level_required=skill.min_level_required,
        importance=skill.importance,
        source=skill.source,
        confidence=skill.confidence,
        raw_mentions=skill.raw_mentions,
        evidence=skill.evidence,
        normalization_status=resolved.normalization_status,
    )


def _resolve_output_skill(skill, resolver: SkillResolver) -> OutputSkillRef:
    resolved: ResolvedSkill = resolver.resolve(skill.skill_id)
    return OutputSkillRef(
        canonical_skill_id=resolved.canonical_skill_id,
        source_skill_id=skill.skill_id,
        skill_label=resolver.canonical_label(skill.skill_id, fallback=skill.skill_label),
        result_level=skill.result_level,
        importance=skill.importance,
        source=skill.source,
        confidence=skill.confidence,
        raw_mentions=skill.raw_mentions,
        evidence=skill.evidence,
        normalization_status=resolved.normalization_status,
    )


def build_canonical_course(raw_course: RawCourse, course_id: str, raw_path: Path, resolver: SkillResolver) -> CanonicalCourse:
    quality_ref = quality_ref_for(course_id)
    return CanonicalCourse(
        course_id=course_id,
        course_name=raw_course.course_name,
        version="v1",
        raw_source_file=str(raw_path),
        course_profile=raw_course.course_profile,
        domain_scores=raw_course.domain_scores,
        discipline_tags=raw_course.discipline_tags,
        topic_tags=raw_course.topic_tags,
        technology_tags=raw_course.technology_tags,
        learning_outcomes=raw_course.learning_outcomes,
        student_takeaway=raw_course.student_takeaway,
        input_skill_refs=[_resolve_input_skill(skill, resolver) for skill in raw_course.input_skills_normalized],
        output_skill_refs=[_resolve_output_skill(skill, resolver) for skill in raw_course.output_skills_normalized],
        practical_profile=raw_course.practical_components,
        intra_course_relations=[
            IntraCourseRelation(
                from_skill_id=relation.from_id,
                to_skill_id=relation.to_id,
                from_canonical_skill_id=resolver.resolve(relation.from_id).canonical_skill_id,
                to_canonical_skill_id=resolver.resolve(relation.to_id).canonical_skill_id,
                from_label=relation.from_label,
                to_label=relation.to_label,
                relation=relation.relation,
                reason=relation.reason,
            )
            for relation in raw_course.curricular_relations
        ],
        quality_ref=quality_ref,
    )


def build_catalog(raw_dir: Path, taxonomy_path: Path) -> tuple[dict[str, CanonicalCourse], dict[str, QualitySnapshot], list[ValidationIssue]]:
    raw_courses, issues = validate_raw_course_dir(raw_dir)
    taxonomy = ensure_taxonomy(raw_dir, taxonomy_path)
    resolver = SkillResolver(taxonomy)
    canonical_courses: dict[str, CanonicalCourse] = {}
    quality_snapshots: dict[str, QualitySnapshot] = {}
    for path in iter_raw_course_paths(raw_dir):
        raw_course = RawCourse.model_validate(load_json(path))
        course_id = course_id_from_path(path)
        canonical_courses[course_id] = build_canonical_course(raw_course, course_id, path, resolver)
        quality_snapshots[quality_ref_for(course_id)] = build_quality_snapshot(raw_course, course_id, path)
    return canonical_courses, quality_snapshots, issues


def write_catalog(
    canonical_courses: dict[str, CanonicalCourse],
    quality_snapshots: dict[str, QualitySnapshot],
    canonical_dir: Path,
    quality_path: Path,
) -> None:
    canonical_dir.mkdir(parents=True, exist_ok=True)
    for course in canonical_courses.values():
        write_model(canonical_dir / f"{course.course_id}.json", course)
    write_jsonl(quality_path, [snapshot.model_dump(mode="json") for snapshot in quality_snapshots.values()])


def load_canonical_courses(canonical_dir: Path) -> dict[str, CanonicalCourse]:
    if not canonical_dir.exists():
        return {}
    catalog: dict[str, CanonicalCourse] = {}
    for path in sorted(canonical_dir.glob("*.json")):
        course = load_model(path, CanonicalCourse)
        catalog[course.course_id] = course
    return catalog


def load_quality_snapshots(quality_path: Path) -> dict[str, QualitySnapshot]:
    snapshots: dict[str, QualitySnapshot] = {}
    for row in read_jsonl(quality_path):
        snapshot = QualitySnapshot.model_validate(row)
        snapshots[snapshot.entity_id] = snapshot
    return snapshots


def load_program(program_path: Path) -> CertificationProgram:
    return load_model(program_path, CertificationProgram)


def resolve_catalog(
    raw_dir: Path,
    taxonomy_path: Path,
    canonical_dir: Path,
    quality_path: Path,
) -> tuple[dict[str, CanonicalCourse], dict[str, QualitySnapshot], list[ValidationIssue]]:
    courses = load_canonical_courses(canonical_dir)
    quality = load_quality_snapshots(quality_path)
    if courses and quality:
        return courses, quality, []
    return build_catalog(raw_dir, taxonomy_path)


def active_target_profile(program: CertificationProgram, slot: ProgramSlot):
    if not slot.target_skill_ids:
        return program.target_skill_profile
    target_index = {item.skill_id: item for item in program.target_skill_profile}
    return [target_index[skill_id] for skill_id in slot.target_skill_ids if skill_id in target_index]


def rank_slot_options(
    program: CertificationProgram,
    slot_id: str,
    courses: dict[str, CanonicalCourse],
    quality_snapshots: dict[str, QualitySnapshot],
    selected_course_ids: list[str] | None = None,
) -> list:
    selected_course_ids = selected_course_ids or []
    slot = next(slot for slot in program.slots if slot.slot_id == slot_id)
    selected_courses = [courses[course_id] for course_id in selected_course_ids if course_id in courses]
    skill_bank = build_skill_bank(program.baseline_skill_bank, selected_courses)
    target_profile = active_target_profile(program, slot)
    ranked = []
    for course_id in slot.candidate_course_ids:
        course = courses[course_id]
        ranked.append(
            rank_course_option(
                course=course,
                quality_snapshot=quality_snapshots.get(course.quality_ref),
                selected_courses=selected_courses,
                skill_bank=skill_bank,
                target_profile=target_profile,
                target_domains=program.target_domain_scores,
            )
        )
    return sorted(ranked, key=lambda item: (-item.score, item.course_name))


def build_roadmap(
    program: CertificationProgram,
    courses: dict[str, CanonicalCourse],
    quality_snapshots: dict[str, QualitySnapshot],
) -> RoadmapResult:
    if program.roadmap_mode == "semester":
        return build_semester_roadmap(program, courses, quality_snapshots)
    return _build_slot_roadmap_legacy(program, courses, quality_snapshots)


def _build_slot_roadmap_legacy(
    program: CertificationProgram,
    courses: dict[str, CanonicalCourse],
    quality_snapshots: dict[str, QualitySnapshot],
) -> RoadmapResult:
    dependency_graph = {slot.slot_id: set(slot.prerequisite_slot_ids) for slot in program.slots}
    slot_lookup = {slot.slot_id: slot for slot in program.slots}
    ordered_slots = list(TopologicalSorter(dependency_graph).static_order())
    selected_course_ids: list[str] = []
    slot_results: list[SlotSelection] = []

    for slot_id in ordered_slots:
        slot = slot_lookup[slot_id]
        ranked_options = rank_slot_options(
            program=program,
            slot_id=slot.slot_id,
            courses=courses,
            quality_snapshots=quality_snapshots,
            selected_course_ids=selected_course_ids,
        )
        selected_for_slot: list[str] = []
        if slot.slot_type in {"required", "choose_n_of_m"} and ranked_options:
            top_n = min(slot.selection_count, len(ranked_options))
            selected_for_slot = [option.course_id for option in ranked_options[:top_n]]
        elif slot.slot_type == "elective" and ranked_options and ranked_options[0].score >= 0.2:
            selected_for_slot = [ranked_options[0].course_id]
        selected_course_ids.extend(selected_for_slot)
        slot_results.append(
            SlotSelection(
                slot_id=slot.slot_id,
                slot_type=slot.slot_type,
                selected_course_ids=selected_for_slot,
                ranked_options=ranked_options,
            )
        )

    final_courses = [courses[course_id] for course_id in selected_course_ids if course_id in courses]
    final_skill_bank = build_skill_bank(program.baseline_skill_bank, final_courses)
    final_skill_items = [
        ProgramSkillLevel(skill_id=skill_id, level=level)
        for skill_id, level in sorted(final_skill_bank.items())
    ]
    achieved = target_coverage(final_skill_bank, program.target_skill_profile)

    return RoadmapResult(
        program_id=program.program_id,
        selected_course_ids=selected_course_ids,
        slot_results=slot_results,
        final_skill_bank=final_skill_items,
        achieved_target_coverage=round(achieved, 4),
    )


def rank_track_courses(
    program: CertificationProgram,
    courses: dict[str, CanonicalCourse],
):
    if not program.tracks:
        raise ValueError("Program has no track definitions")
    track = next(track for track in program.tracks if track.track_id == program.track_id)
    return match_courses_to_track(courses, track)
