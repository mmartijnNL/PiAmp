from app.config import load_config


def test_load_config_builds_input_and_zone_models():
    config = load_config("config/example.yaml")

    assert config.name == "piamp"
    assert [i.name for i in config.inputs] == ["tv", "music", "aux"]
    assert [z.name for z in config.zones] == ["living_room", "kitchen", "office"]
    assert config.routes == ()

    zone = next(z for z in config.zones if z.name == "living_room")
    assert zone.output_channels == ["L", "R"]
    assert zone.output_device == "default"


def test_load_config_rejects_duplicate_names():
    try:
        load_config("config/invalid_duplicate.yaml")
    except ValueError as exc:
        assert "duplicate" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError for duplicate names")
