from __future__ import annotations

from typing import Any

from .models import SystemConfig


class PipeWireAdapter:
    def __init__(self, config: SystemConfig | None = None):
        self.config = config

    def discover_devices(self) -> dict[str, Any]:
        source_name = "UMC1820 Multichannel"
        sinks = [zone.output_device for zone in (self.config.zones if self.config else [])]
        return {
            "source_device": source_name,
            "output_devices": sorted(set(sinks)),
            "inputs": [input_def.name for input_def in (self.config.inputs if self.config else [])],
            "zones": [zone.name for zone in (self.config.zones if self.config else [])],
        }

    def route_plan(self, route_state: dict[str, dict[str, float]]) -> dict[str, Any]:
        return {
            "source_device": "UMC1820 Multichannel",
            "route_state": route_state,
            "note": "The PipeWire layer is intentionally separated from config and routing logic.",
        }
