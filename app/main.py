from __future__ import annotations

import argparse

from .api import AudioAPI
from .config import load_config
from .pipewire import PipeWireAdapter
from .router import RouteManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PiAmp audio routing service")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--state", default="state/routes.json", help="Path to persisted route state")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    print("Starting PiAmp...")
    print(f"Loading config from {args.config}")
    config = load_config(args.config)
    print("Config loaded.")

    print("Checking configured audio devices...")
    validation = PipeWireAdapter(config=config).validate_configured_devices()
    if not validation["ok"]:
        missing = ", ".join(validation["missing_devices"]) if validation["missing_devices"] else "unknown"
        print(f"ERROR: configured audio devices are not available on this machine: {missing}")
        raise SystemExit(1)

    print("OK: all configured devices were found.")

    route_manager = RouteManager(config, state_path=args.state)
    print(f"Loaded route state from {args.state}")
    print(f"Known zones: {', '.join(route_manager.zones.keys()) or 'none'}")

    AudioAPI(route_manager, PipeWireAdapter(config=config))
    print("PiAmp ready.")


if __name__ == "__main__":
    main()
