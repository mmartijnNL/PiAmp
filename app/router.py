from __future__ import annotations

from pathlib import Path
import time
from typing import Iterable

from .models import SystemConfig
from .state_store import StateStore
from .logic import RouteMap, RouteState, RouteSelector, ResolvedRoutes, uses_fallback


class RouteManager:
    def __init__(self, config: SystemConfig | None = None, state_path: str | Path | None = None, *, persist_state: bool = True, strict_state: bool = False):
        self._inputs: dict[str, list[str]] = {}
        self._zones: dict[str, dict[str, object]] = {}
        self.state: dict[str, dict[str, float]] = {}
        self.routes: RouteState = {}
        self.state_store = StateStore(state_path)

        if config is not None:
            for input_def in config.inputs:
                self.add_input(input_def.name, list(input_def.channels))
            for zone_def in config.zones:
                self.add_zone(zone_def.name, zone_def.device, list(zone_def.channels))

        self.routes = self.state_store.load(strict=strict_state)
        self.selector = RouteSelector(getattr(config, "silence_threshold_db", -50.0))
        self._levels: dict[str, float] = {}
        if config is not None:
            def filter_inputs(mapping: RouteMap, inside_logic: bool = False) -> RouteMap:
                if inside_logic:
                    unknown = set(mapping) - {"AND", "OR"} - set(self._inputs)
                    if unknown:
                        raise ValueError("unknown input(s) in route logic: " + ", ".join(sorted(unknown)))
                filtered = {
                    name: filter_inputs(value, True) if isinstance(value, dict) else value
                    for name, value in mapping.items()
                    if name in {"AND", "OR"} or name in self._inputs
                }
                if "OR" in filtered and not filtered["OR"]:
                    raise ValueError("OR groups must contain a configured input")
                return filtered
            filtered_state: dict[str, RouteMap] = {}
            for zone_name, route_map in self.routes.items():
                if zone_name not in self._zones:
                    continue
                filtered_state[zone_name] = filter_inputs(route_map)

            self.routes = filtered_state
            for zone_name in self._zones:
                self.routes.setdefault(zone_name, {})

            if persist_state:
                self.save_state()
        self.state = self.resolve({})

    def add_input(self, name: str, channels: list[str] | None = None) -> None:
        self._inputs[name] = list(channels or [])

    def add_zone(self, name: str, output_device: str, output_channels: list[str] | None = None) -> None:
        self._zones[name] = {
            "output_device": output_device,
            "output_channels": list(output_channels or []),
        }
        self.state.setdefault(name, {})
        self.routes.setdefault(name, {})

    def save_state(self) -> None:
        self.state_store.save(self.routes)

    @property
    def needs_levels(self) -> bool:
        return uses_fallback(self.routes)

    def resolve(self, levels: dict[str, float], now: float | None = None) -> ResolvedRoutes:
        self._levels = levels
        return self.selector.resolve(self.routes, levels, time.monotonic() if now is None else now)

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
            self.routes.setdefault(zone_name, {})
            self.routes[zone_name][input_name] = float(gain_db)
        self.state = self.resolve(self._levels)
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
