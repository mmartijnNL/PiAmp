from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import SystemConfig
from .state_store import StateStore


class RouteManager:
    def __init__(self, config: SystemConfig | None = None, state_path: str | Path | None = None, *, persist_state: bool = True, strict_state: bool = False):
        self._inputs: dict[str, list[str]] = {}
        self._zones: dict[str, dict[str, object]] = {}
        self.state: dict[str, dict[str, float]] = {}
        self.state_store = StateStore(state_path)

        if config is not None:
            for input_def in config.inputs:
                self.add_input(input_def.name, list(input_def.channels))
            for zone_def in config.zones:
                self.add_zone(zone_def.name, zone_def.device, list(zone_def.channels))

        self.state = self.state_store.load(strict=strict_state)
        if config is not None:
            filtered_state: dict[str, dict[str, float]] = {}

            for zone_name, route_map in self.state.items():
                if zone_name not in self._zones:
                    continue
                filtered_state[zone_name] = {
                    input_name: float(gain)
                    for input_name, gain in route_map.items()
                    if input_name in self._inputs
                }

            self.state = filtered_state
            for zone_name in self._zones:
                self.state.setdefault(zone_name, {})

            if persist_state:
                self.save_state()

    def add_input(self, name: str, channels: list[str] | None = None) -> None:
        self._inputs[name] = list(channels or [])

    def add_zone(self, name: str, output_device: str, output_channels: list[str] | None = None) -> None:
        self._zones[name] = {
            "output_device": output_device,
            "output_channels": list(output_channels or []),
        }
        self.state.setdefault(name, {})

    def save_state(self) -> None:
        self.state_store.save(self.state)

    def _expand_zones(self, zones: str | Iterable[str]) -> list[str]:
        if isinstance(zones, str):
            requested = [zones]
        else:
            requested = list(zones)

        if "all" in requested:
            return list(self._zones.keys())

        expanded: list[str] = []
        for zone_name in requested:
            if zone_name not in self._zones:
                raise ValueError(f"unknown zone: {zone_name}")
            if zone_name not in expanded:
                expanded.append(zone_name)
        return expanded

    def route(self, input_name: str, zones: str | Iterable[str], gain_db: float = 0.0) -> dict[str, dict[str, float]]:
        if input_name not in self._inputs:
            raise ValueError(f"unknown input: {input_name}")

        selected_zones = self._expand_zones(zones)
        for zone_name in selected_zones:
            self.state.setdefault(zone_name, {})
            self.state[zone_name][input_name] = float(gain_db)
        self.save_state()
        return self.state

    def route_many(self, routes: Iterable[dict[str, object]]) -> dict[str, dict[str, float]]:
        for route in routes:
            self.route(
                str(route["input"]),
                route.get("zones", ["all"]),
                float(route.get("gain_db", 0.0)),
            )
        self.save_state()
        return self.state

    @property
    def inputs(self) -> dict[str, list[str]]:
        return self._inputs.copy()

    @property
    def zones(self) -> dict[str, dict[str, object]]:
        return {name: dict(value) for name, value in self._zones.items()}
