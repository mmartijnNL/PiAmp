import json

from app.pipewire import PipeWireAdapter
from app.router import RouteManager


def test_pipewire_adapter_discovers_source_and_output_nodes():
    def fake_runner(command):
        if command[:3] == ["pw-cli", "ls", "Node"]:
            return '''
                id 91, type PipeWire:Interface:Node/3
                node.name = "alsa_input.usb-BEHRINGER_UMC1820_7A122C9D-00.multichannel-input"
                media.class = "Audio/Source"
                id 57, type PipeWire:Interface:Node/3
                node.name = "alsa_output.usb-BEHRINGER_UMC1820_7A122C9D-00.analog-stereo"
                media.class = "Audio/Sink"
                id 58, type PipeWire:Interface:Node/3
                node.name = "alsa_output.pci-0000_01_00.1.hdmi-stereo"
                media.class = "Audio/Sink"
            '''
        if command[:3] == ["wpctl", "list", "audio"]:
            return '''
                Sinks:
                    * 57. Built-in Audio Stereo
                    58. HDMI Audio
                Sources:
                    * 91. UMC1820 Multichannel
            '''
        raise AssertionError(f"Unexpected command: {command}")

    adapter = PipeWireAdapter(runner=fake_runner)
    devices = adapter.discover_devices()

    assert devices["source_device"] == "UMC1820 Multichannel"
    assert "Built-in Audio Stereo" in devices["output_devices"]
    assert "HDMI Audio" in devices["output_devices"]


def test_pipewire_adapter_resolves_configured_input_and_zone_devices():
    config = type("Cfg", (), {
        "inputs": [
            type("Input", (), {"name": "Pi", "source": {"device": "Monitor of Built-in Audio", "channels": [0, 1]}})(),
            type("Input", (), {"name": "Test Input", "source": {"device": "UMC1820 Multichannel", "channels": [0, 1]}})(),
        ],
        "zones": [
            type("Zone", (), {"name": "Bureau Martijn", "device": "UMC1820 Multichannel", "channels": [0, 1]})(),
        ],
    })()

    def fake_runner(command):
        if command[:3] == ["wpctl", "list", "audio"]:
            return '''
                Sinks:
                    * 58. UMC1820 Multichannel
                Sources:
                    * 91. Monitor of Built-in Audio
            '''
        return ""

    adapter = PipeWireAdapter(config=config, runner=fake_runner)
    resolved = adapter.resolve_configured_devices()

    assert resolved["Pi"]["device"] == "Monitor of Built-in Audio"
    assert resolved["Test Input"]["device"] == "UMC1820 Multichannel"
    assert resolved["Bureau Martijn"]["device"] == "UMC1820 Multichannel"


def test_pipewire_adapter_reports_missing_configured_devices():
    config = type("Cfg", (), {
        "inputs": [
            type("Input", (), {"name": "Pi", "source": {"device": "Monitor of Built-in Audio", "channels": [0, 1]}})(),
        ],
        "zones": [
            type("Zone", (), {"name": "Bureau Martijn", "device": "UMC1820 Multichannel", "channels": [0, 1]})(),
        ],
    })()

    def fake_runner(command):
        if command[:3] == ["wpctl", "list", "audio"]:
            return '''
                Sinks:
                    * 57. HDMI Audio
                Sources:
                    * 91. Internal Mic
            '''
        return ""

    adapter = PipeWireAdapter(config=config, runner=fake_runner)
    result = adapter.validate_configured_devices()

    assert result["ok"] is False
    assert "Monitor of Built-in Audio" in result["missing_devices"]
    assert "UMC1820 Multichannel" in result["missing_devices"]


def test_pipewire_adapter_applies_routes_to_live_nodes():
    config = type("Cfg", (), {
        "inputs": [type("Input", (), {"name": "Pi", "source": {"device": "Monitor of Built-in Audio", "channels": [0, 1]}})()],
        "zones": [type("Zone", (), {"name": "Bureau Martijn", "device": "UMC1820 Multichannel", "channels": [0, 1]})()],
    })()

    commands = []

    def fake_runner(command):
        commands.append(command)
        if command[:3] == ["wpctl", "list", "audio"]:
            return '''
                Sinks:
                    * 57. UMC1820 Multichannel
                Sources:
                    * 91. Monitor of Built-in Audio
            '''
        if command == ["pw-cli", "ls", "Node"]:
            return '''
                id 91, type PipeWire:Interface:Node/3
                node.name = "alsa_input.usb-...-input"
                media.class = "Audio/Source"
                id 57, type PipeWire:Interface:Node/3
                node.name = "alsa_output.usb-...-analog-stereo"
                media.class = "Audio/Sink"
            '''
        return ""

    adapter = PipeWireAdapter(config=config, runner=fake_runner)
    result = adapter.apply_routes({"Bureau Martijn": {"Pi": 0.0}})

    assert result["applied"][0]["input"] == "Pi"
    assert result["applied"][0]["zone"] == "Bureau Martijn"
    assert ["pw-link", "91", "57"] in commands


def test_route_manager_routes_input_to_zones_and_mixes():
    manager = RouteManager()
    manager.add_input("tv", ["L", "R"])
    manager.add_input("music", ["L", "R"])
    manager.add_zone("living_room", "default", ["L", "R"])
    manager.add_zone("kitchen", "default", ["L", "R"])

    manager.route("tv", ["living_room", "kitchen"], gain_db=0.0)
    manager.route("music", ["kitchen"], gain_db=-3.0)

    assert manager.state["living_room"]["tv"] == 0.0
    assert manager.state["kitchen"]["tv"] == 0.0
    assert manager.state["kitchen"]["music"] == -3.0


def test_route_manager_group_command_expands_all_zones():
    manager = RouteManager()
    manager.add_input("tv", ["L", "R"])
    manager.add_zone("lounge", "default", ["L", "R"])
    manager.add_zone("bedroom", "default", ["L", "R"])

    manager.route("tv", ["all"], gain_db=2.5)

    assert manager.state["lounge"]["tv"] == 2.5
    assert manager.state["bedroom"]["tv"] == 2.5


def test_route_manager_filters_persisted_state_to_configured_zones(tmp_path):
    state_path = tmp_path / "routes.json"
    state_path.write_text(json.dumps({
        "bedroom": {"tv": 2.5},
        "Bureau Martijn": {"Pi": 0.0},
    }), encoding="utf-8")

    config = type("Cfg", (), {
        "inputs": [type("Input", (), {"name": "Pi", "channels": [0, 1]})()],
        "zones": [type("Zone", (), {"name": "Bureau Martijn", "device": "UMC1820 Multichannel", "channels": [0, 1]})()],
    })()

    manager = RouteManager(config=config, state_path=state_path)

    assert set(manager.state.keys()) == {"Bureau Martijn"}
    assert "bedroom" not in manager.state


def test_route_manager_persists_runtime_state(tmp_path):
    state_path = tmp_path / "routes.json"
    manager = RouteManager(state_path=state_path)
    manager.add_input("tv", ["L", "R"])
    manager.add_zone("lounge", "default", ["L", "R"])
    manager.route("tv", ["lounge"], gain_db=-2.0)

    assert state_path.exists()
    assert state_path.read_text(encoding="utf-8")
    reloaded = RouteManager(state_path=state_path)
    assert reloaded.state["lounge"]["tv"] == -2.0
