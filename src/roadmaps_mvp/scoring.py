from __future__ import annotations

from collections import defaultdict

from roadmaps_mvp.models import (
    CanonicalCourse,
    InputSkillRef,
    OutputSkillRef,
    ProgramDomainPreference,
    ProgramSkillLevel,
    ProgramTargetSkill,
    QualitySnapshot,
    RankedCourseOption,
    TargetSkillGain,
)


INPUT_IMPORTANCE_WEIGHTS = {"required": 1.0, "recommended": 0.6, "optional": 0.25}
OUTPUT_IMPORTANCE_WEIGHTS = {"core": 1.0, "secondary": 0.6, "incidental": 0.25}
CONFIDENCE_WEIGHTS = {"high": 1.0, "medium": 0.75, "low": 0.5}


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def input_weight(skill: InputSkillRef) -> float:
    return INPUT_IMPORTANCE_WEIGHTS[skill.importance] * CONFIDENCE_WEIGHTS[skill.confidence]


def output_weight(skill: OutputSkillRef) -> float:
    return OUTPUT_IMPORTANCE_WEIGHTS[skill.importance] * CONFIDENCE_WEIGHTS[skill.confidence]


def build_skill_bank(
    baseline_skills: list[ProgramSkillLevel],
    selected_courses: list[CanonicalCourse],
) -> dict[str, int]:
    skill_bank: dict[str, int] = {item.skill_id: item.level for item in baseline_skills}
    for course in selected_courses:
        for skill in course.output_skill_refs:
            current_level = skill_bank.get(skill.canonical_skill_id, 0)
            skill_bank[skill.canonical_skill_id] = max(current_level, skill.result_level)
    return skill_bank


def prerequisite_fit(course: CanonicalCourse, skill_bank: dict[str, int]) -> tuple[float, list[str]]:
    if not course.input_skill_refs:
        return 1.0, []

    weighted_total = 0.0
    weighted_score = 0.0
    missing: list[str] = []
    for skill in course.input_skill_refs:
        weight = input_weight(skill)
        current_level = skill_bank.get(skill.canonical_skill_id, 0)
        fit = clamp(current_level / skill.min_level_required) if skill.min_level_required else 1.0
        weighted_total += weight
        weighted_score += weight * fit
        if current_level < skill.min_level_required:
            missing.append(skill.canonical_skill_id)

    return (weighted_score / weighted_total if weighted_total else 1.0), sorted(set(missing))


def _course_output_levels(course: CanonicalCourse) -> dict[str, int]:
    levels: dict[str, int] = {}
    for skill in course.output_skill_refs:
        levels[skill.canonical_skill_id] = max(levels.get(skill.canonical_skill_id, 0), skill.result_level)
    return levels


def target_skill_gain(
    course: CanonicalCourse,
    target_profile: list[ProgramTargetSkill],
    skill_bank: dict[str, int],
) -> tuple[float, list[TargetSkillGain]]:
    if not target_profile:
        return 0.0, []

    output_levels = _course_output_levels(course)
    weighted_total = 0.0
    weighted_gain = 0.0
    gains: list[TargetSkillGain] = []

    for target in target_profile:
        weighted_total += target.weight
        current_level = skill_bank.get(target.skill_id, 0)
        output_level = output_levels.get(target.skill_id, 0)
        resulting_level = max(current_level, output_level)
        gained_levels = max(0, min(output_level, target.target_level) - current_level)
        gain_ratio = gained_levels / target.target_level if target.target_level else 0.0
        weighted_gain += target.weight * gain_ratio
        if gained_levels > 0:
            gains.append(
                TargetSkillGain(
                    skill_id=target.skill_id,
                    gained_levels=gained_levels,
                    target_level=target.target_level,
                    resulting_level=min(resulting_level, target.target_level),
                )
            )

    return (weighted_gain / weighted_total if weighted_total else 0.0), sorted(gains, key=lambda item: item.skill_id)


def domain_alignment(course: CanonicalCourse, target_domains: list[ProgramDomainPreference]) -> float:
    if not target_domains:
        return 0.0

    course_domains = {domain.domain_id: domain.score for domain in course.domain_scores}
    total_weight = sum(item.weight for item in target_domains)
    if total_weight <= 0:
        return 0.0

    dot_product = sum(course_domains.get(item.domain_id, 0.0) * item.weight for item in target_domains)
    return clamp(dot_product / total_weight)


def evidence_score(snapshot: QualitySnapshot | None) -> float:
    if snapshot is None:
        return 0.0
    confidence_component = CONFIDENCE_WEIGHTS[snapshot.extraction_confidence]
    coverage = snapshot.source_coverage
    source_depth = 0.0
    if coverage.syllabus_used:
        source_depth += 0.25
    source_depth += min(coverage.lab_works_used_count, 10) / 20
    source_depth += min(coverage.lectures_used_count, 10) / 20
    source_depth += min(coverage.extra_materials_used_count, 10) / 20
    if coverage.single_document_used:
        source_depth *= 0.9
    return clamp(0.6 * confidence_component + 0.4 * clamp(source_depth))


def weighted_jaccard_outputs(course_a: CanonicalCourse, course_b: CanonicalCourse) -> float:
    weights_a = defaultdict(float)
    weights_b = defaultdict(float)
    for skill in course_a.output_skill_refs:
        weights_a[skill.canonical_skill_id] = max(weights_a[skill.canonical_skill_id], output_weight(skill))
    for skill in course_b.output_skill_refs:
        weights_b[skill.canonical_skill_id] = max(weights_b[skill.canonical_skill_id], output_weight(skill))

    keys = set(weights_a) | set(weights_b)
    if not keys:
        return 0.0
    numerator = sum(min(weights_a.get(key, 0.0), weights_b.get(key, 0.0)) for key in keys)
    denominator = sum(max(weights_a.get(key, 0.0), weights_b.get(key, 0.0)) for key in keys)
    return clamp(numerator / denominator if denominator else 0.0)


def overlap_penalty(course: CanonicalCourse, selected_courses: list[CanonicalCourse]) -> tuple[float, list[str]]:
    if not selected_courses:
        return 0.0, []

    overlaps: list[tuple[str, float]] = []
    for selected in selected_courses:
        score = weighted_jaccard_outputs(course, selected)
        if score > 0:
            overlaps.append((selected.course_id, score))

    if not overlaps:
        return 0.0, []

    max_penalty = max(score for _, score in overlaps)
    overlap_ids = [course_id for course_id, score in overlaps if score >= 0.05]
    return clamp(max_penalty), sorted(overlap_ids)


def rank_course_option(
    course: CanonicalCourse,
    quality_snapshot: QualitySnapshot | None,
    selected_courses: list[CanonicalCourse],
    skill_bank: dict[str, int],
    target_profile: list[ProgramTargetSkill],
    target_domains: list[ProgramDomainPreference],
) -> RankedCourseOption:
    prereq_score, missing_prereqs = prerequisite_fit(course, skill_bank)
    target_gain_score, gained_skills = target_skill_gain(course, target_profile, skill_bank)
    domain_score = domain_alignment(course, target_domains)
    evidence = evidence_score(quality_snapshot)
    overlap, overlap_ids = overlap_penalty(course, selected_courses)
    total = clamp(
        0.45 * prereq_score
        + 0.35 * target_gain_score
        + 0.10 * domain_score
        + 0.10 * evidence
        - 0.20 * overlap
    )
    return RankedCourseOption(
        course_id=course.course_id,
        course_name=course.course_name,
        score=round(total, 4),
        prerequisite_fit=round(prereq_score, 4),
        target_skill_gain=round(target_gain_score, 4),
        domain_alignment=round(domain_score, 4),
        evidence_score=round(evidence, 4),
        overlap_penalty=round(overlap, 4),
        missing_prerequisites=missing_prereqs,
        gained_target_skills=gained_skills,
        overlaps_with_selected=overlap_ids,
    )


def target_coverage(
    skill_bank: dict[str, int],
    target_profile: list[ProgramTargetSkill],
) -> float:
    if not target_profile:
        return 0.0
    weighted_total = sum(item.weight for item in target_profile)
    weighted_score = 0.0
    for item in target_profile:
        current_level = skill_bank.get(item.skill_id, 0)
        coverage = clamp(current_level / item.target_level) if item.target_level else 1.0
        weighted_score += item.weight * coverage
    return clamp(weighted_score / weighted_total if weighted_total else 0.0)
