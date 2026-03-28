# Roadmaps MVP

This repository now treats the existing top-level `*.json` files as raw course extraction artifacts and adds a Python MVP around them.

## Layers

- Raw layer: existing course JSON files in the repository root
- Taxonomy layer: [`taxonomies/skills.json`](/Users/pelmeshek1706/Desktop/projects/roadmaps/taxonomies/skills.json)
- Program layer: [`programs/data_science_mvp.json`](/Users/pelmeshek1706/Desktop/projects/roadmaps/programs/data_science_mvp.json)
- Canonical layer: generated into `canonical_courses/`
- Quality layer: generated into `quality/course_quality.jsonl`
- Schema snapshots: generated into `schemas/`

## CLI

Run commands with:

```bash
PYTHONPATH=src python3 -m roadmaps_mvp.cli --help
```

Main commands:

- `validate-courses`
- `normalize-skills`
- `build-canonical-courses`
- `rank-slot-options`
- `build-roadmap`
- `generate-schemas`

The raw course JSON files are not rewritten by the pipeline.
