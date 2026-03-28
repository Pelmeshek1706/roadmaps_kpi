from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re

from roadmaps_mvp.io import iter_raw_course_paths, load_json, load_model, write_model
from roadmaps_mvp.models import RawCourse, SkillTaxonomy, SkillTaxonomyEntry


DEFAULT_ALIAS_GROUPS: dict[str, list[str]] = {
    "programming_fundamentals": ["basic_programming", "programming_basics"],
    "linear_algebra": ["linear_algebra_basics"],
    "algorithms_and_data_structures": ["data_structures_and_algorithms"],
    "ols_regression": ["ols_regression_modeling"],
}


@dataclass(frozen=True)
class ResolvedSkill:
    canonical_skill_id: str
    normalization_status: str


def prettify_skill_label(skill_id: str) -> str:
    return skill_id.replace("_", " ").strip().title()


def normalize_lookup_key(skill_id: str) -> str:
    normalized = skill_id.strip().lower()
    normalized = re.sub(r"[\s\-/]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


class SkillResolver:
    def __init__(self, taxonomy: SkillTaxonomy):
        self.taxonomy = taxonomy
        self.alias_to_canonical: dict[str, str] = {}
        self.labels: dict[str, str] = {}
        self.categories: dict[str, str] = {}
        for entry in taxonomy.skills:
            self.alias_to_canonical[normalize_lookup_key(entry.skill_id)] = entry.skill_id
            self.labels[entry.skill_id] = entry.canonical_label
            self.categories[entry.skill_id] = entry.category
            self.alias_to_canonical[normalize_lookup_key(entry.canonical_label)] = entry.skill_id
            self.alias_to_canonical[normalize_lookup_key(prettify_skill_label(entry.skill_id))] = entry.skill_id
            for alias in entry.aliases:
                self.alias_to_canonical[normalize_lookup_key(alias)] = entry.skill_id

    def resolve(self, skill_id: str) -> ResolvedSkill:
        canonical = self.alias_to_canonical.get(normalize_lookup_key(skill_id))
        if canonical is None:
            return ResolvedSkill(canonical_skill_id=skill_id, normalization_status="unmapped")
        if canonical == skill_id:
            return ResolvedSkill(canonical_skill_id=canonical, normalization_status="canonical")
        return ResolvedSkill(canonical_skill_id=canonical, normalization_status="aliased")

    def canonical_label(self, skill_id: str, fallback: str | None = None) -> str:
        resolved = self.resolve(skill_id)
        return self.labels.get(resolved.canonical_skill_id, fallback or prettify_skill_label(resolved.canonical_skill_id))

    def category_for(self, skill_id: str) -> str | None:
        resolved = self.resolve(skill_id)
        return self.categories.get(resolved.canonical_skill_id)


def _category_for_skill(skill_id: str) -> str:
    if any(token in skill_id for token in ("python", "programming", "algebra", "statistics", "mathematics", "algorithms")):
        return "foundation"
    if any(token in skill_id for token in ("analysis", "modeling", "forecasting", "classification", "clustering")):
        return "analytics"
    return "applied"


def build_skill_taxonomy(raw_courses: list[RawCourse]) -> SkillTaxonomy:
    labels: dict[str, str] = {}
    all_skill_ids: set[str] = set()
    for course in raw_courses:
        for item in course.input_skills_normalized:
            all_skill_ids.add(item.skill_id)
            labels.setdefault(item.skill_id, item.skill_label)
        for item in course.output_skills_normalized:
            all_skill_ids.add(item.skill_id)
            labels.setdefault(item.skill_id, item.skill_label)

    canonical_entries: dict[str, SkillTaxonomyEntry] = {}
    alias_map: dict[str, list[str]] = defaultdict(list)
    for skill_id in sorted(all_skill_ids):
        canonical_id = skill_id
        for candidate_canonical, aliases in DEFAULT_ALIAS_GROUPS.items():
            if skill_id == candidate_canonical or skill_id in aliases:
                canonical_id = candidate_canonical
                break
        if canonical_id != skill_id:
            alias_map[canonical_id].append(skill_id)

    canonical_ids = sorted({*all_skill_ids, *DEFAULT_ALIAS_GROUPS.keys()} - set().union(*DEFAULT_ALIAS_GROUPS.values()))
    for canonical_id in canonical_ids:
        canonical_entries[canonical_id] = SkillTaxonomyEntry(
            skill_id=canonical_id,
            canonical_label=labels.get(canonical_id, prettify_skill_label(canonical_id)),
            aliases=sorted(set(alias_map.get(canonical_id, []))),
            category=_category_for_skill(canonical_id),
        )

    return SkillTaxonomy(
        taxonomy_id="skills",
        version="v1",
        skills=sorted(canonical_entries.values(), key=lambda item: item.skill_id),
    )


def load_taxonomy(path: Path) -> SkillTaxonomy:
    return load_model(path, SkillTaxonomy)


def ensure_taxonomy(raw_dir: Path, taxonomy_path: Path) -> SkillTaxonomy:
    if taxonomy_path.exists():
        return load_taxonomy(taxonomy_path)
    raw_courses = [RawCourse.model_validate(load_json(path)) for path in iter_raw_course_paths(raw_dir)]
    taxonomy = build_skill_taxonomy(raw_courses)
    write_model(taxonomy_path, taxonomy)
    return taxonomy


def normalization_report(raw_courses: list[RawCourse], resolver: SkillResolver) -> dict:
    skill_occurrences: dict[str, dict] = {}
    canonical_groups: dict[str, list[str]] = defaultdict(list)
    unmapped: set[str] = set()
    for course in raw_courses:
        for item in [*course.input_skills_normalized, *course.output_skills_normalized]:
            resolved = resolver.resolve(item.skill_id)
            canonical_groups[resolved.canonical_skill_id].append(item.skill_id)
            if resolved.normalization_status == "unmapped":
                unmapped.add(item.skill_id)
            skill_occurrences[item.skill_id] = {
                "resolved_to": resolved.canonical_skill_id,
                "status": resolved.normalization_status,
            }

    return {
        "total_unique_skill_ids": len(skill_occurrences),
        "alias_groups": {
            canonical_id: sorted(set(ids))
            for canonical_id, ids in sorted(canonical_groups.items())
            if len(set(ids)) > 1
        },
        "skill_occurrences": dict(sorted(skill_occurrences.items())),
        "unmapped_skill_ids": sorted(unmapped),
    }
