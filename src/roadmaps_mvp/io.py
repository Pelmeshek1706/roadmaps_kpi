from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel


ModelT = TypeVar("ModelT", bound=BaseModel)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


RAW_COURSE_SIGNATURE_KEYS = {
    "course_name",
    "course_profile",
    "domain_scores",
    "input_skills_normalized",
    "output_skills_normalized",
    "curricular_relations",
    "practical_components",
    "source_coverage",
    "confidence",
}


def is_raw_course_json(path: Path) -> bool:
    try:
        payload = load_json(path)
    except json.JSONDecodeError:
        return False
    return RAW_COURSE_SIGNATURE_KEYS.issubset(payload.keys())


def iter_raw_course_paths(raw_dir: Path) -> list[Path]:
    return sorted(path for path in raw_dir.glob("*.json") if path.is_file() and is_raw_course_json(path))


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_model(path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(load_json(path))


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict | list) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_model(path: Path, model: BaseModel) -> None:
    write_json(path, model.model_dump(mode="json"))


def write_models(path_by_name: dict[str, BaseModel]) -> None:
    for file_path, model in path_by_name.items():
        write_model(Path(file_path), model)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(json.loads(line))
    return items


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    ensure_parent(path)
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")
