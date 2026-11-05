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
        output_devices = wpctl_data.get("sinks", [])

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
            output_devices = [zone.output_device for zone in self.config.zones]

        return {
            "source_device": source_device or "unknown-source",
            "output_devices": sorted(set(output_devices)),
            "inputs": [input_def.name for input_def in (self.config.inputs if self.config else [])],
            "zones": [zone.name for zone in (self.config.zones if self.config else [])],
        }

    def route_plan(self, route_state: dict[str, dict[str, float]]) -> dict[str, Any]:
        devices = self.discover_devices()
        return {
            "source_device": devices["source_device"],
            "output_devices": devices["output_devices"],
            "route_state": route_state,
            "note": "The PipeWire layer is intentionally separated from config and routing logic.",
        }
