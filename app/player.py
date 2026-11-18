from __future__ import annotations

import random
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .models import SystemConfig, input_folder, folder_node_name
from .logic import ResolvedRoutes

MUSIC_EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma", ".aiff", ".aif"}
RATE = 48000
CHANNELS = 2


def list_music_files(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in MUSIC_EXTENSIONS
    )


class FolderPlayer:
    """Plays every music file in a folder, in a loop, through one persistent
    unlinked PipeWire playback stream named after the input. The stream stays
    alive across tracks so links made by the router are never lost."""

    def __init__(self, input_name: str, folder: str | Path, shuffle: bool = False):
        self.input_name = input_name
        self.folder = Path(folder).expanduser()
        self.shuffle = shuffle
        self.node_name = folder_node_name(input_name)
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._paused = True
        self._sink: subprocess.Popen | None = None
        self._decoder: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"player-{input_name}")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._stop.set()
            self._condition.notify_all()
            if self._paused and self._decoder is not None:
                self._decoder.send_signal(signal.SIGCONT)
            for proc in (self._decoder, self._sink):
                if proc is not None and proc.poll() is None:
                    proc.terminate()
        self._thread.join(timeout=5)
        for proc in (self._decoder, self._sink):
            if proc is not None and proc.poll() is None:
                proc.kill()

    def set_active(self, active: bool) -> None:
        with self._condition:
            if self._paused == (not active):
                return
            if self._decoder is not None:
                self._decoder.send_signal(signal.SIGCONT if active else signal.SIGSTOP)
            self._paused = not active
            self._condition.notify_all()

    def _start_sink(self) -> subprocess.Popen:
        # --target 0 keeps the stream unlinked until PiAmp routes it.
        return subprocess.Popen(
            [
                "pw-cat", "--playback", "--raw", "--format", "s16",
                "--rate", str(RATE), "--channels", str(CHANNELS), "--target", "0",
                "-P", f"{{ node.name={self.node_name} media.role=Music }}", "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sink = self._start_sink()
            while not self._stop.is_set() and self._sink.poll() is None:
                files = list_music_files(self.folder) if self.folder.is_dir() else []
                if not files:
                    self._stop.wait(5.0)
                    continue
                if self.shuffle:
                    random.shuffle(files)
                for path in files:
                    with self._condition:
                        self._condition.wait_for(lambda: self._stop.is_set() or not self._paused)
                        if self._stop.is_set() or self._sink.poll() is not None:
                            break
                        self._decoder = subprocess.Popen(
                            [
                                "ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-vn",
                                "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS), "-",
                            ],
                            stdout=self._sink.stdin,
                            stderr=subprocess.DEVNULL,
                        )
                    self._decoder.wait()
            if self._sink.poll() is None:
                self._sink.terminate()
            if not self._stop.is_set():
                self._stop.wait(1.0)


class PlayerManager:
    """Keeps one FolderPlayer running per folder input in the config."""

    def __init__(self, node_exists: Callable[[str], bool] | None = None):
        self._players: dict[str, FolderPlayer] = {}
        self._active_inputs: set[str] = set()
        self._node_exists = node_exists

    def sync(self, config: SystemConfig, ready_timeout: float = 5.0) -> None:
        wanted: dict[str, tuple[Path, bool]] = {}
        for input_def in config.inputs:
            folder = input_folder(input_def)
            if folder is not None:
                shuffle = bool(input_def.source.get("shuffle", False))
                wanted[input_def.name] = (Path(folder).expanduser(), shuffle)

        for name in list(self._players):
            player = self._players[name]
            if name not in wanted or wanted[name] != (player.folder, player.shuffle):
                player.stop()
                del self._players[name]

        started = []
        for name, (folder, shuffle) in wanted.items():
            if name not in self._players:
                player = FolderPlayer(name, folder, shuffle)
                player.set_active(name in self._active_inputs)
                player.start()
                self._players[name] = player
                started.append(player)

        if self._node_exists is not None:
            deadline = time.monotonic() + ready_timeout
            for player in started:
                while time.monotonic() < deadline and not self._node_exists(player.node_name):
                    time.sleep(0.2)

    def set_routes(self, routes: ResolvedRoutes) -> None:
        active = {name for inputs in routes.values() for name in inputs}
        for name, player in self._players.items():
            player.set_active(name in active)
        self._active_inputs = active

    def stop_all(self) -> None:
        for player in self._players.values():
            player.stop()
        self._players.clear()
