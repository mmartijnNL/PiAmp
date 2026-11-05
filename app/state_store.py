from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, state_path: str | Path | None = None):
        self.state_path = Path(state_path) if state_path is not None else Path("state/routes.json")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, dict[str, float]]:
        if not self.state_path.exists():
            return {}

        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

        if isinstance(raw, dict):
            return {zone: {input_name: float(gain) for input_name, gain in values.items()} for zone, values in raw.items() if isinstance(values, dict)}
        return {}

    def save(self, state: dict[str, dict[str, float]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
