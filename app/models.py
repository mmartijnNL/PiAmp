import re
from dataclasses import dataclass, field
from typing import Any, Tuple


def folder_node_name(input_name: str) -> str:
    return "piamp-" + re.sub(r"[^a-z0-9]+", "-", input_name.lower()).strip("-")


@dataclass(frozen=True)
class InputDefinition:
    name: str
    source: str | dict[str, Any] = "pipewire"
    channels: list[int | str] = field(default_factory=list)


def input_folder(input_def: Any) -> str | None:
    source = input_def.source
    if isinstance(source, dict) and source.get("folder"):
        return str(source["folder"])
    return None


def input_device(input_def: Any) -> str:
    """PipeWire node name/description of an input. Folder inputs are played by
    PiAmp itself, through a stream node with a derived name."""
    if input_folder(input_def) is not None:
        return folder_node_name(input_def.name)
    source = input_def.source
    if isinstance(source, dict):
        return str(source.get("device", "unknown-source"))
    return str(source or "unknown-source")


def input_channels(input_def: Any) -> list[int | str]:
    source = input_def.source
    if isinstance(source, dict) and source.get("channels"):
        return list(source["channels"])
    if input_folder(input_def) is not None:
        return [0, 1]
    return list(getattr(input_def, "channels", []) or [])


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
    inputs: Tuple[InputDefinition, ...] = field(default_factory=tuple)
    zones: Tuple[ZoneDefinition, ...] = field(default_factory=tuple)
    routes: Tuple[RouteDefinition, ...] = field(default_factory=tuple)
    silence_threshold_db: float = -50.0
