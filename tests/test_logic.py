import pytest
import yaml

from app.logic import RouteSelector, parse_route_state
from app.models import InputDefinition, SystemConfig, ZoneDefinition
from app.router import RouteManager
from app.state_store import StateStore


def test_nested_and_mix_falls_back_only_after_five_continuous_silent_seconds():
    routes = parse_route_state({
        "Office": {"AND": {"TV": 0, "GameCube": -6}},
        "WC": {"OR": {"AND": {"TV": 0, "GameCube": 0}, "Jazz": -60}},
    })
    selector = RouteSelector(-50)
    silent = {"TV": -80, "GameCube": -80, "Jazz": -10}
    primary = {"TV": 0, "GameCube": 0}
    assert selector.resolve(routes, silent, 0)["WC"] == primary
    assert selector.resolve(routes, silent, 4.999)["WC"] == primary
    active = {**silent, "GameCube": -49}
    assert selector.resolve(routes, active, 4.999)["WC"] == primary
    assert selector.resolve(routes, silent, 5)["WC"] == primary
    assert selector.resolve(routes, silent, 9.999)["WC"] == primary
    resolved = selector.resolve(routes, silent, 10)
    assert resolved["Office"] == {"TV": 0, "GameCube": -6}
    assert resolved["WC"] == {"Jazz": -60}
    assert selector.resolve(routes, active, 10.1)["WC"] == primary


def test_unknown_measurements_are_not_silence_and_threshold_is_inclusive():
    routes = parse_route_state({"WC": {"OR": {"TV": 0, "Jazz": -60}}})
    selector = RouteSelector(-50)
    assert selector.resolve(routes, {}, 0)["WC"] == {"TV": 0}
    assert selector.resolve(routes, {}, 20)["WC"] == {"TV": 0}
    assert selector.resolve(routes, {"TV": -80}, 21)["WC"] == {"TV": 0}
    assert selector.resolve(routes, {"TV": -50}, 25)["WC"] == {"TV": 0}
    assert selector.resolve(routes, {"TV": -80}, 26)["WC"] == {"TV": 0}
    assert selector.resolve(routes, {}, 30)["WC"] == {"TV": 0}
    assert selector.resolve(routes, {"TV": -80}, 31)["WC"] == {"TV": 0}
    assert selector.resolve(routes, {"TV": -80}, 35.999)["WC"] == {"TV": 0}
    assert selector.resolve(routes, {"TV": -80}, 36)["WC"] == {"Jazz": -60}
    assert selector.resolve(routes, {}, 37)["WC"] == {"Jazz": -60}


def test_or_preserves_branch_priority_and_stays_on_last_if_all_are_silent():
    routes = parse_route_state({"WC": {"OR": {"TV": 0, "GameCube": 0, "Jazz": -60}}})
    selector = RouteSelector()
    levels = {"TV": -80, "GameCube": -10, "Jazz": -10}
    selector.resolve(routes, levels, 0)
    assert selector.resolve(routes, levels, 5)["WC"] == {"GameCube": 0}
    levels["GameCube"] = -80
    assert selector.resolve(routes, levels, 6)["WC"] == {"GameCube": 0}
    assert selector.resolve(routes, levels, 10.999)["WC"] == {"GameCube": 0}
    assert selector.resolve(routes, levels, 11)["WC"] == {"Jazz": -60}
    levels["Jazz"] = -80
    assert selector.resolve(routes, levels, 50)["WC"] == {"Jazz": -60}
    assert selector.resolve(routes, levels, 55)["WC"] == {"Jazz": -60}
    levels["TV"] = -10
    assert selector.resolve(routes, levels, 56)["WC"] == {"TV": 0}


def test_or_inside_and_changes_only_its_branch_of_the_mix():
    routes = parse_route_state({"WC": {"AND": {"TV": -6, "OR": {"GameCube": 0, "Jazz": -60}}}})
    selector = RouteSelector()
    levels = {"TV": -10, "GameCube": -80, "Jazz": -20}
    assert selector.resolve(routes, levels, 0)["WC"] == {"TV": -6, "GameCube": 0}
    assert selector.resolve(routes, levels, 5)["WC"] == {"TV": -6, "Jazz": -60}
    levels["GameCube"] = -10
    assert selector.resolve(routes, levels, 5.1)["WC"] == {"TV": -6, "GameCube": 0}


@pytest.mark.parametrize("value", [
    {"WC": {"OR": {}}},
    {"WC": {"AND": 0}},
    {"WC": {"TV": {"Jazz": 0}}},
    {"WC": {"TV": float("nan")}},
    {"WC": {"TV": True}},
    {"WC": {"TV": 21}},
])
def test_invalid_expressions_are_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        parse_route_state(value)


def test_route_logic_is_preserved_when_saving_and_flat_routes_still_work(tmp_path):
    path = tmp_path / "routes.yaml"
    original = {"WC": {"OR": {"AND": {"TV": 0, "GameCube": 0}, "Jazz": -60}}}
    path.write_text(yaml.safe_dump(original, sort_keys=False))
    config = SystemConfig(
        inputs=tuple(InputDefinition(name) for name in ("TV", "GameCube", "Jazz")),
        zones=(ZoneDefinition("WC"),),
    )
    manager = RouteManager(config, path, persist_state=False, strict_state=True)
    assert manager.state == {"WC": {"TV": 0, "GameCube": 0}}
    manager.resolve({"TV": -80, "GameCube": -80, "Jazz": -10}, 0)
    manager.state = manager.resolve({"TV": -80, "GameCube": -80, "Jazz": -10}, 5)
    assert manager.state == {"WC": {"Jazz": -60}}
    manager.save_state()
    assert yaml.safe_load(path.read_text()) == original
    assert StateStore(path).load(strict=True) == original
    path.write_text("WC:\n  Jazz: -10\n")
    manager = RouteManager(config, path, persist_state=False, strict_state=True)
    assert not manager.needs_levels
    assert manager.state == {"WC": {"Jazz": -10}}


def test_unknown_inputs_inside_logic_are_reported(tmp_path):
    path = tmp_path / "routes.yaml"
    path.write_text("WC:\n  OR:\n    Typo: 0\n    Jazz: -60\n")
    config = SystemConfig(inputs=(InputDefinition("Jazz"),), zones=(ZoneDefinition("WC"),))
    with pytest.raises(ValueError, match="unknown input.*Typo"):
        RouteManager(config, path, persist_state=False, strict_state=True)
