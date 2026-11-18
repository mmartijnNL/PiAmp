import pytest

from app.config import load_config


def test_load_config_builds_input_and_zone_models(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""
inputs:
  - name: Pi
  - name: Aux
  - name: TV
zones:
  - name: Bureau Martijn
    device: UMC1820 Pro
    channels: [0, 1]
""")
    config = load_config(config_path)

    assert [i.name for i in config.inputs] == ["Pi", "Aux", "TV"]
    assert [z.name for z in config.zones] == ["Bureau Martijn"]
    assert config.routes == ()

    zone = next(z for z in config.zones if z.name == "Bureau Martijn")
    assert zone.channels == [0, 1]
    assert zone.device == "UMC1820 Pro"


def test_load_config_rejects_duplicate_names(tmp_path):
    invalid_path = tmp_path / "invalid_duplicate.yaml"
    invalid_path.write_text(
        """
name: piamp

inputs:
  - name: tv
    source: pipewire
    channels: ["L", "R"]
  - name: tv
    source: pipewire
    channels: ["L", "R"]

zones:
  - name: living_room
    output_device: default
    output_channels: ["L", "R"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_config(invalid_path)
