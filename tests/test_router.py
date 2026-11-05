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
