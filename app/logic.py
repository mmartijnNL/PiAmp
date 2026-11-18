from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias, Union


@dataclass(frozen=True)
class TimedRoute:
    gain_db: float
    start_time: str
    end_time: str

    def active_at(self, clock: str) -> bool:
        if self.start_time < self.end_time:
            return self.start_time <= clock < self.end_time
        return clock >= self.start_time or clock < self.end_time


RouteMap: TypeAlias = dict[str, Union[float, TimedRoute, "RouteMap"]]
RouteState: TypeAlias = dict[str, RouteMap]
ResolvedRoutes: TypeAlias = dict[str, dict[str, float]]
SILENCE_SECONDS = 5.0


def parse_route_state(raw: object) -> RouteState:
    def parse_gain(value: object, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"route gain for {name} must be a finite number")
        gain = float(value)
        if not math.isfinite(gain) or gain > 20:
            raise ValueError("route gains must be finite and at most +20 dB")
        return gain

    def parse_clock(value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            raise ValueError('StartTime and EndTime must be quoted local times in "HH:MM" format')
        return value

    def parse_map(value: object) -> RouteMap:
        if not isinstance(value, dict):
            raise ValueError("routes must map zone names to input/gain mappings or AND/OR groups")
        result: RouteMap = {}
        for name, child in value.items():
            if not isinstance(name, str) or not name:
                raise ValueError("route input and operator names must be nonempty strings")
            if name in {"AND", "OR"}:
                children = parse_map(child)
                if name == "OR" and not children:
                    raise ValueError("OR groups must contain at least one branch")
                result[name] = children
            else:
                if isinstance(child, dict):
                    if set(child) != {"Gain", "StartTime", "EndTime"}:
                        raise ValueError(f"timed route for {name} must contain Gain, StartTime, and EndTime")
                    start = parse_clock(child["StartTime"])
                    end = parse_clock(child["EndTime"])
                    if start == end:
                        raise ValueError("StartTime and EndTime must differ")
                    result[name] = TimedRoute(parse_gain(child["Gain"], name), start, end)
                else:
                    result[name] = parse_gain(child, name)
        return result

    if not isinstance(raw, dict):
        raise ValueError("routes must map zone names to input/gain mappings")
    if any(not isinstance(zone, str) or not zone for zone in raw):
        raise ValueError("route zone names must be nonempty strings")
    return {zone: parse_map(value) for zone, value in raw.items()}


def uses_fallback(routes: RouteState) -> bool:
    def contains_or(mapping: RouteMap) -> bool:
        return any(name == "OR" or (isinstance(value, dict) and contains_or(value))
                   for name, value in mapping.items())
    return any(contains_or(mapping) for mapping in routes.values())


class RouteSelector:
    def __init__(self, threshold_db: float = -50.0):
        self.threshold_db = threshold_db
        self._selected: dict[tuple[str, ...], int] = {}
        self._silent_since: dict[tuple[str, ...], float] = {}
        self._available: dict[tuple[str, ...], bool] = {}

    def resolve(self, routes: RouteState, levels: Mapping[str, float], now: float) -> ResolvedRoutes:
        clock = datetime.now().strftime("%H:%M")

        def group(mapping: RouteMap, operator: str, path: tuple[str, ...]) -> tuple[dict[str, float], bool | None]:
            branches: list[tuple[dict[str, float], bool | None]] = []
            keys: list[tuple[str, ...]] = []
            for name, value in mapping.items():
                key = (*path, name)
                keys.append(key)
                if isinstance(value, dict):
                    branches.append(group(value, name, key))
                elif isinstance(value, TimedRoute) and not value.active_at(clock):
                    branches.append(({}, False))
                else:
                    level = levels.get(name)
                    audible = None if level is None or not math.isfinite(level) else level >= self.threshold_db
                    gain = value.gain_db if isinstance(value, TimedRoute) else value
                    branches.append(({name: gain}, audible))
            if operator == "OR":
                available = [index for index, (inputs, _) in enumerate(branches) if inputs]
                newly_available = {
                    index for index in available if self._available.get(keys[index]) is False
                }
                for key, (inputs, audible) in zip(keys, branches):
                    self._available[key] = bool(inputs)
                    if inputs and audible is False:
                        self._silent_since.setdefault(key, now)
                    else:
                        self._silent_since.pop(key, None)
                if not available:
                    self._selected.pop(path, None)
                    return {}, False
                selected = self._selected.get(path, 0)
                if selected not in available:
                    selected = available[0]
                for index in available:
                    if index >= selected:
                        break
                    if branches[index][1] is True or index in newly_available:
                        selected = index
                        break
                if branches[selected][1] is False and now - self._silent_since[keys[selected]] >= SILENCE_SECONDS:
                    for index in available:
                        if index <= selected:
                            continue
                        selected = index
                        since = self._silent_since.get(keys[selected])
                        if since is None or now - since < SILENCE_SECONDS:
                            break
                self._selected[path] = selected
                return branches[selected]
            mixed: dict[str, float] = {}
            for inputs, _ in branches:
                for name, gain in inputs.items():
                    if name in mixed and mixed[name] != gain:
                        raise ValueError(f"input {name} has conflicting gains in an AND mix")
                    mixed[name] = gain
            audible = True if any(status is True for _, status in branches) else (
                None if any(status is None for _, status in branches) else False
            )
            return mixed, audible

        return {zone: group(mapping, "AND", (zone,))[0] for zone, mapping in routes.items()}
