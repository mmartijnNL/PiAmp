from __future__ import annotations

import math
from pathlib import Path

import yaml


class StateStore:
    def __init__(self, state_path: str | Path | None = None):
        self.state_path = Path(state_path) if state_path is not None else Path("config/routes.yaml")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, strict: bool = False) -> dict[str, dict[str, float]]:
        if not self.state_path.exists():
            if strict:
                raise FileNotFoundError(f"routes file not found: {self.state_path}")
            return {}

        try:
            raw = yaml.safe_load(self.state_path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            if strict:
                raise
            return {}

        if strict:
            if not isinstance(raw, dict) or any(not isinstance(values, dict) for values in raw.values()):
                raise ValueError("routes must map zone names to input/gain mappings")
            for values in raw.values():
                for gain in values.values():
                    if isinstance(gain, bool) or not math.isfinite(float(gain)):
                        raise ValueError("route gains must be finite numbers")

        if isinstance(raw, dict):
            return {zone: {input_name: float(gain) for input_name, gain in values.items()} for zone, values in raw.items() if isinstance(values, dict)}
        return {}

    def save(self, state: dict[str, dict[str, float]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
