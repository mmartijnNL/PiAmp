import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

from app.main import PiAmpService


class ServiceTests(unittest.TestCase):
    def test_reloads_both_files_without_rewriting_and_rejects_invalid_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            state_path = Path(directory) / "routes.json"
            config_path.write_text("inputs:\n  - name: Aux\n    source: Test\nzones:\n  - name: Office\n    device: Test\n    channels: [0, 1]\n")
            state_path.write_text('{"Office": {"Aux": 0}}')
            with patch("app.main.PipeWireAdapter") as adapter_class:
                adapter = adapter_class.return_value
                adapter.validate_configured_devices.return_value = {"ok": True}
                adapter.apply_routes.return_value = {"applied": []}
                service = PiAmpService(str(config_path), str(state_path))
                self.assertTrue(service.check_for_changes())
                self.assertEqual(state_path.read_text(), '{"Office": {"Aux": 0}}')
                self.assertFalse(service.check_for_changes())
                self.assertEqual(adapter.apply_routes.call_count, 2)

                state_path.write_text(json.dumps({"Office": {}}))
                self.assertTrue(service.check_for_changes())
                adapter.apply_routes.assert_called_with({"Office": {}})

                config_path.write_text(config_path.read_text().replace("[0, 1]", "[2, 3]"))
                self.assertTrue(service.check_for_changes())
                self.assertEqual(service.api.route_manager.zones["Office"]["output_channels"], [2, 3])

                previous_api = service.api
                calls = adapter.apply_routes.call_count
                for invalid in ('{', '[]', '{"Office": {"Aux": "NaN"}}'):
                    state_path.write_text(invalid)
                    self.assertFalse(service.check_for_changes())
                    self.assertIs(service.api, previous_api)
                    self.assertEqual(adapter.apply_routes.call_count, calls)
                    self.assertEqual(state_path.read_text(), invalid)

                state_path.write_text('{"Office": {"Aux": -3}}')
                self.assertTrue(service.check_for_changes())
                config_path.write_text("zones: [")
                calls = adapter.apply_routes.call_count
                self.assertFalse(service.check_for_changes())
                self.assertEqual(adapter.apply_routes.call_count, calls)

    def test_deleted_zone_disconnects_old_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            state_path = Path(directory) / "routes.json"
            config_path.write_text("inputs:\n  - name: Aux\nzones:\n  - name: Office\n    device: Test\n")
            state_path.write_text('{"Office": {"Aux": 0}}')
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
                self.assertEqual(state_path.read_text(), '{"Office": {"Aux": 0}}')

    def test_run_keeps_polling_until_stopped(self):
        service = PiAmpService("config.yaml", "routes.json")
        stop_event = Mock(spec=Event)
        stop_event.is_set.side_effect = [False, False, False, True]
        with patch.object(service, "check_for_changes") as check:
            service.run(stop_event)
        self.assertEqual(check.call_count, 3)
        self.assertEqual(stop_event.wait.call_count, 3)
        stop_event.wait.assert_called_with(1.0)

    def test_pipewire_command_failure_is_logged_without_success(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            state_path = Path(directory) / "routes.json"
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


if __name__ == "__main__":
    unittest.main()