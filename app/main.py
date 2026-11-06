from __future__ import annotations

import argparse
from pathlib import Path
from threading import Event

import yaml

from .api import AudioAPI
from .config import load_config
from .pipewire import PipeWireAdapter
from .router import RouteManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PiAmp audio routing service")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--state", default="state/routes.json", help="Path to persisted route state")
    return parser


class PiAmpService:
    def __init__(self, config_path: str, state_path: str):
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)
        self.last_seen: tuple[bytes, bytes] | None = None
        self.api: AudioAPI | None = None

    def check_for_changes(self) -> bool:
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
                self.last_seen = None
                return False
            if retired_zones:
                self.api.pipewire_adapter.apply_routes(retired_zones)
            result = adapter.apply_routes(manager.state)
            self.api = AudioAPI(manager, adapter)
            print(f"Reloaded config and routes: applied {len(result['applied'])} route(s).", flush=True)
            return True
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, yaml.YAMLError) as exc:
            print(f"ERROR: reload failed: {exc}. Waiting for corrected files.", flush=True)
            return False

    def run(self, stop_event: Event | None = None) -> None:
        stop_event = stop_event or Event()
        print("PiAmp watching config and routes for changes (every 1 second).", flush=True)
        while not stop_event.is_set():
            self.check_for_changes()
            stop_event.wait(1.0)


def main() -> None:
    args = build_parser().parse_args()

    print("Starting PiAmp...", flush=True)
    try:
        PiAmpService(args.config, args.state).run()
    except KeyboardInterrupt:
        print("PiAmp stopped.", flush=True)


if __name__ == "__main__":
    main()
