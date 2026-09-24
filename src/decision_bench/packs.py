from __future__ import annotations

from pathlib import Path

import jsonschema
import yaml

from decision_bench.domain import GoldCase, TaskPack


def load_packs(packs_dir: Path) -> dict[str, TaskPack]:
    packs: dict[str, TaskPack] = {}
    if not packs_dir.is_dir():
        raise FileNotFoundError(f"Pack directory not found: {packs_dir}")
    for path in sorted(packs_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name} must contain a mapping")
        schema = raw["output_schema"]
        jsonschema.Draft202012Validator.check_schema(schema)
        cases = []
        for item in raw.get("cases") or []:
            cases.append(
                GoldCase(
                    id=str(item["id"]),
                    title=str(item.get("title") or item["id"]),
                    input=str(item["input"]),
                    expected=dict(item.get("expected") or {}),
                    expect_min_children=int(item.get("expect_min_children") or 0),
                )
            )
        pack = TaskPack(
            id=str(raw["id"]),
            name=str(raw["name"]),
            description=str(raw.get("description") or ""),
            output_schema=schema,
            cases=cases,
        )
        if pack.id in packs:
            raise ValueError(f"Duplicate pack id {pack.id}")
        packs[pack.id] = pack
    return packs


def find_case(packs: dict[str, TaskPack], pack_id: str | None, case_id: str | None) -> GoldCase | None:
    if not pack_id or not case_id:
        return None
    pack = packs.get(pack_id)
    if pack is None:
        return None
    return next((case for case in pack.cases if case.id == case_id), None)
