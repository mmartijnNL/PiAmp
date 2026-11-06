import json
import textwrap

from app.pipewire import PipeWireAdapter
from app.router import RouteManager


def test_pipewire_adapter_discovers_pro_audio_nodes_and_validates_all_sources():
    config = type("Cfg", (), {
        "inputs": [type("Input", (), {"name": "Aux", "source": {"device": "UMC1820 Pro", "channels": [0, 1]}})()],
        "zones": [type("Zone", (), {"name": "Office", "device": "Built-in Audio Stereo", "channels": [0, 1]})()],
    })()
    nodes = json.dumps([
        {"id": 10, "type": "PipeWire:Interface:Node", "info": {"props": {"node.description": "Internal Mic", "media.class": "Audio/Source"}}},
        {"id": 57, "type": "PipeWire:Interface:Node", "info": {"props": {"node.description": "Built-in Audio Stereo", "media.class": "Audio/Sink"}}},
        {"id": 90, "type": "PipeWire:Interface:Node", "info": {"props": {"node.description": "UMC1820 Pro", "media.class": "Audio/Sink"}}},
        {"id": 91, "type": "PipeWire:Interface:Node", "info": {"props": {"node.description": "UMC1820 Pro", "media.class": "Audio/Source"}}},
    ])

    def fake_runner(command):
        assert command == ["pw-dump"]
        return nodes

    adapter = PipeWireAdapter(config=config, runner=fake_runner)
    devices = adapter.discover_devices()

    assert devices["source_device"] == "Internal Mic"
    assert "UMC1820 Pro" in devices["source_devices"]
    assert "UMC1820 Pro" in devices["output_devices"]
    assert adapter.validate_configured_devices() == {"ok": True, "missing_devices": []}


def test_pipewire_adapter_discovers_source_and_output_nodes():
    def fake_runner(command):
        if command == ["pw-dump"]:
            return ""
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
        if command == ["wpctl", "status"]:
            return textwrap.dedent('''\
                Audio
                 ├─ Sinks:
                 │  *   57. Built-in Audio Stereo
                 │      58. HDMI Audio
                 │
                 ├─ Sources:
                 │  *   91. UMC1820 Multichannel

                Video
                 ├─ Sources:
                 │  *   99. Not An Audio Source
            ''')
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
        if command == ["wpctl", "status"]:
            return textwrap.dedent('''\
                Audio
                 ├─ Sinks:
                 │  *   58. UMC1820 Multichannel
                 │
                 ├─ Sources:
                 │  *   91. Monitor of Built-in Audio
            ''')
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
        if command == ["wpctl", "status"]:
            return textwrap.dedent('''\
                Audio
                 ├─ Sinks:
                 │  *   57. HDMI Audio
                 │
                 ├─ Sources:
                 │  *   91. Internal Mic
            ''')
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

    pw_dump_fixture = json.dumps([
        {
            "id": 91, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.description": "Monitor of Built-in Audio", "media.class": "Audio/Source"}},
        },
        {
            "id": 57, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.description": "UMC1820 Multichannel", "media.class": "Audio/Sink"}},
        },
        {
            "id": 200, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": 91, "port.direction": "out", "port.id": 0}},
        },
        {
            "id": 201, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": 91, "port.direction": "out", "port.id": 1}},
        },
        {
            "id": 300, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": 57, "port.direction": "in", "port.id": 0}},
        },
        {
            "id": 301, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": 57, "port.direction": "in", "port.id": 1}},
        },
    ])

    def fake_runner(command):
        commands.append(command)
        if command == ["pw-dump"]:
            return pw_dump_fixture
        return ""

    adapter = PipeWireAdapter(config=config, runner=fake_runner)
    result = adapter.apply_routes({"Bureau Martijn": {"Pi": 0.0}})

    assert result["applied"][0]["input"] == "Pi"
    assert result["applied"][0]["zone"] == "Bureau Martijn"
    assert result["applied"][0]["source_id"] == "91"
    assert result["applied"][0]["sink_id"] == "57"
    assert ["pw-link", "200", "300"] in commands
    assert ["pw-link", "201", "301"] in commands
    assert ["wpctl", "set-volume", "57", "1.000"] in commands


def test_pipewire_adapter_removes_stale_links_on_reapply():
    # Simulates re-running after a zone's configured channels changed: an old
    # link from a previous run (port 400 -> port 300, no longer part of the
    # current config) should be disconnected, while the fresh link for the
    # currently configured channel (200 -> 300) should be (re)created.
    config = type("Cfg", (), {
        "inputs": [type("Input", (), {"name": "Pi", "source": {"device": "Monitor of Built-in Audio", "channels": [0, 1]}})()],
        "zones": [type("Zone", (), {"name": "Bureau Martijn", "device": "UMC1820 Multichannel", "channels": [0, 1]})()],
    })()

    commands = []

    pw_dump_fixture = json.dumps([
        {
            "id": 91, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.description": "Monitor of Built-in Audio", "media.class": "Audio/Source"}},
        },
        {
            "id": 57, "type": "PipeWire:Interface:Node",
            "info": {"props": {"node.description": "UMC1820 Multichannel", "media.class": "Audio/Sink"}},
        },
        {
            "id": 200, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": 91, "port.direction": "out", "port.id": 0}},
        },
        {
            "id": 201, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": 91, "port.direction": "out", "port.id": 1}},
        },
        {
            "id": 300, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": 57, "port.direction": "in", "port.id": 0}},
        },
        {
            "id": 301, "type": "PipeWire:Interface:Port",
            "info": {"props": {"node.id": 57, "port.direction": "in", "port.id": 1}},
        },
        {
            "id": 1, "type": "PipeWire:Interface:Link",
            "info": {"output-port-id": 400, "input-port-id": 300},
        },
    ])

    def fake_runner(command):
        commands.append(command)
        if command == ["pw-dump"]:
            return pw_dump_fixture
        return ""

    adapter = PipeWireAdapter(config=config, runner=fake_runner)
    adapter.apply_routes({"Bureau Martijn": {"Pi": 0.0}})

    assert ["pw-link", "-d", "400", "300"] in commands
    assert ["pw-link", "200", "300"] in commands
    assert ["pw-link", "201", "301"] in commands

    commands.clear()
    adapter.apply_routes({"Bureau Martijn": {}})
    assert ["pw-link", "-d", "400", "300"] in commands
    assert not any(command[0] == "pw-link" and command[1] != "-d" for command in commands)


def test_route_manager_routes_input_to_zones_and_mixes(tmp_path):
    manager = RouteManager(state_path=tmp_path / "routes.json")
    manager.add_input("tv", ["L", "R"])
    manager.add_input("music", ["L", "R"])
    manager.add_zone("living_room", "default", ["L", "R"])
    manager.add_zone("kitchen", "default", ["L", "R"])

    manager.route("tv", ["living_room", "kitchen"], gain_db=0.0)
    manager.route("music", ["kitchen"], gain_db=-3.0)

    assert manager.state["living_room"]["tv"] == 0.0
    assert manager.state["kitchen"]["tv"] == 0.0
    assert manager.state["kitchen"]["music"] == -3.0


def test_route_manager_group_command_expands_all_zones(tmp_path):
    manager = RouteManager(state_path=tmp_path / "routes.json")
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
