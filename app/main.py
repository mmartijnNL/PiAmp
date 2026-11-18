from __future__ import annotations

import argparse
import threading
import time
from collections import deque
from pathlib import Path
from threading import Event

import yaml

from .api import AudioAPI
from .config import load_config
from .ui import TerminalUI
from .pipewire import PipeWireAdapter
from .player import PlayerManager
from .router import RouteManager
from .audio import InputLevelMonitor
from .models import SystemConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PiAmp audio routing service")
    parser.add_argument("--config", default="config/config.yaml", help="Path to YAML config")
    parser.add_argument("--routes", "--state", dest="state", default="config/routes.yaml", help="Path to YAML routes")
    parser.add_argument("--no-ui", "--no-meter", dest="no_ui", action="store_true", help="Disable the terminal UI")
    return parser


class PiAmpService:
    def __init__(self, config_path: str, state_path: str):
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)
        self.last_seen: tuple[bytes, bytes] | None = None
        self.api: AudioAPI | None = None
        self.messages: deque[str] = deque(maxlen=6)
        self.ui_active = False
        self.active_errors: dict[str, str] = {}
        self.players = PlayerManager(self._node_exists)
        self.level_monitor: InputLevelMonitor | None = None
        self.monitor_config: SystemConfig | None = None
        self.monitor_retry_at = 0.0

    @staticmethod
    def _node_exists(node_name: str) -> bool:
        nodes, _links = PipeWireAdapter._parse_pw_dump(PipeWireAdapter._default_runner(["pw-dump"]))
        wanted = PipeWireAdapter._normalize_device_name(node_name)
        return any(PipeWireAdapter._normalize_device_name(n["name"]) == wanted for n in nodes.values())

    def log(self, message: str) -> None:
        self.messages.append(message)
        if not self.ui_active:
            print(message, flush=True)

    def check_for_changes(self) -> bool:
        candidate_monitor = None
        try:
            snapshot = (self.config_path.read_bytes(), self.state_path.read_bytes())
            if snapshot == self.last_seen:
                return False
            self.last_seen = snapshot
            config = load_config(self.config_path)
            adapter = PipeWireAdapter(config=config)
            validation = adapter.validate_configured_devices()
            if not validation["ok"]:
                raise ValueError("configured audio devices are unavailable: " + ", ".join(validation["missing_devices"]))
            manager = RouteManager(config, state_path=self.state_path, persist_state=False, strict_state=True)
            self.players.sync(config)
            if manager.needs_levels:
                if self.level_monitor is not None and config == self.monitor_config:
                    candidate_monitor = self.level_monitor
                else:
                    candidate_monitor = InputLevelMonitor(config)
                    candidate_monitor.start()
                manager.state = manager.resolve(candidate_monitor.read_levels())
            adapter.apply_routes(manager.state, dry_run=True)
            retired_zones = {}
            if self.api is not None:
                retired_zones = {
                    name: {} for name, zone in self.api.route_manager.zones.items()
                    if manager.zones.get(name) != zone
                }
                if retired_zones:
                    self.api.pipewire_adapter.apply_routes(retired_zones, dry_run=True)
            if snapshot != (self.config_path.read_bytes(), self.state_path.read_bytes()):
                if candidate_monitor is not None and candidate_monitor is not self.level_monitor:
                    candidate_monitor.stop()
                self.last_seen = None
                return False
            if retired_zones:
                self.api.pipewire_adapter.apply_routes(retired_zones)
            result = adapter.apply_routes(manager.state)
            self.players.set_routes(manager.state)
            self.api = AudioAPI(manager, adapter)
            previous_monitor = self.level_monitor
            self.level_monitor = candidate_monitor
            self.monitor_config = config
            if previous_monitor is not None and previous_monitor is not candidate_monitor:
                previous_monitor.stop()
            self.active_errors.pop("routes", None)
            self.active_errors.pop("logic", None)
            self.log(f"Reloaded config and routes: applied {len(result['applied'])} route(s).")
            return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RuntimeError, yaml.YAMLError) as exc:
            if candidate_monitor is not None and candidate_monitor is not self.level_monitor:
                candidate_monitor.stop()
            error = f"ERROR: reload failed: {exc}. Waiting for corrected files."
            self.active_errors["routes"] = error
            self.log(error)
            return False

    def update_routes(self, now: float | None = None) -> bool:
        if self.api is None:
            return False
        manager = self.api.route_manager
        now = time.monotonic() if now is None else now
        levels: dict[str, float] = {}
        monitor_failed = False
        if manager.needs_levels:
            if self.level_monitor is None and now < self.monitor_retry_at:
                monitor_failed = True
            else:
                try:
                    if self.level_monitor is None:
                        config = self.monitor_config
                        if config is None:
                            raise RuntimeError("no input-monitor configuration has been loaded")
                        self.level_monitor = InputLevelMonitor(config)
                        self.level_monitor.start()
                    levels = self.level_monitor.read_levels()
                except (OSError, ValueError, RuntimeError) as exc:
                    if self.level_monitor is not None:
                        self.level_monitor.stop()
                        self.level_monitor = None
                    self.monitor_retry_at = now + 1.0
                    error = f"ERROR: route audio detection failed: {exc}"
                    if self.active_errors.get("logic") != error:
                        self.log(error)
                    self.active_errors["logic"] = error
                    monitor_failed = True
        try:
            resolved = self.api.route_manager.resolve(levels, now)
            changed = resolved != self.api.route_manager.state
            if changed:
                adapter = self.api.pipewire_adapter
                if adapter is None:
                    raise RuntimeError("no PipeWire adapter is available for automatic routing")
                adapter.apply_routes(resolved, dry_run=True)
                adapter.apply_routes(resolved)
                self.api.route_manager.state = resolved
            self.players.set_routes(resolved)
            if not monitor_failed:
                self.active_errors.pop("logic", None)
            return changed
        except (OSError, ValueError, RuntimeError) as exc:
            error = f"ERROR: automatic route change failed: {exc}"
            if self.active_errors.get("logic") != error:
                self.log(error)
            self.active_errors["logic"] = error
            return False

    def _status_lines(self) -> list[str]:
        route_count = sum(len(routes) for routes in self.api.route_manager.state.values()) if self.api else 0
        return [f"Routes active: {route_count}", *list(self.active_errors.values())]

    def start_ui(self, stop_event: Event) -> None:
        def ready() -> None:
            self.ui_active = True
            self.active_errors.pop("ui", None)

        def target() -> None:
            # Config/device discovery can fail transiently (e.g. an in-progress
            # edit or a device that isn't up yet), so retry on the same 1s
            # cadence as the route reload loop instead of giving up for good.
            while not stop_event.is_set():
                try:
                    config = load_config(self.config_path)
                    devices = PipeWireAdapter(config=config).discover_devices()
                    ui = TerminalUI(
                        config=config,
                        source_devices=devices.get("source_devices", []),
                        sink_devices=devices.get("output_devices", []),
                        footer=self._status_lines,
                    )
                    ui.run(stop_event, on_ready=ready)
                    return
                except Exception as exc:
                    self.ui_active = False
                    error = f"ERROR: terminal UI stopped: {exc}"
                    self.active_errors["ui"] = error
                    self.log(error)
                    stop_event.wait(1.0)

        threading.Thread(target=target, daemon=True).start()

    def run(self, stop_event: Event | None = None) -> None:
        stop_event = stop_event or Event()
        self.log("PiAmp watching config, routes, and input activity (every 0.1 seconds).")
        try:
            while not stop_event.is_set():
                self.check_for_changes()
                self.update_routes()
                stop_event.wait(0.1)
        finally:
            if self.level_monitor is not None:
                self.level_monitor.stop()


def main() -> None:
    args = build_parser().parse_args()

    print("Starting PiAmp...", flush=True)
    service = PiAmpService(args.config, args.state)
    stop_event = Event()
    if not args.no_ui:
        service.start_ui(stop_event)
    try:
        service.run(stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        service.players.stop_all()
    print("PiAmp stopped.", flush=True)


if __name__ == "__main__":
    main()
