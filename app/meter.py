from __future__ import annotations

import argparse
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Sequence

import numpy as np

from .models import SystemConfig

BYTES_PER_SAMPLE = 4  # pw-record --format f32
MIN_LEVEL_DB = -60.0
MAX_LEVEL_DB = 0.0
BAR_WIDTH = 50
CPU_TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")


@dataclass(frozen=True)
class StreamSpec:
    name: str
    target: str
    channels: int
    properties: str | None = None
    channel_map: tuple[str, ...] = ()

    def command(self, rate: int, frames: int) -> list[str]:
        command = [
            "pw-record",
            "--target", self.target,
            "--rate", str(rate),
            "--channels", str(self.channels),
            "--format", "f32",
            "--raw",
        ]
        if self.channel_map:
            command += ["--channel-map", ",".join(self.channel_map)]
        if self.properties:
            command += ["--properties", self.properties]
        command.append("-")
        return command


@dataclass(frozen=True)
class MeterEntry:
    """One configured input or zone, shown as a device name plus one bar per
    configured channel (left/right), fed from a capture `StreamSpec`."""

    label: str
    side: str  # "input" or "zone"
    stream_name: str
    indices: tuple[int, ...]


def _normalize_device_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().lower()


def _source_device(source: str | dict[str, Any]) -> str:
    source_cfg = source if isinstance(source, dict) else {"device": source}
    return str(source_cfg.get("device", source or "unknown-source"))


def _source_channels(source: str | dict[str, Any], fallback: list[Any]) -> list[int]:
    source_cfg = source if isinstance(source, dict) else {}
    raw_channels = source_cfg.get("channels", fallback) or []
    return [int(value) for value in raw_channels]


def plan_streams(
    config: SystemConfig,
    source_devices: Iterable[str] = (),
    sink_devices: Iterable[str] = (),
) -> tuple[list[StreamSpec], list[MeterEntry]]:
    """Build one capture `StreamSpec` per distinct device used by the
    configured inputs/zones (grouping entries that share a device), plus the
    `MeterEntry` list describing how to render each configured input/zone
    from those streams.

    A device is captured via its sink monitor (`stream.capture.sink = true`)
    when it is only known as a sink (e.g. a "Built-in Audio Stereo" output
    being monitored as an input source); zones are always sink monitors,
    since a zone is by definition a playback device.
    """
    sources = {_normalize_device_name(name) for name in source_devices}
    sinks = {_normalize_device_name(name) for name in sink_devices}

    # Keyed by (normalized device name, is_sink_capture); tracks the display
    # name and the highest configured channel index so the stream requests
    # just enough channels to cover every entry sharing that device.
    plans: dict[tuple[str, bool], dict[str, Any]] = {}
    pending: list[tuple[MeterEntry, tuple[str, bool]]] = []

    def register(device_name: str, channels: list[int], is_sink: bool) -> tuple[str, bool]:
        normalized = _normalize_device_name(device_name)
        key = (normalized, is_sink)
        plan = plans.setdefault(key, {"device": device_name, "max_index": -1})
        if channels:
            plan["max_index"] = max(plan["max_index"], max(channels))
        return key

    for input_def in config.inputs:
        device_name = _source_device(input_def.source)
        channels = _source_channels(input_def.source, list(input_def.channels))
        normalized = _normalize_device_name(device_name)
        is_sink_capture = normalized in sinks and normalized not in sources
        key = register(device_name, channels, is_sink_capture)
        pending.append((MeterEntry(label=input_def.name, side="input", stream_name="", indices=tuple(channels)), key))

    for zone in config.zones:
        device_name = str(zone.device)
        channels = [int(value) for value in zone.channels]
        key = register(device_name, channels, True)
        pending.append((MeterEntry(label=zone.name, side="zone", stream_name="", indices=tuple(channels)), key))

    streams: list[StreamSpec] = []
    stream_names: dict[tuple[str, bool], str] = {}
    for index, (key, plan) in enumerate(plans.items()):
        _normalized, is_sink_capture = key
        stream_name = f"stream{index}"
        stream_names[key] = stream_name
        streams.append(StreamSpec(
            name=stream_name,
            target=plan["device"],
            channels=max(plan["max_index"] + 1, 2),
            properties="{ stream.capture.sink = true }" if is_sink_capture else None,
        ))

    entries = [replace(entry, stream_name=stream_names[key]) for entry, key in pending]
    return streams, entries


def resolve_streams(streams: Sequence[StreamSpec], output: str) -> list[StreamSpec]:
    """Resolve friendly names to exact nodes and capture their native channels."""
    nodes = [
        obj["info"]["props"]
        for obj in json.loads(output)
        if obj.get("type") == "PipeWire:Interface:Node"
        and (obj.get("info") or {}).get("props", {}).get("media.class") in {"Audio/Source", "Audio/Sink"}
    ]
    resolved: list[StreamSpec] = []
    for spec in streams:
        media_class = "Audio/Sink" if spec.properties else "Audio/Source"
        candidates = [
            node for node in nodes
            if node["media.class"] == media_class
            and _normalize_device_name(spec.target) in {
                _normalize_device_name(str(node.get("node.name", ""))),
                _normalize_device_name(str(node.get("node.description", ""))),
            }
        ]
        if not candidates:
            raise ValueError(f"meter device not found in PipeWire: {spec.target} ({media_class})")
        # Match the routing adapter's first matching node when descriptions repeat.
        node = candidates[0]
        positions = node.get("audio.position", [])
        if isinstance(positions, str):
            positions = positions.strip("[]").replace(",", " ").split()
        channel_map = tuple(positions)
        if not channel_map or len(channel_map) != int(node.get("audio.channels", 0)):
            raise ValueError(f"missing native channel map for meter device: {spec.target}")
        if spec.channels > len(channel_map):
            raise ValueError(f"unavailable configured channel for meter device: {spec.target}")
        properties = "{ stream.dont-remix = true"
        if media_class == "Audio/Sink":
            properties += " stream.capture.sink = true"
        properties += " }"
        resolved.append(replace(
            spec, target=node["node.name"], channels=len(channel_map),
            channel_map=channel_map, properties=properties,
        ))
    return resolved


def _bar_labels(count: int) -> list[str]:
    if count <= 0:
        return []
    if count == 1:
        return ["M"]
    labels = ["L", "R"]
    labels.extend(str(n) for n in range(3, count + 1))
    return labels[:count]


def read_cpu_temp_celsius(path: Path = CPU_TEMP_PATH) -> float | None:
    """Read the SoC temperature from sysfs, in degrees Celsius."""
    try:
        return int(path.read_text().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def levels_from_frame(raw: bytes, channels: int) -> np.ndarray:
    """Compute per-channel RMS levels in dBFS from a raw interleaved f32 PCM block."""
    audio = np.frombuffer(raw, dtype="<f4").reshape(-1, channels)
    rms = np.sqrt(np.mean(audio ** 2, axis=0))
    return 20 * np.log10(np.maximum(rms, 1e-8))


def render_bar(level_db: float, width: int = BAR_WIDTH) -> str:
    """Render a text VU bar for a level clamped to [MIN_LEVEL_DB, MAX_LEVEL_DB]."""
    span = MAX_LEVEL_DB - MIN_LEVEL_DB
    filled = int((level_db - MIN_LEVEL_DB) / span * width)
    return "#" * max(0, min(width, filled))


class StreamReader:
    """Reads fixed-size raw PCM blocks from a pw-record process's stdout and
    publishes per-channel dBFS levels to a single-slot queue, where the
    newest reading always wins over a stale unread one."""

    def __init__(self, spec: StreamSpec, stdout: BinaryIO, frames: int, level_queue: "queue.Queue[np.ndarray]"):
        self.spec = spec
        self.stdout = stdout
        self.frame_bytes = frames * spec.channels * BYTES_PER_SAMPLE
        self.queue = level_queue

    def read_block(self) -> bytes | None:
        data = bytearray()
        while len(data) < self.frame_bytes:
            chunk = self.stdout.read(self.frame_bytes - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def publish(self, levels: np.ndarray) -> None:
        try:
            self.queue.put_nowait(levels)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(levels)

    def run(self) -> None:
        while True:
            block = self.read_block()
            if block is None:
                return
            self.publish(levels_from_frame(block, self.spec.channels))


class TerminalMeter:
    """Live per-input/per-zone level meter rendered to the terminal.

    Configured inputs are shown in a left column and configured zones in a
    right column, each as a device name followed by one bar per configured
    channel (L/R). A fixed-size status block (never growing) follows with
    the CPU temperature and whatever `footer` reports."""

    def __init__(
        self,
        config: SystemConfig | None = None,
        *,
        source_devices: Iterable[str] = (),
        sink_devices: Iterable[str] = (),
        streams: Sequence[StreamSpec] | None = None,
        entries: Sequence[MeterEntry] | None = None,
        rate: int = 48000,
        frames: int = 4800,
        refresh: float = 0.1,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        footer: Callable[[], Sequence[str]] | None = None,
        cpu_temp_path: Path = CPU_TEMP_PATH,
    ):
        if streams is None and entries is None:
            streams, entries = plan_streams(config or SystemConfig(), source_devices, sink_devices)
        self.config = config
        self.footer = footer
        self.streams = list(streams or [])
        self.entries = list(entries or [])
        self.rate = rate
        self.frames = frames
        self.refresh = refresh
        self.popen = popen
        self.cpu_temp_path = cpu_temp_path
        self.processes: list[subprocess.Popen] = []
        self.queues: dict[str, "queue.Queue[np.ndarray]"] = {}
        self.levels: dict[str, np.ndarray | None] = {spec.name: None for spec in self.streams}

    def start(self) -> None:
        if self.config is not None and self.streams:
            from .pipewire import PipeWireAdapter

            self.streams = resolve_streams(self.streams, PipeWireAdapter._default_runner(["pw-dump"]))
        for spec in self.streams:
            proc = self.popen(spec.command(self.rate, self.frames), stdout=subprocess.PIPE, bufsize=0)
            self.processes.append(proc)
            level_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1)
            self.queues[spec.name] = level_queue
            reader = StreamReader(spec, proc.stdout, self.frames, level_queue)
            threading.Thread(target=reader.run, daemon=True).start()

    def _collect_levels(self) -> None:
        for name, level_queue in self.queues.items():
            try:
                self.levels[name] = level_queue.get_nowait()
            except queue.Empty:
                pass

    def _render_entry_column(self, side: str, bar_width: int) -> list[str]:
        column: list[str] = []
        for entry in self.entries:
            if entry.side != side:
                continue
            column.append(entry.label)
            if not entry.indices:
                column.append("(no channels configured)")
                continue
            values = self.levels.get(entry.stream_name)
            for bar_label, channel_index in zip(_bar_labels(len(entry.indices)), entry.indices):
                if values is None:
                    column.append(f"{bar_label} Waiting for audio...")
                    continue
                if channel_index >= len(values):
                    column.append(f"{bar_label}    -- dB |{'':<{bar_width}}|")
                    continue
                level = float(values[channel_index])
                column.append(f"{bar_label} {level:6.0f} dB |{render_bar(level, bar_width):<{bar_width}}|")
        return column

    def render_lines(self, bar_width: int = 20) -> list[str]:
        """Lay the input column and zone column side by side, followed by a
        fixed-size status block, so the frame stays short enough to fit the
        terminal and be redrawn in place without growing over time."""
        columns = [self._render_entry_column("input", bar_width), self._render_entry_column("zone", bar_width)]

        widths = [max((len(line) for line in column), default=0) for column in columns]
        height = max((len(column) for column in columns), default=0)
        lines = []
        for row in range(height):
            cells = [(column[row] if row < len(column) else "").ljust(width) for column, width in zip(columns, widths)]
            lines.append("   ".join(cells).rstrip())

        lines.append("")
        lines.append("--- PiAmp ---")
        cpu_temp = read_cpu_temp_celsius(self.cpu_temp_path)
        lines.append(f"CPU temp: {cpu_temp:.1f}\u00b0C" if cpu_temp is not None else "CPU temp: unavailable")
        if self.footer is not None:
            lines.extend(self.footer())
        return lines

    def render(self) -> str:
        """Return a frame that overwrites the previous one in place."""
        size = shutil.get_terminal_size((80, 24))
        lines = [line[: size.columns - 1] for line in self.render_lines()][: size.lines]
        return "\033[H" + "".join(f"{line}\033[K\n" for line in lines).rstrip("\n") + "\033[J"

    def run(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        self.start()
        # Alternate screen + hidden cursor: the display stays put and the
        # user's scrollback is restored on exit.
        sys.stdout.write("\033[?1049h\033[?25l")
        try:
            while not stop_event.is_set():
                self._collect_levels()
                sys.stdout.write(self.render())
                sys.stdout.flush()
                stop_event.wait(self.refresh)
        finally:
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()
            self.stop()

    def stop(self) -> None:
        for proc in self.processes:
            proc.terminate()
        for proc in self.processes:
            proc.wait()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PiAmp terminal audio level meter")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--rate", type=int, default=48000, help="Sample rate to request from pw-record")
    parser.add_argument("--frames", type=int, default=4800, help="Frames per analysis block")
    parser.add_argument("--refresh", type=float, default=0.1, help="Screen refresh interval in seconds")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    from .config import load_config
    from .pipewire import PipeWireAdapter

    config = load_config(args.config)
    devices = PipeWireAdapter(config=config).discover_devices()
    meter = TerminalMeter(
        config=config,
        source_devices=devices.get("source_devices", []),
        sink_devices=devices.get("output_devices", []),
        rate=args.rate,
        frames=args.frames,
        refresh=args.refresh,
    )
    try:
        meter.run()
    except KeyboardInterrupt:
        print("PiAmp meter stopped.", flush=True)


if __name__ == "__main__":
    main()
