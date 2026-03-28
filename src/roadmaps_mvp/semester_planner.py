from __future__ import annotations

from dataclasses import dataclass

from roadmaps_mvp.models import (
    AcademicTerm,
    CanonicalCourse,
    CertificationProgram,
    CourseOffering,
    CourseStage,
    ProgramSkillLevel,
    QualitySnapshot,
    RankedCourseOption,
    RoadmapResult,
    SemesterPlan,
    StudentPlanSummary,
    StudentProfile,
    TrackCourseMatch,
    TrackDefinition,
)
from roadmaps_mvp.scoring import (
    build_skill_bank,
    clamp,
    evidence_score,
    overlap_penalty,
    prerequisite_fit,
    target_coverage,
    target_skill_gain,
)
from roadmaps_mvp.tracks import match_courses_to_track


@dataclass(frozen=True)
class SemesterPlannerContext:
    track: TrackDefinition
    student: StudentProfile
    term_sequence: list[AcademicTerm]
    course_matches: dict[str, TrackCourseMatch]
    offerings: dict[str, CourseOffering]
    completed_course_ids: set[str]
    program: CertificationProgram


def advance_term(term: AcademicTerm) -> AcademicTerm:
    if term.semester == 1:
        return AcademicTerm(course=term.course, semester=2)
    return AcademicTerm(course=term.course + 1, semester=1)


def build_term_sequence(start_term: AcademicTerm, horizon_semesters: int) -> list[AcademicTerm]:
    sequence = [start_term]
    while len(sequence) < horizon_semesters:
        sequence.append(advance_term(sequence[-1]))
    return sequence


def resolve_active_track(program: CertificationProgram) -> TrackDefinition:
    if program.tracks:
        for track in program.tracks:
            if track.track_id == program.track_id:
                return track
        raise ValueError(f"Track {program.track_id!r} not found in program.tracks")

    return TrackDefinition(
        track_id=program.track_id,
        title=program.title,
        selector_domains=program.target_domain_scores,
        selector_skill_ids=[skill.skill_id for skill in program.target_skill_profile],
        target_skill_profile=program.target_skill_profile,
        target_domain_scores=program.target_domain_scores,
        foundation_skill_ids=[],
    )


def resolve_student_profile(program: CertificationProgram) -> StudentProfile:
    if program.student_profile is not None:
        return program.student_profile
    if program.planning_window is None:
        start_term = AcademicTerm(course=1, semester=1)
    else:
        start_term = program.planning_window.start_term
    return StudentProfile(
        student_id="legacy_student",
        baseline_skill_bank=program.baseline_skill_bank,
        completed_course_ids=[],
        starting_term=start_term,
    )


def build_offering_index(program: CertificationProgram, course_ids: set[str]) -> dict[str, CourseOffering]:
    offering_map = {offering.course_id: offering for offering in program.course_offerings}
    missing = sorted(course_ids - set(offering_map))
    if missing:
        raise ValueError(f"Missing course_offerings for courses: {', '.join(missing)}")
    return offering_map


def _term_equals(left: AcademicTerm, right: AcademicTerm) -> bool:
    return left.course == right.course and left.semester == right.semester


def is_offered_this_term(offering: CourseOffering, term: AcademicTerm) -> bool:
    return any(_term_equals(candidate, term) for candidate in offering.available_terms)


def stage_gate_satisfied(
    offering: CourseOffering,
    selected_courses: list[CanonicalCourse],
    all_offerings: dict[str, CourseOffering],
) -> bool:
    selected_stages = [
        all_offerings[course.course_id].stage
        for course in selected_courses
        if course.course_id in all_offerings
    ]
    all_pool_stages = {item.stage for item in all_offerings.values()}
    foundation_selected = "foundation" in selected_stages
    core_selected = "core" in selected_stages or foundation_selected
    if offering.stage == "foundation":
        return True
    if offering.stage == "core":
        return foundation_selected or "foundation" not in all_pool_stages
    if offering.stage == "advanced":
        return core_selected or not {"foundation", "core"} & all_pool_stages
    return True


def hard_missing_prerequisites(course: CanonicalCourse, skill_bank: dict[str, int]) -> list[str]:
    missing: list[str] = []
    for skill in course.input_skill_refs:
        if skill.importance != "required":
            continue
        if skill.confidence == "low":
            continue
        if skill_bank.get(skill.canonical_skill_id, 0) < skill.min_level_required:
            missing.append(skill.canonical_skill_id)
    return sorted(set(missing))


def readiness_score(course: CanonicalCourse, skill_bank: dict[str, int]) -> float:
    prereq_score, _ = prerequisite_fit(course, skill_bank)
    return prereq_score


def track_affinity(course_id: str, matches: dict[str, TrackCourseMatch]) -> float:
    return matches[course_id].affinity_score


def unlock_score(
    candidate: CanonicalCourse,
    remaining_courses: list[CanonicalCourse],
    skill_bank: dict[str, int],
) -> float:
    if not remaining_courses:
        return 0.0

    current_bank = skill_bank
    future_bank = build_skill_bank(
        [ProgramSkillLevel(skill_id=skill_id, level=level) for skill_id, level in current_bank.items()],
        [candidate],
    )
    improvements = 0.0
    for course in remaining_courses:
        before = len(hard_missing_prerequisites(course, current_bank))
        after = len(hard_missing_prerequisites(course, future_bank))
        if before > after:
            improvements += (before - after) / before
    return clamp(improvements / len(remaining_courses)) if remaining_courses else 0.0


def phase_fit_score(stage: CourseStage, semester_index: int, total_semesters: int) -> float:
    progress = 0.0 if total_semesters <= 1 else (semester_index - 1) / (total_semesters - 1)
    if stage == "foundation":
        return clamp(1.0 - progress)
    if stage == "core":
        return clamp(1.0 - abs(progress - 0.5) * 1.5)
    return clamp(progress + 0.2)


def rank_semester_candidate(
    course: CanonicalCourse,
    quality_snapshot: QualitySnapshot | None,
    selected_courses: list[CanonicalCourse],
    skill_bank: dict[str, int],
    remaining_courses: list[CanonicalCourse],
    match: TrackCourseMatch,
    offering: CourseOffering,
    track: TrackDefinition,
    edge_bonus: float,
    edge_penalty: float,
    semester_index: int,
    total_semesters: int,
) -> RankedCourseOption:
    prereq_score, missing_prereqs = prerequisite_fit(course, skill_bank)
    readiness = readiness_score(course, skill_bank)
    target_gain_score, gained_skills = target_skill_gain(course, track.target_skill_profile, skill_bank)
    unlock = unlock_score(course, remaining_courses, skill_bank)
    evidence = evidence_score(quality_snapshot)
    overlap, overlap_ids = overlap_penalty(course, selected_courses)
    phase_fit = phase_fit_score(offering.stage, semester_index, total_semesters)
    total = clamp(
        0.35 * target_gain_score
        + 0.20 * unlock
        + 0.15 * match.affinity_score
        + 0.10 * readiness
        + 0.10 * evidence
        + 0.10 * phase_fit
        + edge_bonus
        - edge_penalty
        - 0.20 * overlap
    )
    return RankedCourseOption(
        course_id=course.course_id,
        course_name=course.course_name,
        score=round(total, 4),
        track_affinity=round(match.affinity_score, 4),
        prerequisite_fit=round(prereq_score, 4),
        readiness_score=round(readiness, 4),
        target_skill_gain=round(target_gain_score, 4),
        unlock_score=round(unlock, 4),
        domain_alignment=round(match.affinity_score, 4),
        phase_fit_score=round(phase_fit, 4),
        evidence_score=round(evidence, 4),
        overlap_penalty=round(overlap, 4),
        stage=offering.stage,
        missing_prerequisites=missing_prereqs,
        gained_target_skills=gained_skills,
        overlaps_with_selected=overlap_ids,
    )


def eligible_courses_for_term(
    term: AcademicTerm,
    semester_index: int,
    selected_courses: list[CanonicalCourse],
    skill_bank: dict[str, int],
    courses: dict[str, CanonicalCourse],
    quality_snapshots: dict[str, QualitySnapshot],
    ctx: SemesterPlannerContext,
) -> tuple[list[RankedCourseOption], list[str]]:
    selected_ids = {course.course_id for course in selected_courses} | ctx.completed_course_ids
    blocked: list[str] = []
    ranked: list[RankedCourseOption] = []
    pool_course_ids = set(ctx.course_matches)
    for course_id in sorted(pool_course_ids):
        if course_id in selected_ids:
            continue
        course = courses[course_id]
        offering = ctx.offerings[course_id]
        if not is_offered_this_term(offering, term):
            blocked.append(f"{course_id}:not_offered")
            continue
        if not stage_gate_satisfied(offering, selected_courses, ctx.offerings):
            blocked.append(f"{course_id}:stage_gate")
            continue
        edge_blocked, edge_bonus, edge_penalty = edge_status(
            course_id=course_id,
            selected_course_ids=selected_ids,
            program=ctx.program,
            max_pairwise_overlap=ctx.track.roadmap_policy.max_pairwise_overlap,
        )
        if edge_blocked:
            blocked.append(f"{course_id}:program_edge_block")
            continue
        hard_missing = hard_missing_prerequisites(course, skill_bank)
        if hard_missing:
            blocked.append(f"{course_id}:missing_prerequisites")
            continue
        remaining = [
            courses[other_id]
            for other_id in pool_course_ids
            if other_id not in selected_ids and other_id != course_id
        ]
        ranked.append(
            rank_semester_candidate(
                course=course,
                quality_snapshot=quality_snapshots.get(course.quality_ref),
                selected_courses=selected_courses,
                skill_bank=skill_bank,
                remaining_courses=remaining,
                match=ctx.course_matches[course_id],
                offering=offering,
                track=ctx.track,
                edge_bonus=edge_bonus,
                edge_penalty=edge_penalty,
                semester_index=semester_index,
                total_semesters=len(ctx.term_sequence),
            )
        )
    return sorted(ranked, key=lambda item: (-item.score, item.course_name)), blocked


def select_courses_for_term(
    ranked: list[RankedCourseOption],
    selected_courses: list[CanonicalCourse],
    courses: dict[str, CanonicalCourse],
    max_courses: int,
    min_score: float = 0.2,
) -> list[RankedCourseOption]:
    chosen: list[RankedCourseOption] = []
    chosen_ids: set[str] = set()
    while len(chosen) < max_courses:
        candidates = []
        prior_courses = selected_courses + [courses[item.course_id] for item in chosen]
        for option in ranked:
            if option.course_id in chosen_ids:
                continue
            course = courses[option.course_id]
            overlap, _ = overlap_penalty(course, prior_courses)
            adjusted = option.model_copy(update={"score": round(clamp(option.score - 0.20 * overlap), 4), "overlap_penalty": round(overlap, 4)})
            candidates.append(adjusted)
        if not candidates:
            break
        best = sorted(candidates, key=lambda item: (-item.score, item.course_name))[0]
        if best.score < min_score:
            break
        chosen.append(best)
        chosen_ids.add(best.course_id)
    return chosen


def reachable_course_ids_and_skill_bank(
    student: StudentProfile,
    candidate_course_ids: set[str],
    courses: dict[str, CanonicalCourse],
) -> tuple[set[str], dict[str, int]]:
    completed_courses = [
        courses[course_id]
        for course_id in student.completed_course_ids
        if course_id in courses and course_id in candidate_course_ids
    ]
    reachable_course_ids = {
        course.course_id
        for course in completed_courses
    }
    skill_bank = build_skill_bank(student.baseline_skill_bank, completed_courses)

    changed = True
    while changed:
        changed = False
        for course_id in sorted(candidate_course_ids - reachable_course_ids):
            course = courses[course_id]
            if hard_missing_prerequisites(course, skill_bank):
                continue
            reachable_course_ids.add(course_id)
            skill_bank = build_skill_bank(
                [ProgramSkillLevel(skill_id=skill_id, level=level) for skill_id, level in skill_bank.items()],
                [course],
            )
            changed = True

    return reachable_course_ids, skill_bank


def preflight_external_prereq_gaps(
    student: StudentProfile,
    candidate_course_ids: set[str],
    courses: dict[str, CanonicalCourse],
) -> list[str]:
    reachable_course_ids, reachable_skill_bank = reachable_course_ids_and_skill_bank(
        student=student,
        candidate_course_ids=candidate_course_ids,
        courses=courses,
    )
    gaps: list[str] = []
    unreachable_course_ids = sorted(candidate_course_ids - reachable_course_ids)
    for course_id in unreachable_course_ids:
        course = courses[course_id]
        for skill in course.input_skill_refs:
            if skill.importance != "required":
                continue
            if skill.confidence == "low":
                continue
            current_level = reachable_skill_bank.get(skill.canonical_skill_id, 0)
            if current_level < skill.min_level_required:
                gaps.append(
                    f"external_prereq_gap:{course_id}:{skill.canonical_skill_id}:{skill.min_level_required}"
                )
    return sorted(set(gaps))


def build_semester_summary(
    semester_plans: list[SemesterPlan],
    final_skill_bank: dict[str, int],
    track: TrackDefinition,
) -> StudentPlanSummary:
    achieved = target_coverage(final_skill_bank, track.target_skill_profile)
    gained_target_skills = sorted(skill_id for skill_id in final_skill_bank if skill_id in {item.skill_id for item in track.target_skill_profile})
    unmet_target_skills = sorted(
        item.skill_id
        for item in track.target_skill_profile
        if final_skill_bank.get(item.skill_id, 0) < item.target_level
    )
    return StudentPlanSummary(
        total_semesters=len(semester_plans),
        total_courses=sum(len(plan.selected_course_ids) for plan in semester_plans),
        achieved_target_coverage=round(achieved, 4),
        gained_target_skills=gained_target_skills,
        unmet_target_skills=unmet_target_skills,
    )


def build_semester_roadmap(
    program: CertificationProgram,
    courses: dict[str, CanonicalCourse],
    quality_snapshots: dict[str, QualitySnapshot],
) -> RoadmapResult:
    track = resolve_active_track(program)
    student = resolve_student_profile(program)
    planning_window = program.planning_window
    if planning_window is None:
        planning_window = type(
            "PlanningWindowFallback",
            (),
            {
                "start_term": student.starting_term,
                "horizon_semesters": track.roadmap_policy.max_semesters,
                "max_courses_per_semester": track.roadmap_policy.max_courses_per_semester,
            },
        )()

    term_sequence = build_term_sequence(
        planning_window.start_term,
        min(planning_window.horizon_semesters, track.roadmap_policy.max_semesters, 4),
    )
    matches = {match.course_id: match for match in match_courses_to_track(courses, track)}
    offerings = build_offering_index(program, set(matches))
    ctx = SemesterPlannerContext(
        track=track,
        student=student,
        term_sequence=term_sequence,
        course_matches=matches,
        offerings=offerings,
        completed_course_ids=set(student.completed_course_ids),
        program=program,
    )

    completed_courses = [courses[course_id] for course_id in student.completed_course_ids if course_id in courses]
    selected_courses: list[CanonicalCourse] = completed_courses.copy()
    semester_plans: list[SemesterPlan] = []
    unmet_constraints = preflight_external_prereq_gaps(
        student=student,
        candidate_course_ids=set(matches),
        courses=courses,
    )

    for semester_index, term in enumerate(term_sequence, start=1):
        skill_bank = build_skill_bank(student.baseline_skill_bank, selected_courses)
        ranked, blocked = eligible_courses_for_term(
            term=term,
            semester_index=semester_index,
            selected_courses=selected_courses,
            skill_bank=skill_bank,
            courses=courses,
            quality_snapshots=quality_snapshots,
            ctx=ctx,
        )
        chosen = select_courses_for_term(
            ranked=ranked,
            selected_courses=selected_courses,
            courses=courses,
            max_courses=min(planning_window.max_courses_per_semester, track.roadmap_policy.max_courses_per_semester),
        )
        chosen_courses = [courses[item.course_id] for item in chosen]
        selected_courses.extend(chosen_courses)
        updated_skill_bank = build_skill_bank(student.baseline_skill_bank, selected_courses)
        coverage_after = target_coverage(updated_skill_bank, track.target_skill_profile)
        semester_plans.append(
            SemesterPlan(
                semester_index=semester_index,
                term=term,
                selected_course_ids=[item.course_id for item in chosen],
                selected_courses=chosen,
                candidate_courses=ranked,
                blocked_course_reasons=blocked,
                coverage_after=round(coverage_after, 4),
                skill_bank_after=[
                    ProgramSkillLevel(skill_id=skill_id, level=level)
                    for skill_id, level in sorted(updated_skill_bank.items())
                ],
            )
        )
        if not ranked and blocked:
            unmet_constraints.append(f"semester_{semester_index}:no_eligible_courses")
        if coverage_after >= track.roadmap_policy.min_target_coverage and not ranked:
            break

    final_skill_bank = build_skill_bank(student.baseline_skill_bank, selected_courses)
    summary = build_semester_summary(semester_plans, final_skill_bank, track)
    if summary.achieved_target_coverage < track.roadmap_policy.min_target_coverage:
        unmet_constraints.append("target_coverage_below_policy")

    return RoadmapResult(
        program_id=program.program_id,
        selected_track_id=track.track_id,
        selected_course_ids=[course.course_id for course in selected_courses if course.course_id not in student.completed_course_ids],
        semester_plans=semester_plans,
        final_skill_bank=[
            ProgramSkillLevel(skill_id=skill_id, level=level)
            for skill_id, level in sorted(final_skill_bank.items())
        ],
        achieved_target_coverage=round(summary.achieved_target_coverage, 4),
        summary=summary,
        unmet_constraints=sorted(set(unmet_constraints)),
    )


def edge_status(
    course_id: str,
    selected_course_ids: set[str],
    program: CertificationProgram,
    max_pairwise_overlap: float,
) -> tuple[bool, float, float]:
    blocked = False
    bonus = 0.0
    penalty = 0.0
    for edge in program.program_edges:
        if edge.to_course_id != course_id:
            continue
        if edge.relation_type == "prerequisite" and edge.from_course_id not in selected_course_ids:
            blocked = True
        elif edge.relation_type == "recommended_after":
            if edge.from_course_id in selected_course_ids:
                bonus += 0.03 * edge.strength
            else:
                penalty += 0.02 * edge.strength
        elif edge.relation_type == "overlap_alternative" and edge.from_course_id in selected_course_ids:
            if edge.strength >= max_pairwise_overlap:
                blocked = True
            else:
                penalty += 0.05 * edge.strength
    return blocked, round(bonus, 4), round(penalty, 4)
