from __future__ import annotations

from pathlib import Path

from roadmaps_mvp.io import write_json
from roadmaps_mvp.models import (
    CanonicalCourse,
    CertificationProgram,
    QualitySnapshot,
    RawCourse,
    RoadmapResult,
    SkillTaxonomy,
)


SCHEMA_MODELS = {
    "raw_course.v1.schema.json": RawCourse,
    "canonical_course.v1.schema.json": CanonicalCourse,
    "skill_taxonomy.v1.schema.json": SkillTaxonomy,
    "certification_program.v1.schema.json": CertificationProgram,
    "quality_snapshot.v1.schema.json": QualitySnapshot,
    "roadmap_result.v1.schema.json": RoadmapResult,
}


def write_schema_snapshots(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for file_name, model in SCHEMA_MODELS.items():
        path = output_dir / file_name
        write_json(path, model.model_json_schema())
        written.append(path)
    return written
