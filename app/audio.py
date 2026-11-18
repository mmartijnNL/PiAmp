from __future__ import annotations

import subprocess
import queue
import threading
import time
from typing import BinaryIO

import numpy as np

from .models import SystemConfig
from .pipewire import PipeWireAdapter
from .ui import StreamReader, StreamSpec, levels_from_frame, plan_streams, resolve_streams


class InputLevelMonitor:
    """Capture pre-gain inputs even when the terminal UI is disabled."""

    def __init__(self, config: SystemConfig):
        self.config = config
        self.processes: list[subprocess.Popen] = []
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._latest: dict[str, np.ndarray] = {}
        self._pending: dict[str, np.ndarray] = {}
        self._updated: dict[str, float] = {}
        self._errors: dict[str, str] = {}

    def start(self) -> None:
        adapter = PipeWireAdapter(self.config)
        devices = adapter.discover_devices()
        streams, self.entries = plan_streams(
            SystemConfig(inputs=self.config.inputs),
            devices.get("source_devices", []), devices.get("output_devices", []),
        )
        self.streams = resolve_streams(streams, adapter.runner(["pw-dump"]))
        try:
            for spec in self.streams:
                proc = subprocess.Popen(spec.command(48000, 4800), stdout=subprocess.PIPE, bufsize=0)
                self.processes.append(proc)
                if proc.stdout is None:
                    raise RuntimeError(f"input capture has no output: {spec.target}")
                self._updated[spec.name] = time.monotonic()
                thread = threading.Thread(target=self._read, args=(spec, proc.stdout), daemon=True)
                self._threads.append(thread)
                thread.start()
        except (OSError, RuntimeError):
            self.stop()
            raise

    def _read(self, spec: StreamSpec, stdout: BinaryIO) -> None:
        # Preserve brief activity between routing ticks rather than dropping it
        # like a display's latest-frame queue.
        reader = StreamReader(spec, stdout, 4800, queue.Queue(maxsize=1))
        try:
            while not self._stop.is_set():
                block = reader.read_block()
                if block is None:
                    if not self._stop.is_set():
                        with self._lock:
                            self._errors[spec.name] = f"input capture ended: {spec.target}"
                    return
                levels = levels_from_frame(block, spec.channels)
                if not np.all(np.isfinite(levels)):
                    raise ValueError(f"non-finite audio samples from {spec.target}")
                with self._lock:
                    self._latest[spec.name] = levels
                    pending = self._pending.get(spec.name)
                    self._pending[spec.name] = levels if pending is None else np.maximum(pending, levels)
                    self._updated[spec.name] = time.monotonic()
        except (OSError, ValueError) as exc:
            if not self._stop.is_set():
                with self._lock:
                    self._errors[spec.name] = str(exc)

    def read_levels(self) -> dict[str, float]:
        now = time.monotonic()
        with self._lock:
            if self._errors:
                raise RuntimeError("; ".join(self._errors.values()))
            for name, updated in self._updated.items():
                if now - updated > 1.0:
                    raise RuntimeError(f"input capture has stopped delivering audio: {name}")
            levels = {name: self._pending.get(name, value) for name, value in self._latest.items()}
            self._pending.clear()
        result = {}
        for entry in self.entries:
            values = levels.get(entry.stream_name)
            if values is not None:
                indices = entry.indices or (0, 1)
                result[entry.label] = float(np.max(values[list(indices)]))
        return result

    def stop(self) -> None:
        self._stop.set()
        for proc in self.processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.processes:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for thread in self._threads:
            thread.join(timeout=2)
        for proc in self.processes:
            if proc.stdout is not None:
                proc.stdout.close()
        self.processes.clear()
