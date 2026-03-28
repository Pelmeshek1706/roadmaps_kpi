from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


LevelConfidence = Literal["high", "medium", "low"]
InputImportance = Literal["required", "recommended", "optional"]
OutputImportance = Literal["core", "secondary", "incidental"]
SkillSource = Literal["explicit", "inferred"]
SlotType = Literal["required", "elective", "choose_n_of_m"]
ProgramRelationType = Literal["prerequisite", "recommended_after", "overlap_alternative"]
ReviewStatus = Literal["unreviewed", "reviewed", "placeholder"]
CourseStage = Literal["foundation", "core", "advanced"]
TrackCourseRole = Literal["foundation", "core", "specialization", "elective"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DomainScore(StrictModel):
    domain_id: str
    domain_label: str
    score: float = Field(ge=0.0, le=1.0)


class CourseNature(StrictModel):
    theory_level: str
    implementation_level: str
    research_orientation: str
    software_engineering_orientation: str


class CourseProfile(StrictModel):
    one_sentence_summary: str
    short_summary: str
    discipline_positioning: list[str]
    course_nature: CourseNature


class SourceCoverage(StrictModel):
    analysis_mode: str
    single_document_used: bool
    syllabus_used: bool
    lab_works_used_count: int = Field(ge=0)
    lectures_used_count: int = Field(ge=0)
    extra_materials_used_count: int = Field(ge=0)
    coverage_comment: str


class EvidenceItem(StrictModel):
    claim: str
    source_type: str
    source_title: str
    evidence_text: str


class LearningOutcomes(StrictModel):
    theoretical_understanding: list[str]
    practical_implementation: list[str]
    analysis_validation: list[str]
    architecture_design: list[str]
    research_and_prototyping: list[str]


class StudentTakeaway(StrictModel):
    what_student_can_understand: list[str]
    what_student_can_build: list[str]
    what_student_can_apply: list[str]


class TopicTags(StrictModel):
    core_concepts: list[str]
    methods_models: list[str]
    data_modalities: list[str]
    problem_types: list[str]
    application_domains: list[str]
    engineering_aspects: list[str]


class TechnologyTags(StrictModel):
    programming_languages: list[str]
    libraries_frameworks_tools_explicit: list[str]
    systems_platforms_explicit: list[str]
    technologies_inferred_strong: list[str]
    technologies_mentioned_in_resources_only: list[str]


class PracticalComponents(StrictModel):
    lab_formats: list[str]
    expected_artifacts: list[str]
    implementation_tasks: list[str]
    engineering_practices: list[str]


class RawInputSkill(StrictModel):
    skill_id: str
    skill_label: str
    min_level_required: int = Field(ge=0, le=4)
    importance: InputImportance
    source: SkillSource
    confidence: LevelConfidence
    raw_mentions: list[str]
    evidence: list[str]


class RawOutputSkill(StrictModel):
    skill_id: str
    skill_label: str
    result_level: int = Field(ge=0, le=4)
    importance: OutputImportance
    source: SkillSource
    confidence: LevelConfidence
    raw_mentions: list[str]
    evidence: list[str]


class RawCurricularRelation(StrictModel):
    from_id: str
    to_id: str
    from_label: str
    to_label: str
    relation: str
    reason: str


class RawCourse(StrictModel):
    status: str
    course_name: str
    course_profile: CourseProfile
    domain_scores: list[DomainScore]
    source_coverage: SourceCoverage
    discipline_tags: list[str]
    topic_tags: TopicTags
    technology_tags: TechnologyTags
    input_skills_normalized: list[RawInputSkill]
    output_skills_normalized: list[RawOutputSkill]
    curricular_relations: list[RawCurricularRelation]
    practical_components: PracticalComponents
    learning_outcomes: LearningOutcomes
    student_takeaway: StudentTakeaway
    evidence: list[EvidenceItem]
    confidence: LevelConfidence


class SkillLevelScale(StrictModel):
    min: int = Field(ge=0, le=4)
    max: int = Field(ge=0, le=4)

    @field_validator("max")
    @classmethod
    def validate_bounds(cls, value: int, info) -> int:
        lower_bound = info.data.get("min", 0)
        if value < lower_bound:
            raise ValueError("max must be >= min")
        return value


class SkillTaxonomyEntry(StrictModel):
    skill_id: str
    canonical_label: str
    aliases: list[str] = Field(default_factory=list)
    category: str = "general"
    level_scale: SkillLevelScale = Field(default_factory=lambda: SkillLevelScale(min=0, max=4))
    status: str = "active"


class SkillTaxonomy(StrictModel):
    taxonomy_id: str
    version: str
    skills: list[SkillTaxonomyEntry]


class InputSkillRef(StrictModel):
    canonical_skill_id: str
    source_skill_id: str
    skill_label: str
    min_level_required: int = Field(ge=0, le=4)
    importance: InputImportance
    source: SkillSource
    confidence: LevelConfidence
    raw_mentions: list[str]
    evidence: list[str]
    normalization_status: str


class OutputSkillRef(StrictModel):
    canonical_skill_id: str
    source_skill_id: str
    skill_label: str
    result_level: int = Field(ge=0, le=4)
    importance: OutputImportance
    source: SkillSource
    confidence: LevelConfidence
    raw_mentions: list[str]
    evidence: list[str]
    normalization_status: str


class IntraCourseRelation(StrictModel):
    from_skill_id: str
    to_skill_id: str
    from_canonical_skill_id: str
    to_canonical_skill_id: str
    from_label: str
    to_label: str
    relation: str
    reason: str


class CanonicalCourse(StrictModel):
    course_id: str
    course_name: str
    version: str
    raw_source_file: str
    course_profile: CourseProfile
    domain_scores: list[DomainScore]
    discipline_tags: list[str]
    topic_tags: TopicTags
    technology_tags: TechnologyTags
    learning_outcomes: LearningOutcomes
    student_takeaway: StudentTakeaway
    input_skill_refs: list[InputSkillRef]
    output_skill_refs: list[OutputSkillRef]
    practical_profile: PracticalComponents
    intra_course_relations: list[IntraCourseRelation]
    quality_ref: str


class QualitySnapshot(StrictModel):
    entity_id: str
    entity_type: str
    raw_source_file: str
    extraction_confidence: LevelConfidence
    source_coverage: SourceCoverage
    review_status: ReviewStatus
    evidence_count: int = Field(ge=0)
    quality_score_placeholder: float = Field(ge=0.0, le=1.0)


class ProgramTargetSkill(StrictModel):
    skill_id: str
    target_level: int = Field(ge=0, le=4)
    weight: float = Field(gt=0.0)


class ProgramSkillLevel(StrictModel):
    skill_id: str
    level: int = Field(ge=0, le=4)


class ProgramDomainPreference(StrictModel):
    domain_id: str
    weight: float = Field(ge=0.0, le=1.0)


class AcademicTerm(StrictModel):
    course: int = Field(ge=1)
    semester: int = Field(ge=1, le=2)


class PlanningWindow(StrictModel):
    start_term: AcademicTerm
    horizon_semesters: int = Field(default=4, ge=1, le=4)
    max_courses_per_semester: int = Field(default=3, ge=1)


class TrackRoadmapPolicy(StrictModel):
    max_semesters: int = Field(default=4, ge=1, le=4)
    max_courses_per_semester: int = Field(default=3, ge=1)
    min_track_affinity: float = Field(default=0.25, ge=0.0, le=1.0)
    max_pairwise_overlap: float = Field(default=0.85, ge=0.0, le=1.0)
    min_target_coverage: float = Field(default=0.6, ge=0.0, le=1.0)


class TrackDefinition(StrictModel):
    track_id: str
    title: str
    selector_domains: list[ProgramDomainPreference] = Field(default_factory=list)
    selector_tags: list[str] = Field(default_factory=list)
    selector_skill_ids: list[str] = Field(default_factory=list)
    target_skill_profile: list[ProgramTargetSkill]
    target_domain_scores: list[ProgramDomainPreference] = Field(default_factory=list)
    foundation_skill_ids: list[str] = Field(default_factory=list)
    roadmap_policy: TrackRoadmapPolicy = Field(default_factory=TrackRoadmapPolicy)
    manual_includes: list[str] = Field(default_factory=list)
    manual_excludes: list[str] = Field(default_factory=list)


class CourseOffering(StrictModel):
    course_id: str
    stage: CourseStage
    available_terms: list[AcademicTerm]
    availability_confidence: LevelConfidence = "medium"
    source: str = "manual"


class StudentProfile(StrictModel):
    student_id: str
    baseline_skill_bank: list[ProgramSkillLevel] = Field(default_factory=list)
    completed_course_ids: list[str] = Field(default_factory=list)
    starting_term: AcademicTerm


class ProgramSlot(StrictModel):
    slot_id: str
    title: str
    slot_type: SlotType
    candidate_course_ids: list[str]
    target_skill_ids: list[str] = Field(default_factory=list)
    prerequisite_slot_ids: list[str] = Field(default_factory=list)
    selection_count: int = Field(default=1, ge=1)
    course: int | None = Field(default=None, ge=1)
    semester: int | None = Field(default=None, ge=1)


class ProgramEdge(StrictModel):
    from_course_id: str
    to_course_id: str
    relation_type: ProgramRelationType
    support_skill_ids: list[str] = Field(default_factory=list)
    strength: float = Field(ge=0.0, le=1.0)
    status: str
    provenance: str


class CompletionRules(StrictModel):
    required_slot_ids: list[str] = Field(default_factory=list)
    min_selected_courses: int = Field(default=0, ge=0)
    target_coverage_threshold: float = Field(default=0.6, ge=0.0, le=1.0)


class CertificationProgram(StrictModel):
    program_id: str
    title: str
    track_id: str
    version: str
    roadmap_mode: str
    tracks: list[TrackDefinition] = Field(default_factory=list)
    planning_window: PlanningWindow | None = None
    student_profile: StudentProfile | None = None
    course_offerings: list[CourseOffering] = Field(default_factory=list)
    target_skill_profile: list[ProgramTargetSkill] = Field(default_factory=list)
    baseline_skill_bank: list[ProgramSkillLevel] = Field(default_factory=list)
    target_domain_scores: list[ProgramDomainPreference] = Field(default_factory=list)
    slots: list[ProgramSlot] = Field(default_factory=list)
    program_edges: list[ProgramEdge] = Field(default_factory=list)
    completion_rules: CompletionRules = Field(default_factory=CompletionRules)


class TrackCourseMatch(StrictModel):
    track_id: str
    course_id: str
    affinity_score: float = Field(ge=0.0, le=1.0)
    role: TrackCourseRole | None = None
    matched_domain_ids: list[str] = Field(default_factory=list)
    matched_target_skill_ids: list[str] = Field(default_factory=list)
    matched_tags: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class TargetSkillGain(StrictModel):
    skill_id: str
    gained_levels: int = Field(ge=0)
    target_level: int = Field(ge=0, le=4)
    resulting_level: int = Field(ge=0, le=4)


class RankedCourseOption(StrictModel):
    course_id: str
    course_name: str
    score: float = Field(ge=0.0, le=1.0)
    track_affinity: float = Field(default=0.0, ge=0.0, le=1.0)
    prerequisite_fit: float = Field(ge=0.0, le=1.0)
    readiness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    target_skill_gain: float = Field(ge=0.0, le=1.0)
    unlock_score: float = Field(default=0.0, ge=0.0, le=1.0)
    domain_alignment: float = Field(ge=0.0, le=1.0)
    phase_fit_score: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_score: float = Field(ge=0.0, le=1.0)
    overlap_penalty: float = Field(ge=0.0, le=1.0)
    stage: CourseStage | None = None
    missing_prerequisites: list[str]
    gained_target_skills: list[TargetSkillGain]
    overlaps_with_selected: list[str]


class SlotSelection(StrictModel):
    slot_id: str
    slot_type: SlotType
    selected_course_ids: list[str]
    ranked_options: list[RankedCourseOption]


class SemesterPlan(StrictModel):
    semester_index: int = Field(ge=1)
    term: AcademicTerm
    selected_course_ids: list[str]
    selected_courses: list[RankedCourseOption]
    candidate_courses: list[RankedCourseOption] = Field(default_factory=list)
    blocked_course_reasons: list[str] = Field(default_factory=list)
    coverage_after: float = Field(ge=0.0, le=1.0)
    skill_bank_after: list[ProgramSkillLevel]


class StudentPlanSummary(StrictModel):
    total_semesters: int = Field(ge=0)
    total_courses: int = Field(ge=0)
    achieved_target_coverage: float = Field(ge=0.0, le=1.0)
    gained_target_skills: list[str] = Field(default_factory=list)
    unmet_target_skills: list[str] = Field(default_factory=list)


class RoadmapResult(StrictModel):
    program_id: str
    selected_track_id: str | None = None
    selected_course_ids: list[str]
    slot_results: list[SlotSelection] = Field(default_factory=list)
    semester_plans: list[SemesterPlan] = Field(default_factory=list)
    final_skill_bank: list[ProgramSkillLevel]
    achieved_target_coverage: float = Field(ge=0.0, le=1.0)
    summary: StudentPlanSummary | None = None
    unmet_constraints: list[str] = Field(default_factory=list)
