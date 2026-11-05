from dataclasses import dataclass, field
from typing import Any, Tuple


@dataclass(frozen=True)
class InputDefinition:
    name: str
    source: str | dict[str, Any] = "pipewire"
    channels: list[int | str] = field(default_factory=list)


@dataclass(frozen=True)
class ZoneDefinition:
    name: str
    device: str = "default"
    channels: list[int | str] = field(default_factory=list)


@dataclass(frozen=True)
class RouteDefinition:
    input: str
    zones: list[str]
    gain_db: float = 0.0


@dataclass(frozen=True)
class SystemConfig:
    name: str
    inputs: Tuple[InputDefinition, ...] = field(default_factory=tuple)
    zones: Tuple[ZoneDefinition, ...] = field(default_factory=tuple)
    routes: Tuple[RouteDefinition, ...] = field(default_factory=tuple)
