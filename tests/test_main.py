import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import yaml

from app.main import PiAmpService


class ServiceTests(unittest.TestCase):
    def test_reloads_both_files_without_rewriting_and_rejects_invalid_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            state_path = Path(directory) / "routes.yaml"
            config_path.write_text("inputs:\n  - name: Aux\n    source: Test\nzones:\n  - name: Office\n    device: Test\n    channels: [0, 1]\n")
            state_path.write_text("Office:\n  Aux: 0\n")
            with patch("app.main.PipeWireAdapter") as adapter_class:
                adapter = adapter_class.return_value
                adapter.validate_configured_devices.return_value = {"ok": True}
                adapter.apply_routes.return_value = {"applied": []}
                service = PiAmpService(str(config_path), str(state_path))
                self.assertTrue(service.check_for_changes())
                self.assertEqual(service._status_lines(), ["Routes active: 1"])
                self.assertEqual(state_path.read_text(), "Office:\n  Aux: 0\n")
                self.assertFalse(service.check_for_changes())
                self.assertEqual(adapter.apply_routes.call_count, 2)

                state_path.write_text("Office: {}\n")
                self.assertTrue(service.check_for_changes())
                adapter.apply_routes.assert_called_with({"Office": {}})

                config_path.write_text(config_path.read_text().replace("[0, 1]", "[2, 3]"))
                self.assertTrue(service.check_for_changes())
                self.assertEqual(service.api.route_manager.zones["Office"]["output_channels"], [2, 3])

                previous_api = service.api
                calls = adapter.apply_routes.call_count
                for invalid in ('{', '[]', 'Office:\n  Aux: .nan\n'):
                    state_path.write_text(invalid)
                    self.assertFalse(service.check_for_changes())
                    self.assertIs(service.api, previous_api)
                    self.assertTrue(any("ERROR: reload failed:" in line for line in service._status_lines()))
                    self.assertEqual(adapter.apply_routes.call_count, calls)
                    self.assertEqual(state_path.read_text(), invalid)

                state_path.write_text("Office:\n  Aux: -3\n")
                self.assertTrue(service.check_for_changes())
                self.assertFalse(any("ERROR:" in line or "Last:" in line for line in service._status_lines()))
                config_path.write_text("zones: [")
                calls = adapter.apply_routes.call_count
                self.assertFalse(service.check_for_changes())
                self.assertEqual(adapter.apply_routes.call_count, calls)

    def test_deleted_zone_disconnects_old_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            state_path = Path(directory) / "routes.yaml"
            config_path.write_text("inputs:\n  - name: Aux\nzones:\n  - name: Office\n    device: Test\n")
            state_path.write_text("Office:\n  Aux: 0\n")
            with patch("app.main.PipeWireAdapter") as adapter_class:
                adapter = adapter_class.return_value
                adapter.validate_configured_devices.return_value = {"ok": True}
                adapter.apply_routes.return_value = {"applied": []}
                service = PiAmpService(str(config_path), str(state_path))
                self.assertTrue(service.check_for_changes())
                adapter.apply_routes.reset_mock()
                config_path.write_text("inputs: []\nzones: []\n")
                self.assertTrue(service.check_for_changes())
                self.assertEqual(adapter.apply_routes.call_args_list[2].args, ({"Office": {}},))
                adapter.apply_routes.assert_called_with({})
                self.assertEqual(yaml.safe_load(state_path.read_text()), {"Office": {"Aux": 0}})

    def test_run_keeps_polling_until_stopped(self):
        service = PiAmpService("config.yaml", "routes.yaml")
        stop_event = Mock(spec=Event)
        stop_event.is_set.side_effect = [False, False, False, True]
        with patch.object(service, "check_for_changes") as check:
            service.run(stop_event)
        self.assertEqual(check.call_count, 3)
        self.assertEqual(stop_event.wait.call_count, 3)
        stop_event.wait.assert_called_with(0.1)

    def test_automatic_routes_work_without_ui_and_do_not_rewrite_logic(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            routes_path = Path(directory) / "routes.yaml"
            config_path.write_text(
                "inputs:\n  - name: TV\n  - name: Jazz\n"
                "zones:\n  - name: WC\n    device: Test\n"
            )
            text = "WC:\n  OR:\n    TV: 0\n    Jazz: -60\n"
            routes_path.write_text(text)
            with patch("app.main.PipeWireAdapter") as adapters, patch("app.main.InputLevelMonitor") as monitors, patch("app.router.time.monotonic", return_value=0):
                adapter = adapters.return_value
                adapter.validate_configured_devices.return_value = {"ok": True}
                adapter.apply_routes.return_value = {"applied": []}
                monitor = monitors.return_value
                monitor.read_levels.return_value = {"TV": -80, "Jazz": -10}
                service = PiAmpService(str(config_path), str(routes_path))
                self.assertTrue(service.check_for_changes())
                service.players = Mock()
                self.assertFalse(service.ui_active)
                self.assertEqual(service.api.route_manager.state, {"WC": {"TV": 0}})
                self.assertFalse(service.update_routes(4.999))
                self.assertTrue(service.update_routes(5))
                adapter.apply_routes.assert_called_with({"WC": {"Jazz": -60}})
                service.players.set_routes.assert_called_with({"WC": {"Jazz": -60}})
                monitor.read_levels.return_value = {"TV": -10, "Jazz": -10}
                adapter.apply_routes.side_effect = RuntimeError("link failed")
                service.players.set_routes.reset_mock()
                self.assertFalse(service.update_routes(5.1))
                service.players.set_routes.assert_not_called()
                self.assertEqual(service.api.route_manager.state, {"WC": {"Jazz": -60}})
                adapter.apply_routes.side_effect = None
                self.assertTrue(service.update_routes(5.1))
                adapter.apply_routes.assert_called_with({"WC": {"TV": 0}})
                service.players.set_routes.assert_called_with({"WC": {"TV": 0}})
                self.assertEqual(routes_path.read_text(), text)
                monitor.read_levels.side_effect = RuntimeError("capture stopped")
                self.assertFalse(service.update_routes(6))
                self.assertIn("capture stopped", service.active_errors["logic"])
                self.assertEqual(service.api.route_manager.state, {"WC": {"TV": 0}})
                monitor.stop.assert_called_once()

    def test_pipewire_command_failure_is_logged_without_success(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            state_path = Path(directory) / "routes.yaml"
            config_path.write_text("inputs: []\nzones: []\n")
            state_path.write_text("{}")
            with patch("app.main.PipeWireAdapter") as adapter_class:
                adapter = adapter_class.return_value
                adapter.validate_configured_devices.return_value = {"ok": True}
                adapter.apply_routes.side_effect = [{"applied": []}, RuntimeError("failed to link ports")]
                service = PiAmpService(str(config_path), str(state_path))
                self.assertFalse(service.check_for_changes())
                self.assertIsNone(service.api)
                self.assertIn("ERROR: reload failed: failed to link ports", service.messages[-1])

    def test_timer_only_routes_update_players_without_an_audio_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            routes = Path(directory) / "routes.yaml"
            config.write_text("inputs:\n  - name: Jazz\nzones:\n  - name: WC\n    device: Test\n")
            routes.write_text('WC:\n  Jazz:\n    Gain: -40\n    StartTime: "10:00"\n    EndTime: "22:00"\n')
            with patch("app.main.PipeWireAdapter") as adapters, patch(
                "app.main.InputLevelMonitor"
            ) as monitors, patch("app.main.PlayerManager") as players, patch("app.logic.datetime") as clock:
                adapter = adapters.return_value
                adapter.validate_configured_devices.return_value = {"ok": True}
                adapter.apply_routes.return_value = {"applied": []}
                clock.now.return_value = datetime(2026, 11, 18, 9)
                service = PiAmpService(str(config), str(routes))
                self.assertTrue(service.check_for_changes())
                players.return_value.set_routes.assert_called_with({"WC": {}})
                clock.now.return_value = datetime(2026, 11, 18, 10)
                self.assertTrue(service.update_routes(1))
                players.return_value.set_routes.assert_called_with({"WC": {"Jazz": -40}})
                clock.now.return_value = datetime(2026, 11, 18, 22)
                self.assertTrue(service.update_routes(2))
                players.return_value.set_routes.assert_called_with({"WC": {}})
                monitors.assert_not_called()

    def test_timers_still_switch_during_monitor_failures_and_retry_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            routes = Path(directory) / "routes.yaml"
            config.write_text("inputs:\n  - name: TV\n  - name: Jazz\nzones:\n  - name: WC\n    device: Test\n")
            routes.write_text(
                'WC:\n  OR:\n    TV:\n      Gain: 0\n'
                '      StartTime: "10:00"\n      EndTime: "22:00"\n    Jazz: -40\n'
            )
            with patch("app.main.PipeWireAdapter") as adapters, patch(
                "app.main.InputLevelMonitor"
            ) as monitors, patch("app.main.PlayerManager") as players, patch("app.logic.datetime") as clock:
                adapter = adapters.return_value
                adapter.validate_configured_devices.return_value = {"ok": True}
                adapter.apply_routes.return_value = {"applied": []}
                monitor = monitors.return_value
                monitor.read_levels.return_value = {"TV": -10, "Jazz": -10}
                clock.now.return_value = datetime(2026, 11, 18, 21)
                service = PiAmpService(str(config), str(routes))
                self.assertTrue(service.check_for_changes())
                monitor.read_levels.side_effect = RuntimeError("capture stopped")
                clock.now.return_value = datetime(2026, 11, 18, 22)
                self.assertTrue(service.update_routes(1))
                players.return_value.set_routes.assert_called_with({"WC": {"Jazz": -40}})
                self.assertIn("capture stopped", service.active_errors["logic"])
                clock.now.return_value = datetime(2026, 11, 19, 10)
                self.assertTrue(service.update_routes(1.1))
                players.return_value.set_routes.assert_called_with({"WC": {"TV": 0}})
                self.assertEqual(monitor.read_levels.call_count, 2)
                self.assertIn("capture stopped", service.active_errors["logic"])


if __name__ == "__main__":
    unittest.main()