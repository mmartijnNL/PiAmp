from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from .models import InputDefinition, SystemConfig, ZoneDefinition


def _validate_unique_names(items: Iterable[str], kind: str) -> None:
    seen: set[str] = set()
    duplicates = []
    for item in items:
        if item in seen:
            duplicates.append(item)
        seen.add(item)
    if duplicates:
        duplicate_names = ", ".join(sorted(set(duplicates)))
        raise ValueError(f"duplicate {kind} name(s): {duplicate_names}")


def load_config(path: str | Path) -> SystemConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config_name = raw.get("name", "piamp")

    input_defs = raw.get("inputs", [])
    zone_defs = raw.get("zones", [])

    input_names = [item.get("name") for item in input_defs if isinstance(item, dict)]
    zone_names = [item.get("name") for item in zone_defs if isinstance(item, dict)]
    _validate_unique_names(input_names, "input")
    _validate_unique_names(zone_names, "zone")

    parsed_inputs = tuple(
        InputDefinition(
            name=item["name"],
            source=item.get("source", "pipewire"),
            channels=list(item.get("channels", [])),
        )
        for item in input_defs
        if isinstance(item, dict)
    )

    parsed_zones = tuple(
        ZoneDefinition(
            name=item["name"],
            device=item.get("device", "default"),
            channels=list(item.get("channels", [])),
        )
        for item in zone_defs
        if isinstance(item, dict)
    )

    return SystemConfig(
        name=config_name,
        inputs=parsed_inputs,
        zones=parsed_zones,
        routes=(),
    )
