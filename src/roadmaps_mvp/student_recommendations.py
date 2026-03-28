from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from roadmaps_mvp.io import load_json
from roadmaps_mvp.models import (
    AcademicTerm,
    CanonicalCourse,
    CertificationProgram,
    ElectiveRecommendationResult,
    ElectiveTermRecommendation,
    ManualSkillInputRound,
    ManualSkillInputSession,
    ManualSkillRecognition,
    ProgramSkillLevel,
    QualitySnapshot,
    RankedCourseOption,
    RawCourse,
    StudentRequiredCourse,
    StudentSkill,
    StudentSkillProfile,
    TargetSkillGain,
    TrackDefinition,
)
from roadmaps_mvp.normalize import SkillResolver, build_skill_taxonomy
from roadmaps_mvp.scoring import (
    clamp,
    domain_alignment,
    evidence_score,
    output_weight,
    overlap_penalty,
    prerequisite_fit,
    target_skill_gain,
)
from roadmaps_mvp.semester_planner import is_offered_this_term
from roadmaps_mvp.tracks import match_courses_to_track


DEFAULT_MANUAL_SKILL_LEVEL = 2
MAX_COURSE_NUMBER = 4
LEVEL_LABELS = {0: "none", 1: "basic", 2: "medium", 3: "advanced", 4: "expert"}


@dataclass(frozen=True)
class CurriculumCourse:
    course_id: str
    raw_path: Path
    raw_course: RawCourse
    term: AcademicTerm
    status: str


def _level_label(level: int) -> str:
    return LEVEL_LABELS[max(0, min(4, level))]


def _term_index(term: AcademicTerm) -> int:
    return (term.course - 1) * 2 + term.semester


def _validate_current_term(course: int, semester: int) -> AcademicTerm:
    if course < 1 or course > MAX_COURSE_NUMBER:
        raise ValueError(f"course must be between 1 and {MAX_COURSE_NUMBER}")
    if semester not in {1, 2}:
        raise ValueError("semester must be 1 or 2")
    return AcademicTerm(course=course, semester=semester)


def _recursive_raw_course_paths(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def _load_raw_courses(paths: list[Path]) -> list[RawCourse]:
    return [RawCourse.model_validate(load_json(path)) for path in paths]


def build_runtime_skill_resolver(required_subject_dir: Path, elective_raw_dir: Path | None = None) -> SkillResolver:
    raw_courses = _load_raw_courses(_recursive_raw_course_paths(required_subject_dir))
    if elective_raw_dir is not None:
        raw_courses.extend(
            RawCourse.model_validate(load_json(path))
            for path in sorted(elective_raw_dir.glob("*.json"))
            if path.is_file()
        )
    return SkillResolver(build_skill_taxonomy(raw_courses))


def _curriculum_status(term: AcademicTerm, current_term: AcademicTerm) -> str:
    current_index = _term_index(current_term)
    term_index = _term_index(term)
    if term_index < current_index:
        return "completed"
    if term_index == current_index:
        return "in_progress"
    return "planned"


def load_specialization_curriculum(required_subject_dir: Path, current_term: AcademicTerm) -> list[CurriculumCourse]:
    curriculum: list[CurriculumCourse] = []
    for path in _recursive_raw_course_paths(required_subject_dir):
        raw_course = RawCourse.model_validate(load_json(path))
        if raw_course.discipline_kind != "mandatory":
            continue
        if raw_course.course is None or raw_course.semester is None:
            raise ValueError(f"Required course {path} must define course and semester")
        term = _validate_current_term(raw_course.course, raw_course.semester)
        curriculum.append(
            CurriculumCourse(
                course_id=path.stem,
                raw_path=path,
                raw_course=raw_course,
                term=term,
                status=_curriculum_status(term, current_term),
            )
        )
    return sorted(curriculum, key=lambda item: (_term_index(item.term), item.course_id))


def _aggregate_skills_from_curriculum_items(
    curriculum_items: list[CurriculumCourse],
    resolver: SkillResolver,
    source: str,
) -> list[StudentSkill]:
    aggregated: dict[str, StudentSkill] = {}
    for item in curriculum_items:
        for skill in item.raw_course.output_skills_normalized:
            resolved = resolver.resolve(skill.skill_id)
            canonical_skill_id = resolved.canonical_skill_id
            candidate = StudentSkill(
                skill_id=canonical_skill_id,
                skill_label=resolver.canonical_label(skill.skill_id, fallback=skill.skill_label),
                level=skill.result_level,
                level_label=_level_label(skill.result_level),
                source=source,
                normalization_status=resolved.normalization_status,
                taxonomy_category=resolver.category_for(skill.skill_id),
                evidence_course_ids=[item.course_id],
                raw_inputs=[],
            )
            current = aggregated.get(canonical_skill_id)
            if current is None:
                aggregated[canonical_skill_id] = candidate
                continue
            merged_level = max(current.level, candidate.level)
            aggregated[canonical_skill_id] = current.model_copy(
                update={
                    "level": merged_level,
                    "level_label": _level_label(merged_level),
                    "evidence_course_ids": sorted(set([*current.evidence_course_ids, item.course_id])),
                }
            )
    return sorted(aggregated.values(), key=lambda item: item.skill_id)


def _aggregate_curriculum_skills(
    curriculum: list[CurriculumCourse],
    resolver: SkillResolver,
    included_statuses: set[str],
    source: str,
) -> list[StudentSkill]:
    return _aggregate_skills_from_curriculum_items(
        [item for item in curriculum if item.status in included_statuses],
        resolver,
        source=source,
    )


def _manual_skill_from_input(
    raw_input: str,
    resolver: SkillResolver,
    default_level: int = DEFAULT_MANUAL_SKILL_LEVEL,
) -> tuple[StudentSkill | None, ManualSkillRecognition]:
    candidate = raw_input.strip()
    resolved = resolver.resolve(candidate)
    if resolved.normalization_status == "unmapped":
        return (
            None,
            ManualSkillRecognition(
                raw_input=candidate,
                normalized_skill=None,
                recognized=False,
            ),
        )

    skill = StudentSkill(
        skill_id=resolved.canonical_skill_id,
        skill_label=resolver.canonical_label(candidate),
        level=default_level,
        level_label=_level_label(default_level),
        source="student_manual",
        normalization_status=resolved.normalization_status,
        taxonomy_category=resolver.category_for(candidate),
        evidence_course_ids=[],
        raw_inputs=[candidate],
    )
    return (
        skill,
        ManualSkillRecognition(
            raw_input=candidate,
            normalized_skill=skill,
            recognized=True,
        ),
    )


def normalize_manual_student_skills(
    manual_skill_inputs: list[str],
    resolver: SkillResolver,
    default_level: int = DEFAULT_MANUAL_SKILL_LEVEL,
) -> tuple[list[StudentSkill], list[str]]:
    aggregated: dict[str, StudentSkill] = {}
    unrecognized: list[str] = []
    for raw_input in manual_skill_inputs:
        candidate = raw_input.strip()
        if not candidate or candidate.lower() == "none":
            continue
        normalized_skill, recognition = _manual_skill_from_input(candidate, resolver, default_level=default_level)
        if not recognition.recognized:
            unrecognized.append(candidate)
            continue
        assert normalized_skill is not None
        existing = aggregated.get(normalized_skill.skill_id)
        if existing is None:
            aggregated[normalized_skill.skill_id] = normalized_skill
            continue
        merged_level = max(existing.level, normalized_skill.level)
        aggregated[normalized_skill.skill_id] = existing.model_copy(
            update={
                "level": merged_level,
                "level_label": _level_label(merged_level),
                "raw_inputs": sorted(set([*existing.raw_inputs, *normalized_skill.raw_inputs])),
            }
        )
    return sorted(aggregated.values(), key=lambda item: item.skill_id), sorted(set(unrecognized))


def run_manual_skill_input_session(
    auto_skills: list[StudentSkill],
    manual_skill_rounds: list[list[str]],
    resolver: SkillResolver,
    default_level: int = DEFAULT_MANUAL_SKILL_LEVEL,
) -> ManualSkillInputSession:
    shown_auto_skills = [skill.skill_id for skill in auto_skills]
    collected_user_skills: list[StudentSkill] = []
    unrecognized_inputs: list[str] = []
    rounds: list[ManualSkillInputRound] = []

    for raw_round in manual_skill_rounds:
        submitted_inputs: list[str] = []
        recognitions: list[ManualSkillRecognition] = []
        stop_requested = False
        for raw_input in raw_round:
            candidate = raw_input.strip()
            if not candidate:
                continue
            if candidate.lower() == "none":
                stop_requested = True
                break
            submitted_inputs.append(candidate)
            normalized_skill, recognition = _manual_skill_from_input(candidate, resolver, default_level=default_level)
            recognitions.append(recognition)
            if normalized_skill is not None:
                collected_user_skills.append(normalized_skill)
            else:
                unrecognized_inputs.append(candidate)

        rounds.append(
            ManualSkillInputRound(
                shown_auto_skills=shown_auto_skills,
                submitted_inputs=submitted_inputs,
                recognized_skills=recognitions,
                stop_requested=stop_requested,
            )
        )
        if stop_requested:
            break

    collected_user_skills = _merge_skill_sets([collected_user_skills])
    return ManualSkillInputSession(
        auto_skills_shown=shown_auto_skills,
        rounds=rounds,
        collected_user_skills=collected_user_skills,
        unrecognized_inputs=sorted(set(unrecognized_inputs)),
    )


def _merged_source(left: str, right: str) -> str:
    priorities = {"student_manual": 3, "curriculum_current": 2, "curriculum_program": 1}
    return left if priorities.get(left, 0) >= priorities.get(right, 0) else right


def _merge_skill_sets(skill_sets: list[list[StudentSkill]]) -> list[StudentSkill]:
    merged: dict[str, StudentSkill] = {}
    for skills in skill_sets:
        for skill in skills:
            existing = merged.get(skill.skill_id)
            if existing is None:
                merged[skill.skill_id] = skill
                continue
            merged_level = max(existing.level, skill.level)
            merged[skill.skill_id] = existing.model_copy(
                update={
                    "level": merged_level,
                    "level_label": _level_label(merged_level),
                    "source": _merged_source(existing.source, skill.source),
                    "evidence_course_ids": sorted(set([*existing.evidence_course_ids, *skill.evidence_course_ids])),
                    "raw_inputs": sorted(set([*existing.raw_inputs, *skill.raw_inputs])),
                }
            )
    return sorted(merged.values(), key=lambda item: item.skill_id)


def _skills_to_bank(skills: list[StudentSkill]) -> dict[str, int]:
    return {skill.skill_id: skill.level for skill in skills}


def build_student_skill_profile(
    specialization_id: str,
    current_course: int,
    current_semester: int,
    required_subject_dir: Path,
    manual_skill_inputs: list[str] | None = None,
    elective_raw_dir: Path | None = None,
    manual_skill_rounds: list[list[str]] | None = None,
) -> StudentSkillProfile:
    current_term = _validate_current_term(current_course, current_semester)
    curriculum = load_specialization_curriculum(required_subject_dir, current_term)
    resolver = build_runtime_skill_resolver(required_subject_dir, elective_raw_dir=elective_raw_dir)

    required_courses = [
        StudentRequiredCourse(
            course_id=item.course_id,
            course_name=item.raw_course.course_name,
            course=item.term.course,
            semester=item.term.semester,
            status=item.status,
            raw_source_file=str(item.raw_path),
        )
        for item in curriculum
    ]
    automatically_extracted_base_skills = _aggregate_curriculum_skills(
        curriculum,
        resolver,
        included_statuses={"completed", "in_progress", "planned"},
        source="curriculum_program",
    )
    current_curriculum_skills = _aggregate_curriculum_skills(
        curriculum,
        resolver,
        included_statuses={"completed", "in_progress"},
        source="curriculum_current",
    )
    planned_curriculum_skills = _aggregate_curriculum_skills(
        curriculum,
        resolver,
        included_statuses={"planned"},
        source="curriculum_program",
    )

    user_skill_sets: list[list[StudentSkill]] = []
    unrecognized_inputs: list[str] = []
    if manual_skill_rounds:
        session = run_manual_skill_input_session(
            automatically_extracted_base_skills,
            manual_skill_rounds,
            resolver,
        )
        user_skill_sets.append(session.collected_user_skills)
        unrecognized_inputs.extend(session.unrecognized_inputs)
    if manual_skill_inputs:
        normalized_user_skills, unrecognized = normalize_manual_student_skills(manual_skill_inputs, resolver)
        user_skill_sets.append(normalized_user_skills)
        unrecognized_inputs.extend(unrecognized)

    user_skills = _merge_skill_sets(user_skill_sets) if user_skill_sets else []
    combined_skill_profile = _merge_skill_sets([automatically_extracted_base_skills, user_skills])
    return StudentSkillProfile(
        specialization_id=specialization_id,
        current_course=current_course,
        current_semester=current_semester,
        required_courses=required_courses,
        automatically_extracted_base_skills=automatically_extracted_base_skills,
        current_curriculum_skills=current_curriculum_skills,
        planned_curriculum_skills=planned_curriculum_skills,
        user_skills=user_skills,
        combined_skill_profile=combined_skill_profile,
        unrecognized_user_skill_inputs=sorted(set(unrecognized_inputs)),
    )


def _merge_course_outputs(skill_bank: dict[str, int], courses: list[CanonicalCourse]) -> dict[str, int]:
    updated = dict(skill_bank)
    for course in courses:
        for skill in course.output_skill_refs:
            updated[skill.canonical_skill_id] = max(updated.get(skill.canonical_skill_id, 0), skill.result_level)
    return updated


def _novelty_gain(course: CanonicalCourse, skill_bank: dict[str, int]) -> float:
    weighted_total = 0.0
    weighted_gain = 0.0
    for skill in course.output_skill_refs:
        weight = output_weight(skill)
        current_level = skill_bank.get(skill.canonical_skill_id, 0)
        max_level = max(skill.result_level, 1)
        gained_levels = max(0, skill.result_level - current_level)
        weighted_total += weight
        weighted_gain += weight * (gained_levels / max_level)
    return clamp(weighted_gain / weighted_total if weighted_total else 0.0)


def _redundancy_penalty(course: CanonicalCourse, skill_bank: dict[str, int], resolver: SkillResolver) -> float:
    weighted_total = 0.0
    weighted_penalty = 0.0
    for skill in course.output_skill_refs:
        weight = output_weight(skill)
        current_level = skill_bank.get(skill.canonical_skill_id, 0)
        if current_level <= 0:
            continue
        overlap_ratio = clamp(current_level / max(skill.result_level, 1))
        category = resolver.category_for(skill.canonical_skill_id)
        if category == "foundation":
            overlap_ratio *= 0.35
        weighted_total += weight
        weighted_penalty += weight * overlap_ratio
    return clamp(weighted_penalty / weighted_total if weighted_total else 0.0)


def _developed_skill_ids(course: CanonicalCourse, skill_bank: dict[str, int]) -> list[str]:
    developed = [
        skill.canonical_skill_id
        for skill in course.output_skill_refs
        if skill.result_level > skill_bank.get(skill.canonical_skill_id, 0)
    ]
    return sorted(set(developed))


def _overlapping_skill_ids(course: CanonicalCourse, skill_bank: dict[str, int]) -> list[str]:
    overlapping = [
        skill.canonical_skill_id
        for skill in course.output_skill_refs
        if skill_bank.get(skill.canonical_skill_id, 0) >= skill.result_level
    ]
    return sorted(set(overlapping))


def _resolve_track(program: CertificationProgram, track_id: str) -> TrackDefinition:
    for track in program.tracks:
        if track.track_id == track_id:
            return track
    if not program.tracks and program.track_id == track_id:
        raise ValueError("Program without tracks is not supported for specialization recommendations")
    raise ValueError(f"Track {track_id!r} not found in program tracks")


def _rank_elective_candidate(
    course: CanonicalCourse,
    quality_snapshot: QualitySnapshot | None,
    selected_courses: list[CanonicalCourse],
    current_skill_bank: dict[str, int],
    future_curriculum_skill_bank: dict[str, int],
    track_affinity: float,
    track: TrackDefinition,
    resolver: SkillResolver,
) -> RankedCourseOption:
    prereq_score, missing_prereqs = prerequisite_fit(course, current_skill_bank)
    target_gain_score, gained_skills = target_skill_gain(course, track.target_skill_profile, current_skill_bank)
    novelty_score = _novelty_gain(course, current_skill_bank)
    current_redundancy = _redundancy_penalty(course, current_skill_bank, resolver)
    future_redundancy = _redundancy_penalty(course, future_curriculum_skill_bank, resolver)
    already_selected_overlap, overlap_ids = overlap_penalty(course, selected_courses)
    evidence = evidence_score(quality_snapshot)
    domain_score = domain_alignment(course, track.target_domain_scores)
    developed_skill_ids = _developed_skill_ids(course, current_skill_bank)
    overlapping_skill_ids = sorted(
        set(
            [
                *_overlapping_skill_ids(course, current_skill_bank),
                *_overlapping_skill_ids(course, future_curriculum_skill_bank),
            ]
        )
    )

    reason_codes: list[str] = []
    if track_affinity > 0:
        reason_codes.append("specialization_alignment")
    if developed_skill_ids:
        reason_codes.append("complements_existing_skills")
    if gained_skills:
        reason_codes.append("improves_target_skills")
    if missing_prereqs:
        reason_codes.append("has_prerequisite_gaps")
    if current_redundancy >= 0.2:
        reason_codes.append("repeats_current_material")
    if future_redundancy >= 0.2:
        reason_codes.append("overlaps_future_mandatory_program")
    if any(resolver.category_for(skill_id) == "foundation" for skill_id in overlapping_skill_ids):
        reason_codes.append("foundation_overlap_allowed")

    total = clamp(
        0.25 * track_affinity
        + 0.20 * prereq_score
        + 0.20 * target_gain_score
        + 0.15 * novelty_score
        + 0.10 * evidence
        + 0.10 * domain_score
        - 0.10 * current_redundancy
        - 0.10 * future_redundancy
        - 0.10 * already_selected_overlap
    )
    return RankedCourseOption(
        course_id=course.course_id,
        course_name=course.course_name,
        score=round(total, 4),
        track_affinity=round(track_affinity, 4),
        prerequisite_fit=round(prereq_score, 4),
        readiness_score=round(prereq_score, 4),
        target_skill_gain=round(target_gain_score, 4),
        unlock_score=round(novelty_score, 4),
        domain_alignment=round(domain_score, 4),
        phase_fit_score=0.0,
        evidence_score=round(evidence, 4),
        overlap_penalty=round(max(current_redundancy, future_redundancy, already_selected_overlap), 4),
        stage=None,
        missing_prerequisites=missing_prereqs,
        gained_target_skills=sorted(gained_skills, key=lambda item: item.skill_id),
        overlaps_with_selected=overlap_ids,
        reason_codes=reason_codes,
        developed_skill_ids=developed_skill_ids,
        overlapping_skill_ids=overlapping_skill_ids,
    )


def _term_capacity_lookup(
    current_term: AcademicTerm,
    term_capacities: dict[tuple[int, int], int] | None,
    program: CertificationProgram,
) -> list[tuple[AcademicTerm, int]]:
    if term_capacities:
        items = []
        for (course, semester), capacity in sorted(term_capacities.items()):
            term = _validate_current_term(course, semester)
            if _term_index(term) >= _term_index(current_term):
                items.append((term, capacity))
        return items

    default_capacity = program.planning_window.max_courses_per_semester if program.planning_window else 3
    return [
        (current_term.model_copy(), default_capacity),
        *[
            (AcademicTerm(course=term_course, semester=term_semester), default_capacity)
            for term_course in range(current_term.course, MAX_COURSE_NUMBER + 1)
            for term_semester in (1, 2)
            if _term_index(AcademicTerm(course=term_course, semester=term_semester)) > _term_index(current_term)
        ],
    ]


def recommend_electives(
    profile: StudentSkillProfile,
    program: CertificationProgram,
    courses: dict[str, CanonicalCourse],
    quality_snapshots: dict[str, QualitySnapshot],
    required_subject_dir: Path,
    elective_raw_dir: Path | None = None,
    term_capacities: dict[tuple[int, int], int] | None = None,
    track_id: str | None = None,
    electives_start_term: AcademicTerm | None = None,
) -> ElectiveRecommendationResult:
    current_term = _validate_current_term(profile.current_course, profile.current_semester)
    effective_track_id = track_id or profile.specialization_id
    track = _resolve_track(program, effective_track_id)
    electives_start_term = electives_start_term or current_term
    resolver = build_runtime_skill_resolver(required_subject_dir, elective_raw_dir=elective_raw_dir)
    curriculum = load_specialization_curriculum(required_subject_dir, current_term)
    matches = {item.course_id: item for item in match_courses_to_track(courses, track)}
    required_course_ids = {item.course_id for item in profile.required_courses}
    offering_map = {offering.course_id: offering for offering in program.course_offerings}
    selected_courses: list[CanonicalCourse] = []
    profile_current_skill_bank = _skills_to_bank(_merge_skill_sets([profile.current_curriculum_skills, profile.user_skills]))
    profile_planned_skill_bank = _skills_to_bank(profile.planned_curriculum_skills)
    recommendations: list[ElectiveTermRecommendation] = []

    for term, capacity in _term_capacity_lookup(current_term, term_capacities, program):
        blocked: list[str] = []
        ranked_candidates: list[RankedCourseOption] = []

        if _term_index(term) < _term_index(electives_start_term):
            blocked.extend(
                f"{course_id}:electives_locked_until:{electives_start_term.course}:{electives_start_term.semester}"
                for course_id in sorted(matches)
            )
            recommendations.append(
                ElectiveTermRecommendation(
                    term=term,
                    max_electives=capacity,
                    recommended_course_ids=[],
                    recommended_courses=[],
                    candidate_courses=[],
                    blocked_course_reasons=blocked,
                )
            )
            continue

        if _term_index(term) == _term_index(current_term):
            current_skill_bank = _merge_course_outputs(profile_current_skill_bank, selected_courses)
            future_curriculum_skill_bank = profile_planned_skill_bank
        else:
            curriculum_until_term = _aggregate_skills_from_curriculum_items(
                [item for item in curriculum if _term_index(item.term) <= _term_index(term)],
                resolver,
                source="curriculum_current",
            )
            future_curriculum_skills = _aggregate_skills_from_curriculum_items(
                [item for item in curriculum if _term_index(item.term) > _term_index(term)],
                resolver,
                source="curriculum_program",
            )
            current_skill_bank = _merge_course_outputs(
                _skills_to_bank(_merge_skill_sets([curriculum_until_term, profile.user_skills])),
                selected_courses,
            )
            future_curriculum_skill_bank = _skills_to_bank(future_curriculum_skills)

        for course_id, match in matches.items():
            if course_id in required_course_ids:
                blocked.append(f"{course_id}:required_program_course")
                continue
            if course_id in {course.course_id for course in selected_courses}:
                blocked.append(f"{course_id}:already_selected")
                continue
            offering = offering_map.get(course_id)
            if offering is None:
                blocked.append(f"{course_id}:missing_offering")
                continue
            if not is_offered_this_term(offering, term):
                blocked.append(f"{course_id}:not_offered")
                continue
            ranked_candidates.append(
                _rank_elective_candidate(
                    course=courses[course_id],
                    quality_snapshot=quality_snapshots.get(courses[course_id].quality_ref),
                    selected_courses=selected_courses,
                    current_skill_bank=current_skill_bank,
                    future_curriculum_skill_bank=future_curriculum_skill_bank,
                    track_affinity=match.affinity_score,
                    track=track,
                    resolver=resolver,
                ).model_copy(update={"stage": offering.stage})
            )

        ranked_candidates = sorted(ranked_candidates, key=lambda item: (-item.score, item.course_name))
        selected_for_term = ranked_candidates[:capacity] if capacity > 0 else []
        selected_courses.extend(courses[item.course_id] for item in selected_for_term)
        recommendations.append(
            ElectiveTermRecommendation(
                term=term,
                max_electives=capacity,
                recommended_course_ids=[item.course_id for item in selected_for_term],
                recommended_courses=selected_for_term,
                candidate_courses=ranked_candidates,
                blocked_course_reasons=sorted(blocked),
            )
        )

    return ElectiveRecommendationResult(
        specialization_id=profile.specialization_id,
        current_term=current_term,
        student_profile=profile,
        term_recommendations=recommendations,
    )


def profile_to_skill_bank(profile: StudentSkillProfile) -> list[ProgramSkillLevel]:
    return [ProgramSkillLevel(skill_id=skill.skill_id, level=skill.level) for skill in profile.combined_skill_profile]
