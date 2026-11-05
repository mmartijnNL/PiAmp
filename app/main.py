from __future__ import annotations

import argparse
import json

from .api import AudioAPI
from .config import load_config
from .router import RouteManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PiAmp audio routing service")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("--state", default="state/routes.json", help="Path to persisted route state")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    route_manager = RouteManager(config, state_path=args.state)
    api = AudioAPI(route_manager)

    result = api.play("tv", ["all"], 0.0)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
