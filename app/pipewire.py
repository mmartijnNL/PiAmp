from __future__ import annotations

import re
import subprocess
from typing import Any, Callable, Sequence

from .models import SystemConfig


class PipeWireAdapter:
    def __init__(self, config: SystemConfig | None = None, runner: Callable[[Sequence[str]], str] | None = None):
        self.config = config
        self.runner = runner or self._default_runner

    @staticmethod
    def _default_runner(command: Sequence[str]) -> str:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return result.stdout or result.stderr or ""

    @staticmethod
    def _normalize_device_name(name: str) -> str:
        return re.sub(r"\s+", " ", name or "").strip().lower()

    @staticmethod
    def _parse_wpctl_audio(output: str) -> dict[str, list[str]]:
        parsed: dict[str, list[str]] = {"sinks": [], "sources": []}
        section: str | None = None

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.endswith(":"):
                section = line[:-1].lower()
                continue
            match = re.search(r"(?:\*\s*)?(?:\d+\.)\s*(.+)", line)
            if match and section in {"sinks", "sources"}:
                parsed[section].append(match.group(1).strip())

        return parsed

    @staticmethod
    def _parse_pw_cli_nodes(output: str) -> list[dict[str, str]]:
        nodes: list[dict[str, str]] = []
        current: dict[str, str] | None = None

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("id "):
                if current:
                    nodes.append(current)
                current = {}
            elif current is not None and line.startswith("node.name"):
                match = re.search(r'node\.name\s*=\s*"([^"]+)"', line)
                if match:
                    current["name"] = match.group(1)
            elif current is not None and line.startswith("media.class"):
                match = re.search(r'media\.class\s*=\s*"([^"]+)"', line)
                if match:
                    current["media_class"] = match.group(1)

        if current:
            nodes.append(current)

        return nodes

    def discover_devices(self) -> dict[str, Any]:
        wpctl_output = self.runner(["wpctl", "list", "audio"])
        wpctl_data = self._parse_wpctl_audio(wpctl_output)

        source_device = None
        output_devices = list(wpctl_data.get("sinks", []))

        if wpctl_data.get("sources"):
            source_device = wpctl_data["sources"][0]

        if source_device is None:
            pw_output = self.runner(["pw-cli", "ls", "Node"])
            for node in self._parse_pw_cli_nodes(pw_output):
                if node.get("media_class") == "Audio/Source":
                    source_device = node.get("name")
                    break

        if not output_devices:
            pw_output = self.runner(["pw-cli", "ls", "Node"])
            for node in self._parse_pw_cli_nodes(pw_output):
                if node.get("media_class") == "Audio/Sink":
                    output_devices.append(node.get("name", "unknown-sink"))

        if self.config is not None and not output_devices:
            output_devices = [zone.device for zone in self.config.zones]

        return {
            "source_device": source_device or "unknown-source",
            "output_devices": sorted(set(output_devices)),
            "inputs": [input_def.name for input_def in (self.config.inputs if self.config else [])],
            "zones": [zone.name for zone in (self.config.zones if self.config else [])],
        }

    def resolve_configured_devices(self) -> dict[str, dict[str, Any]]:
        if self.config is None:
            return {}

        live = self.discover_devices()
        live_lookup = {
            self._normalize_device_name(name): name
            for name in [*live.get("output_devices", []), live.get("source_device", "")]
            if name
        }

        resolved: dict[str, dict[str, Any]] = {}

        for input_def in self.config.inputs:
            source_cfg = input_def.source if isinstance(input_def.source, dict) else {"device": input_def.source}
            device_name = str(source_cfg.get("device", input_def.source or "unknown-source"))
            normalized = self._normalize_device_name(device_name)
            resolved[input_def.name] = {
                "device": live_lookup.get(normalized, device_name),
                "channels": list(source_cfg.get("channels", getattr(input_def, "channels", []))),
            }

        for zone in self.config.zones:
            device_name = str(getattr(zone, "device", "default"))
            normalized = self._normalize_device_name(device_name)
            resolved[zone.name] = {
                "device": live_lookup.get(normalized, device_name),
                "channels": list(getattr(zone, "channels", [])),
            }

        return resolved

    def validate_configured_devices(self) -> dict[str, Any]:
        if self.config is None:
            return {"ok": True, "missing_devices": []}

        live = self.discover_devices()
        live_lookup = {
            self._normalize_device_name(name): name
            for name in [*live.get("output_devices", []), live.get("source_device", "")]
            if name
        }

        missing: list[str] = []

        for input_def in self.config.inputs:
            source_cfg = input_def.source if isinstance(input_def.source, dict) else {"device": input_def.source}
            device_name = str(source_cfg.get("device", input_def.source or "unknown-source"))
            if self._normalize_device_name(device_name) not in live_lookup:
                missing.append(device_name)

        for zone in self.config.zones:
            device_name = str(getattr(zone, "device", "default"))
            if self._normalize_device_name(device_name) not in live_lookup:
                missing.append(device_name)

        return {
            "ok": not missing,
            "missing_devices": sorted(set(missing)),
        }

    def route_plan(self, route_state: dict[str, dict[str, float]]) -> dict[str, Any]:
        devices = self.discover_devices()
        return {
            "source_device": devices["source_device"],
            "output_devices": devices["output_devices"],
            "route_state": route_state,
            "note": "The PipeWire layer is intentionally separated from config and routing logic.",
        }
