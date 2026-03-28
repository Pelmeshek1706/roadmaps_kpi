from __future__ import annotations

from collections.abc import Iterable

from roadmaps_mvp.models import CanonicalCourse, ProgramDomainPreference, TrackCourseMatch, TrackDefinition
from roadmaps_mvp.scoring import clamp, domain_alignment


def _normalize_tag(value: str) -> str:
    return value.strip().lower().replace(" / ", "_").replace(" ", "_")


def course_tags(course: CanonicalCourse) -> set[str]:
    tags = {_normalize_tag(tag) for tag in course.discipline_tags}
    topic_values = course.topic_tags.model_dump(mode="json")
    for values in topic_values.values():
        tags.update(_normalize_tag(value) for value in values)
    return tags


def course_output_skill_ids(course: CanonicalCourse) -> set[str]:
    return {skill.canonical_skill_id for skill in course.output_skill_refs}


def target_skill_ids(track: TrackDefinition) -> set[str]:
    explicit = set(track.selector_skill_ids)
    explicit.update(skill.skill_id for skill in track.target_skill_profile)
    return explicit


def _tag_similarity(course: CanonicalCourse, selector_tags: Iterable[str]) -> tuple[float, list[str]]:
    selector = {_normalize_tag(tag) for tag in selector_tags if tag}
    if not selector:
        return 0.0, []
    course_tag_set = course_tags(course)
    overlap = sorted(selector & course_tag_set)
    union = selector | course_tag_set
    return (len(overlap) / len(union) if union else 0.0), overlap


def _skill_overlap(course: CanonicalCourse, track: TrackDefinition) -> tuple[float, list[str]]:
    track_skills = target_skill_ids(track)
    if not track_skills:
        return 0.0, []
    course_skills = course_output_skill_ids(course)
    overlap = sorted(track_skills & course_skills)
    union = track_skills | course_skills
    return (len(overlap) / len(union) if union else 0.0), overlap


def infer_track_role(match: TrackCourseMatch, foundation_skill_ids: set[str]) -> str:
    matched_skills = set(match.matched_target_skill_ids)
    if matched_skills & foundation_skill_ids:
        return "foundation"
    if len(matched_skills) >= 3:
        return "core"
    if matched_skills:
        return "specialization"
    return "elective"


def score_track_match(course: CanonicalCourse, track: TrackDefinition) -> TrackCourseMatch:
    domain_selector: list[ProgramDomainPreference] = track.selector_domains or track.target_domain_scores
    domain_score = domain_alignment(course, domain_selector)
    tag_score, matched_tags = _tag_similarity(course, track.selector_tags)
    skill_score, matched_skill_ids = _skill_overlap(course, track)
    affinity = clamp(0.55 * domain_score + 0.25 * tag_score + 0.20 * skill_score)
    matched_domains = sorted(
        domain.domain_id
        for domain in course.domain_scores
        if domain.domain_id in {item.domain_id for item in domain_selector}
    )
    reason_codes: list[str] = []
    if matched_domains:
        reason_codes.append("domain_alignment")
    if matched_tags:
        reason_codes.append("tag_similarity")
    if matched_skill_ids:
        reason_codes.append("target_skill_overlap")
    role = infer_track_role(
        TrackCourseMatch(
            track_id=track.track_id,
            course_id=course.course_id,
            affinity_score=affinity,
            role=None,
            matched_domain_ids=matched_domains,
            matched_target_skill_ids=matched_skill_ids,
            matched_tags=matched_tags,
            reason_codes=reason_codes,
        ),
        set(track.foundation_skill_ids),
    )
    return TrackCourseMatch(
        track_id=track.track_id,
        course_id=course.course_id,
        affinity_score=round(affinity, 4),
        role=role,
        matched_domain_ids=matched_domains,
        matched_target_skill_ids=matched_skill_ids,
        matched_tags=matched_tags,
        reason_codes=reason_codes,
    )


def match_courses_to_track(courses: dict[str, CanonicalCourse], track: TrackDefinition) -> list[TrackCourseMatch]:
    matches: list[TrackCourseMatch] = []
    for course in courses.values():
        match = score_track_match(course, track)
        if course.course_id in track.manual_includes:
            match = match.model_copy(
                update={
                    "affinity_score": max(match.affinity_score, track.roadmap_policy.min_track_affinity),
                    "reason_codes": sorted(set([*match.reason_codes, "manual_include"])),
                }
            )
        if course.course_id in track.manual_excludes:
            continue
        if match.affinity_score >= track.roadmap_policy.min_track_affinity:
            matches.append(match)
    return sorted(matches, key=lambda item: (-item.affinity_score, item.course_id))
