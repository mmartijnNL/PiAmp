from __future__ import annotations

from typing import Any

from .router import RouteManager


class AudioAPI:
    def __init__(self, route_manager: RouteManager):
        self.route_manager = route_manager

    def play(self, input_name: str, zones: list[str] | str = "all", gain_db: float = 0.0) -> dict[str, Any]:
        return self.handle_command({
            "action": "play",
            "input": input_name,
            "zones": zones,
            "gain_db": gain_db,
        })

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action", "route")).lower()

        if action in {"route", "play"}:
            if "routes" in payload:
                routes = payload["routes"]
                self.route_manager.route_many(routes)
                return {"status": "ok", "state": self.route_manager.state}

            input_name = str(payload["input"])
            zones = payload.get("zones", ["all"])
            gain_db = float(payload.get("gain_db", 0.0))
            self.route_manager.route(input_name, zones, gain_db)
            return {"status": "ok", "state": self.route_manager.state}

        if action == "status":
            return {"status": "ok", "state": self.route_manager.state}

        raise ValueError(f"unsupported action: {action}")
