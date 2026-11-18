from __future__ import annotations

from pathlib import Path

import yaml

from .logic import RouteState, parse_route_state


class StateStore:
    def __init__(self, state_path: str | Path | None = None):
        self.state_path = Path(state_path) if state_path is not None else Path("config/routes.yaml")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, strict: bool = False) -> RouteState:
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

        if not strict and not isinstance(raw, dict):
            return {}
        return parse_route_state(raw)

    def save(self, state: RouteState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
