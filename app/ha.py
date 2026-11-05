from __future__ import annotations

from typing import Any

from .api import AudioAPI


class HomeAssistantBridge:
    def __init__(self, api: AudioAPI):
        self.api = api

    def state_payload(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "zones": self.api.route_manager.state,
        }

    def receive_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.api.handle_command(payload)
