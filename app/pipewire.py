from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .models import SystemConfig, input_channels, input_device, input_folder


class PipeWireAdapter:
    def __init__(self, config: SystemConfig | None = None, runner: Callable[[Sequence[str]], str] | None = None):
        self.config = config
        self.runner = runner or self._default_runner

    @staticmethod
    def _default_runner(command: Sequence[str]) -> str:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"{' '.join(command)} failed (exit {result.returncode}): {detail}")
        return result.stdout or result.stderr or ""

    @staticmethod
    def _normalize_device_name(name: str) -> str:
        return re.sub(r"\s+", " ", name or "").strip().lower()

    @staticmethod
    def _iter_wpctl_audio_entries(output: str):
        """Yield (section, id, name) for entries under the Audio > Sinks/Sources
        sections of `wpctl status` output.

        `wpctl status` groups devices under top-level categories (Audio, Video,
        Settings) that are not indented, and draws each category's sections
        (Devices, Sinks, Sources, ...) using box-drawing tree characters. We
        track the current category so that e.g. Video's "Sources:" section
        (camera devices) is never confused with Audio's "Sources:" section.
        """
        category: str | None = None
        section: str | None = None

        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if not raw_line[:1].isspace():
                category = line
                section = None
                continue

            if category != "Audio":
                continue

            stripped = re.sub(r"^[\s│├└─]+", "", line)

            section_match = re.match(r"^(Devices|Sinks|Sources|Filters|Streams):\s*$", stripped)
            if section_match:
                section = section_match.group(1).lower()
                continue

            if section not in {"sinks", "sources"}:
                continue

            entry_match = re.match(r"^(?:\*\s*)?(\d+)\.\s*(.+)$", stripped)
            if entry_match:
                name = re.sub(r"\s*\[[^\]]*\]\s*$", "", entry_match.group(2)).strip()
                yield section, int(entry_match.group(1)), name

    @staticmethod
    def _parse_wpctl_audio(output: str) -> dict[str, list[str]]:
        parsed: dict[str, list[str]] = {"sinks": [], "sources": []}
        for section, _node_id, name in PipeWireAdapter._iter_wpctl_audio_entries(output):
            parsed[section].append(name)
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

    @staticmethod
    def _parse_pw_dump(output: str) -> tuple[dict[int, dict[str, Any]], list[tuple[int, int]]]:
        """Parse `pw-dump` JSON into (audio nodes, existing links).

        Nodes are keyed by node id, each with its ports indexed by direction
        ("in"/"out") and channel number. `port.id` in PipeWire is 0-based *per
        node and per direction* (e.g. a stereo sink has playback ports 0/1 and
        monitor ports 0/1), which lines up directly with the 0-based
        `channels` indices used in config.yaml. The port object's own
        top-level `id` is what `pw-link` needs to actually create a link
        (node ids and port channel numbers are not valid `pw-link` arguments).

        Links are returned as (output_port_id, input_port_id) pairs so
        `apply_routes` can detect and remove stale links left over from a
        previous run (e.g. after a zone's configured channels changed).
        """
        try:
            objects = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return {}, []

        if not isinstance(objects, list):
            return {}, []

        nodes: dict[int, dict[str, Any]] = {}
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Node":
                continue
            props = ((obj.get("info") or {}).get("props")) or {}
            media_class = props.get("media.class")
            if media_class not in {"Audio/Sink", "Audio/Source", "Stream/Output/Audio"}:
                continue
            nodes[obj["id"]] = {
                "node_id": obj["id"],
                "name": props.get("node.description") or props.get("node.name") or "",
                "media_class": media_class,
                "ports": {"in": {}, "out": {}},
                "gain_zone": props.get("piamp.gain.zone"),
                "channel_volumes": next(
                    (param["channelVolumes"] for param in (obj.get("info") or {}).get("params", {}).get("Props", [])
                     if "channelVolumes" in param), None
                ),
            }

        for obj in objects:
            if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Port":
                continue
            props = ((obj.get("info") or {}).get("props")) or {}
            node = nodes.get(props.get("node.id"))
            direction = props.get("port.direction")
            channel = props.get("port.id")
            if node is None or direction not in {"in", "out"} or channel is None:
                continue
            node["ports"][direction][int(channel)] = obj["id"]

        links: list[tuple[int, int]] = []
        for obj in objects:
            if not isinstance(obj, dict) or obj.get("type") != "PipeWire:Interface:Link":
                continue
            info = obj.get("info") or {}
            output_port = info.get("output-port-id")
            input_port = info.get("input-port-id")
            if output_port is None or input_port is None:
                continue
            links.append((int(output_port), int(input_port)))

        return nodes, links

    def discover_devices(self) -> dict[str, Any]:
        nodes, _links = self._parse_pw_dump(self.runner(["pw-dump"]))
        if nodes:
            source_devices = [node["name"] for node in nodes.values() if node["media_class"] == "Audio/Source"]
            return {
                "source_device": source_devices[0] if source_devices else "unknown-source",
                "source_devices": sorted(set(source_devices)),
                "output_devices": sorted({node["name"] for node in nodes.values() if node["media_class"] == "Audio/Sink"}),
                "inputs": [input_def.name for input_def in (self.config.inputs if self.config else [])],
                "zones": [zone.name for zone in (self.config.zones if self.config else [])],
            }

        wpctl_output = self.runner(["wpctl", "status"])
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
            for name in [*live.get("output_devices", []), *live.get("source_devices", []), live.get("source_device", "")]
            if name
        }

        resolved: dict[str, dict[str, Any]] = {}

        for input_def in self.config.inputs:
            device_name = input_device(input_def)
            normalized = self._normalize_device_name(device_name)
            resolved[input_def.name] = {
                "device": live_lookup.get(normalized, device_name),
                "channels": input_channels(input_def),
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
            for name in [*live.get("output_devices", []), *live.get("source_devices", []), live.get("source_device", "")]
            if name
        }

        missing: list[str] = []

        for input_def in self.config.inputs:
            if input_folder(input_def) is not None:
                if not Path(input_folder(input_def)).expanduser().is_dir():
                    missing.append(input_folder(input_def))
                continue
            device_name = input_device(input_def)
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

    @staticmethod
    def _gain_zone(zone_name: str) -> str:
        return hashlib.sha256(zone_name.encode()).hexdigest()

    @staticmethod
    def _gain_name(zone_name: str, input_name: str) -> str:
        key = json.dumps([zone_name, input_name]).encode()
        return "piamp-route-" + hashlib.sha256(key).hexdigest()

    def _create_gain_node(self, name: str, zone: str, channels: int) -> dict[str, Any]:
        positions = " ".join(f"AUX{index}" for index in range(channels))
        self.runner(["pw-cli", "create-node", "adapter",
                     "{ factory.name = support.null-audio-sink "
                     f"node.name = {name} node.description = {name} "
                     f"piamp.gain.zone = {self._gain_zone(zone)} "
                     f"audio.position = [ {positions} ] "
                     "media.class = Audio/Sink object.linger = true "
                     "node.virtual = true priority.session = 0 node.autoconnect = false "
                     "monitor.channel-volumes = true }"])
        for _ in range(40):
            live_nodes, _ = self._parse_pw_dump(self.runner(["pw-dump"]))
            node = next((node for node in live_nodes.values() if node["name"] == name), None)
            if node is not None and len(node["ports"]["in"]) == channels and len(node["ports"]["out"]) == channels:
                return node
            time.sleep(0.05)
        raise RuntimeError(f"PipeWire gain stage did not become ready: {name}")

    def apply_routes(self, route_state: dict[str, dict[str, float]], dry_run: bool = False) -> dict[str, Any]:
        if self.config is None:
            return {"applied": []}

        nodes, existing_links = self._parse_pw_dump(self.runner(["pw-dump"]))
        nodes_by_name: dict[str, list[dict[str, Any]]] = {}
        for node in nodes.values():
            nodes_by_name.setdefault(self._normalize_device_name(node["name"]), []).append(node)

        def resolve_node(device_name: str, preferred_class: str) -> dict[str, Any] | None:
            candidates = nodes_by_name.get(self._normalize_device_name(device_name), [])
            if not candidates:
                return None
            preferred = [n for n in candidates if n["media_class"] == preferred_class]
            # Fall back to any matching node so e.g. a Sink's monitor ports can
            # be used as an input device (capturing whatever apps play through it).
            return (preferred or candidates)[0]

        applied: list[dict[str, Any]] = []
        desired_links: set[tuple[int, int]] = set()
        managed_port_ids: set[int] = set()
        for zone_name, input_map in route_state.items():
            zone = next((item for item in self.config.zones if item.name == zone_name), None)
            if zone is None:
                continue

            zone_node = resolve_node(str(zone.device), "Audio/Sink")
            if zone_node is None:
                raise ValueError(f"zone device not found in PipeWire: {zone.device}")
            zone_channels = list(getattr(zone, "channels", None) or [0, 1])
            managed_port_ids.update(zone_node["ports"]["in"].values())

            for input_name, gain_db in input_map.items():
                gain_db = float(gain_db)
                if not math.isfinite(gain_db) or gain_db > 20:
                    raise ValueError("route gains must be finite and at most +20 dB")
                input_def = next((item for item in self.config.inputs if item.name == input_name), None)
                if input_def is None:
                    continue

                source_name = input_device(input_def)
                source_node = resolve_node(source_name, "Audio/Source")
                if source_node is None:
                    raise ValueError(f"input device not found in PipeWire: {source_name}")

                in_channels = input_channels(input_def) or [0, 1]

                link_pairs: list[tuple[int, int]] = []
                for in_channel, out_channel in zip(in_channels, zone_channels):
                    src_port = source_node["ports"]["out"].get(int(in_channel))
                    dst_port = zone_node["ports"]["in"].get(int(out_channel))
                    if src_port is None or dst_port is None:
                        raise ValueError(
                            f"unavailable channel for {input_name} -> {zone_name}: "
                            f"{source_name} output {in_channel}, {zone.device} input {out_channel}"
                        )
                    link_pairs.append((src_port, dst_port))
                    desired_links.add((src_port, dst_port))

                applied.append({
                    "input": input_name,
                    "zone": zone_name,
                    "source": source_name,
                    "sink": str(zone.device),
                    "source_id": str(source_node["node_id"]),
                    "sink_id": str(zone_node["node_id"]),
                    "links": [
                        {"source_port": str(src_port), "sink_port": str(dst_port)}
                        for src_port, dst_port in link_pairs
                    ],
                    "gain_db": float(gain_db),
                })

        if not dry_run:
            managed_zones = {self._gain_zone(zone) for zone in route_state}
            old_gain_nodes = [node for node in nodes.values() if node["gain_zone"] in managed_zones]
            for node in old_gain_nodes:
                managed_port_ids.update(node["ports"]["in"].values())

            active_gain_names: set[str] = set()
            gain_settings: dict[int, tuple[int, float]] = {}
            # Hardware sink volume is shared; apply route gain before mixing.
            for route in applied:
                if route["gain_db"] == 0:
                    continue
                name = self._gain_name(route["zone"], route["input"])
                pairs = [(int(link["source_port"]), int(link["sink_port"])) for link in route["links"]]
                stage = next((node for node in nodes.values() if node["name"] == name), None)
                if stage is None or len(stage["ports"]["in"]) != len(pairs) or len(stage["ports"]["out"]) != len(pairs):
                    if stage is not None:
                        self.runner(["pw-cli", "destroy", str(stage["node_id"])])
                        ports = set(stage["ports"]["in"].values()) | set(stage["ports"]["out"].values())
                        existing_links = [(src, dst) for src, dst in existing_links if src not in ports and dst not in ports]
                    stage = self._create_gain_node(name, route["zone"], len(pairs))
                active_gain_names.add(name)
                linear_gain = 10 ** (route["gain_db"] / 20)
                volumes = " ".join(f"{linear_gain:.12g}" for _ in pairs)
                self.runner(["pw-cli", "set-param", str(stage["node_id"]), "Props",
                             f"{{ volume = 1.0 mute = false channelVolumes = [ {volumes} ] }}"])
                gain_settings[stage["node_id"]] = (len(pairs), linear_gain)
                desired_links.difference_update(pairs)
                stage_links = []
                for channel, (source_port, sink_port) in enumerate(pairs):
                    stage_links.extend([
                        (source_port, stage["ports"]["in"][channel]),
                        (stage["ports"]["out"][channel], sink_port),
                    ])
                desired_links.update(stage_links)
                route["links"] = [
                    {"source_port": str(src), "sink_port": str(dst)} for src, dst in stage_links
                ]

            # Zones can share a multichannel sink. Reconcile their combined
            # plan so one zone never deletes another zone's desired links.
            for out_port, in_port in existing_links:
                if in_port in managed_port_ids and (out_port, in_port) not in desired_links:
                    self.runner(["pw-link", "-d", str(out_port), str(in_port)])
            for src_port, dst_port in sorted(desired_links - set(existing_links)):
                self.runner(["pw-link", str(src_port), str(dst_port)])

            if desired_links:
                live_nodes, live_links = self._parse_pw_dump(self.runner(["pw-dump"]))
                missing_links = desired_links - set(live_links)
                if missing_links:
                    raise RuntimeError(f"PipeWire did not retain requested links: {sorted(missing_links)}")
                for node_id, (channels, gain) in gain_settings.items():
                    actual = live_nodes.get(node_id, {}).get("channel_volumes") or []
                    if len(actual) != channels or not all(math.isclose(value, gain, rel_tol=1e-4, abs_tol=1e-12) for value in actual):
                        raise RuntimeError(f"PipeWire did not retain requested gain for node {node_id}")
            for node in old_gain_nodes:
                if node["name"] not in active_gain_names:
                    self.runner(["pw-cli", "destroy", str(node["node_id"])])

        return {"applied": applied, "dry_run": dry_run}

    def route_plan(self, route_state: dict[str, dict[str, float]]) -> dict[str, Any]:
        devices = self.discover_devices()
        return {
            "source_device": devices["source_device"],
            "output_devices": devices["output_devices"],
            "route_state": route_state,
            "note": "The PipeWire layer is intentionally separated from config and routing logic.",
        }
