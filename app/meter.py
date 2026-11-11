from __future__ import annotations

import argparse
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import BinaryIO, Callable, Sequence

import numpy as np

BYTES_PER_SAMPLE = 4  # pw-record --format f32
MIN_LEVEL_DB = -60.0
MAX_LEVEL_DB = 0.0
BAR_WIDTH = 50


@dataclass(frozen=True)
class StreamSpec:
    name: str
    target: str
    channels: int
    properties: str | None = None

    def command(self, rate: int, frames: int) -> list[str]:
        command = [
            "pw-record",
            "--target", self.target,
            "--rate", str(rate),
            "--channels", str(self.channels),
            "--format", "f32",
            "--raw",
        ]
        if self.properties:
            command += ["--properties", self.properties]
        command.append("-")
        return command


# Defaults match the UMC1820's multichannel capture/playback nodes: 10 input
# channels (8 analog in + S/PDIF) and 12 output channels (8 analog out + 4
# monitor sends). `stream.capture.sink = true` makes pw-record tap the
# sink's monitor ports instead of trying to capture a playback stream.
DEFAULT_STREAMS: tuple[StreamSpec, ...] = (
    StreamSpec(
        name="INPUT",
        target="alsa_input.usb-BEHRINGER_UMC1820_7A122C9D-00.multichannel-input",
        channels=10,
    ),
    StreamSpec(
        name="OUTPUT",
        target="alsa_output.usb-BEHRINGER_UMC1820_7A122C9D-00.multichannel-output",
        channels=12,
        properties="{ stream.capture.sink = true }",
    ),
)


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
    """Live UMC1820 input/output level meter rendered to the terminal."""

    def __init__(
        self,
        streams: Sequence[StreamSpec] = DEFAULT_STREAMS,
        rate: int = 48000,
        frames: int = 4800,
        refresh: float = 0.1,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        footer: Callable[[], Sequence[str]] | None = None,
    ):
        self.footer = footer
        self.streams = list(streams)
        self.rate = rate
        self.frames = frames
        self.refresh = refresh
        self.popen = popen
        self.processes: list[subprocess.Popen] = []
        self.queues: dict[str, "queue.Queue[np.ndarray]"] = {}
        self.levels: dict[str, np.ndarray | None] = {spec.name: None for spec in self.streams}

    def start(self) -> None:
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

    def render_lines(self, bar_width: int = 20) -> list[str]:
        """Lay streams out side by side so the frame stays short enough to
        fit the terminal and be redrawn in place."""
        columns: list[list[str]] = []
        for spec in self.streams:
            column = [f"--- UMC1820 {spec.name} ---"]
            values = self.levels[spec.name]
            if values is None:
                column.append("Waiting for audio...")
            else:
                for index, level in enumerate(values):
                    column.append(f"{index + 1:02} {level:6.0f} dB |{render_bar(float(level), bar_width):<{bar_width}}|")
            columns.append(column)

        widths = [max(len(line) for line in column) for column in columns]
        height = max(len(column) for column in columns)
        lines = []
        for row in range(height):
            cells = [(column[row] if row < len(column) else "").ljust(width) for column, width in zip(columns, widths)]
            lines.append("   ".join(cells).rstrip())
        if self.footer is not None:
            lines.append("")
            lines.append("--- PiAmp ---")
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
    parser.add_argument("--rate", type=int, default=48000, help="Sample rate to request from pw-record")
    parser.add_argument("--frames", type=int, default=4800, help="Frames per analysis block")
    parser.add_argument("--refresh", type=float, default=0.1, help="Screen refresh interval in seconds")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    meter = TerminalMeter(rate=args.rate, frames=args.frames, refresh=args.refresh)
    try:
        meter.run()
    except KeyboardInterrupt:
        print("PiAmp meter stopped.", flush=True)


if __name__ == "__main__":
    main()
