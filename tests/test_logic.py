from datetime import datetime
from unittest.mock import patch

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


@pytest.mark.parametrize(("clock", "expected"), [
    ("09:59:59", {}), ("10:00:00", {"Jazz": -40}),
    ("21:59:59", {"Jazz": -40}), ("22:00:00", {}),
])
def test_daily_timer_boundaries(clock, expected):
    routes = parse_route_state({"WC": {"Jazz": {
        "Gain": -40, "StartTime": "10:00", "EndTime": "22:00",
    }}})
    with patch("app.logic.datetime") as wall_clock:
        wall_clock.now.return_value = datetime.fromisoformat(f"2026-11-18T{clock}")
        assert RouteSelector().resolve(routes, {}, 0) == {"WC": expected}


def test_overnight_timer_skips_unavailable_branches_and_resumes_on_time():
    routes = parse_route_state({"WC": {"OR": {
        "Jazz": {"Gain": -40, "StartTime": "22:00", "EndTime": "06:00"},
        "TV": 0,
    }}})
    selector = RouteSelector()
    with patch("app.logic.datetime") as wall_clock:
        for clock, expected in [
            ("21:59:59", {"TV": 0}), ("22:00:00", {"Jazz": -40}),
            ("05:59:59", {"Jazz": -40}), ("06:00:00", {"TV": 0}),
        ]:
            wall_clock.now.return_value = datetime.fromisoformat(f"2026-11-18T{clock}")
            assert selector.resolve(routes, {}, 0)["WC"] == expected


def test_timer_can_disable_the_only_or_branch():
    routes = parse_route_state({"WC": {"OR": {
        "Jazz": {"Gain": -40, "StartTime": "10:00", "EndTime": "22:00"},
    }}})
    with patch("app.logic.datetime") as wall_clock:
        wall_clock.now.return_value = datetime(2026, 11, 18, 23)
        assert RouteSelector().resolve(routes, {}, 0) == {"WC": {}}


@pytest.mark.parametrize("timer", [
    {"Gain": 0, "StartTime": "10:00"},
    {"Gain": 0, "StartTime": "25:00", "EndTime": "22:00"},
    {"Gain": 0, "StartTime": "10:00", "EndTime": "10:00"},
    {"Gain": 0, "StartTime": 600, "EndTime": "22:00"},
    {"Gain": True, "StartTime": "10:00", "EndTime": "22:00"},
    {"Gain": 0, "StartTime": "10:00", "EndTime": "22:00", "Typo": 1},
])
def test_invalid_timers_are_rejected(timer):
    with pytest.raises(ValueError):
        parse_route_state({"WC": {"Jazz": timer}})


def test_timer_survives_route_filtering_and_save(tmp_path):
    path = tmp_path / "routes.yaml"
    original = {"WC": {"Jazz": {"Gain": -40, "StartTime": "10:00", "EndTime": "22:00"}}}
    path.write_text(yaml.safe_dump(original))
    config = SystemConfig(inputs=(InputDefinition("Jazz"),), zones=(ZoneDefinition("WC"),))
    manager = RouteManager(config, path, persist_state=False, strict_state=True)
    assert not manager.needs_levels
    manager.save_state()
    assert yaml.safe_load(path.read_text()) == original
