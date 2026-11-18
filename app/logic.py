from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TypeAlias, Union

RouteMap: TypeAlias = dict[str, Union[float, "RouteMap"]]
RouteState: TypeAlias = dict[str, RouteMap]
ResolvedRoutes: TypeAlias = dict[str, dict[str, float]]
SILENCE_SECONDS = 5.0


def parse_route_state(raw: object) -> RouteState:
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
                if isinstance(child, (bool, dict, list)) or child is None:
                    raise ValueError(f"route gain for {name} must be a finite number")
                gain = float(child)
                if not math.isfinite(gain) or gain > 20:
                    raise ValueError("route gains must be finite and at most +20 dB")
                result[name] = gain
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

    def resolve(self, routes: RouteState, levels: Mapping[str, float], now: float) -> ResolvedRoutes:
        def group(mapping: RouteMap, operator: str, path: tuple[str, ...]) -> tuple[dict[str, float], bool | None]:
            branches: list[tuple[dict[str, float], bool | None]] = []
            keys: list[tuple[str, ...]] = []
            for name, value in mapping.items():
                key = (*path, name)
                keys.append(key)
                if isinstance(value, dict):
                    branches.append(group(value, name, key))
                else:
                    level = levels.get(name)
                    audible = None if level is None or not math.isfinite(level) else level >= self.threshold_db
                    branches.append(({name: value}, audible))
            if operator == "OR":
                for key, (_, audible) in zip(keys, branches):
                    if audible is False:
                        self._silent_since.setdefault(key, now)
                    else:
                        self._silent_since.pop(key, None)
                selected = self._selected.get(path, 0)
                for index in range(selected):
                    if branches[index][1] is True:
                        selected = index
                        break
                if branches[selected][1] is False and now - self._silent_since[keys[selected]] >= SILENCE_SECONDS:
                    while selected < len(branches) - 1:
                        selected += 1
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
