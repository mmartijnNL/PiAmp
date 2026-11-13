import io
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np

from app.meter import (
    MeterEntry,
    StreamReader,
    StreamSpec,
    TerminalMeter,
    levels_from_frame,
    plan_streams,
    read_cpu_temp_celsius,
    render_bar,
    resolve_streams,
)
from app.models import InputDefinition, SystemConfig, ZoneDefinition


def _pcm_bytes(samples: list[list[float]]) -> bytes:
    return np.array(samples, dtype="<f4").tobytes()


class LevelsFromFrameTests(unittest.TestCase):
    def test_computes_rms_dbfs_per_channel(self):
        silence = _pcm_bytes([[0.0, 0.0]] * 10)
        full_scale = _pcm_bytes([[1.0, -1.0]] * 10)

        silent_levels = levels_from_frame(silence, channels=2)
        loud_levels = levels_from_frame(full_scale, channels=2)

        self.assertTrue(np.all(silent_levels <= -100))
        np.testing.assert_allclose(loud_levels, [0.0, 0.0], atol=1e-5)


SAMPLE_CONFIG = SystemConfig(
    inputs=(
        InputDefinition(name="Pi", source={"device": "Built-in Audio Stereo", "channels": [0, 1]}),
        InputDefinition(name="GameCube", source={"device": "UMC1820 Pro", "channels": [4, 5]}),
        InputDefinition(name="TV", source={"device": "UMC1820 Pro", "channels": [2, 3]}),
    ),
    zones=(
        ZoneDefinition(name="Bureau Martijn", device="UMC1820 Pro", channels=[2, 3]),
        ZoneDefinition(name="WC", device="UMC1820 Pro", channels=[4, 5]),
    ),
)


class PlanStreamsTests(unittest.TestCase):
    def test_groups_shared_device_entries_and_sizes_channels_to_cover_configured_indices(self):
        streams, entries = plan_streams(
            SAMPLE_CONFIG,
            source_devices=["UMC1820 Pro"],
            sink_devices=["UMC1820 Pro", "Built-in Audio Stereo"],
        )

        # GameCube (ch 4,5) and TV (ch 2,3) are both sourced from the UMC1820 Pro
        # *source* capture, so they should share a single stream sized for index 5.
        input_stream_names = {entry.stream_name for entry in entries if entry.label in ("GameCube", "TV")}
        self.assertEqual(len(input_stream_names), 1)
        input_stream = next(s for s in streams if s.name in input_stream_names)
        self.assertEqual(input_stream.target, "UMC1820 Pro")
        self.assertIsNone(input_stream.properties)
        self.assertEqual(input_stream.channels, 6)

        # Both zones share the UMC1820 Pro *sink monitor* capture, a distinct
        # stream from the plain source capture used for the inputs above.
        zone_stream_names = {entry.stream_name for entry in entries if entry.side == "zone"}
        self.assertEqual(len(zone_stream_names), 1)
        self.assertNotEqual(zone_stream_names, input_stream_names)
        zone_stream = next(s for s in streams if s.name in zone_stream_names)
        self.assertEqual(zone_stream.properties, "{ stream.capture.sink = true }")

    def test_input_device_known_only_as_sink_is_captured_as_sink_monitor(self):
        streams, entries = plan_streams(
            SAMPLE_CONFIG,
            source_devices=["UMC1820 Pro"],
            sink_devices=["UMC1820 Pro", "Built-in Audio Stereo"],
        )

        pi_entry = next(entry for entry in entries if entry.label == "Pi")
        pi_stream = next(s for s in streams if s.name == pi_entry.stream_name)
        self.assertEqual(pi_stream.target, "Built-in Audio Stereo")
        self.assertEqual(pi_stream.properties, "{ stream.capture.sink = true }")

    def test_entries_preserve_only_configured_channel_indices(self):
        _streams, entries = plan_streams(
            SAMPLE_CONFIG, source_devices=["UMC1820 Pro"], sink_devices=["UMC1820 Pro", "Built-in Audio Stereo"]
        )

        gamecube = next(entry for entry in entries if entry.label == "GameCube")
        self.assertEqual(gamecube.indices, (4, 5))
        bureau = next(entry for entry in entries if entry.label == "Bureau Martijn")
        self.assertEqual(bureau.indices, (2, 3))


class ReadCpuTempTests(unittest.TestCase):
    def test_reads_millidegrees_as_celsius(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "temp"
            path.write_text("53214\n")
            self.assertAlmostEqual(read_cpu_temp_celsius(path), 53.214)

    def test_returns_none_when_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(read_cpu_temp_celsius(Path(directory) / "missing"))


class ResolveStreamsTests(unittest.TestCase):
    def test_selects_exact_source_and_sink_with_native_channel_maps(self):
        nodes = [
            {"node.name": "umc.sink", "node.description": "UMC1820 Pro",
             "media.class": "Audio/Sink", "audio.channels": 12,
             "audio.position": ",".join(f"AUX{i}" for i in range(12))},
            {"node.name": "umc.source", "node.description": "UMC1820 Pro",
             "media.class": "Audio/Source", "audio.channels": 10,
             "audio.position": [f"AUX{i}" for i in range(10)]},
            {"node.name": "builtin.sink", "node.description": "Built-in Audio Stereo",
             "media.class": "Audio/Sink", "audio.channels": 2, "audio.position": "FL,FR"},
        ]
        output = json.dumps([
            {"type": "PipeWire:Interface:Node", "info": {"props": node}} for node in nodes
        ])
        streams, entries = plan_streams(
            SAMPLE_CONFIG, ["UMC1820 Pro"], ["UMC1820 Pro", "Built-in Audio Stereo"]
        )
        resolved = resolve_streams(streams, output)
        self.assertEqual([s.target for s in resolved], ["builtin.sink", "umc.source", "umc.sink"])
        self.assertEqual([s.channels for s in resolved], [2, 10, 12])
        for spec in resolved:
            command = spec.command(48000, 4800)
            self.assertEqual(command[command.index("--channel-map") + 1], ",".join(spec.channel_map))
            self.assertIn("stream.dont-remix = true", spec.properties)
        self.assertNotIn("stream.capture.sink", resolved[1].properties)
        self.assertIn("stream.capture.sink = true", resolved[2].properties)

        # Native ten-channel PCM must keep TV (2/3) and GameCube (4/5) distinct.
        spec = resolved[1]
        samples = [[0.0, 0.0, 0.5, 0.25, 1.0, 0.125, 0.0, 0.0, 0.0, 0.0]] * 2
        levels_queue = queue.Queue(maxsize=1)
        StreamReader(spec, io.BytesIO(_pcm_bytes(samples)), 2, levels_queue).run()
        meter = TerminalMeter(streams=resolved, entries=entries)
        meter.levels[spec.name] = levels_queue.get_nowait()
        lines = meter._render_entry_column("input", 20)
        self.assertIn("L      0 dB |", lines[4])
        self.assertIn("R    -18 dB |", lines[5])
        self.assertIn("L     -6 dB |", lines[7])
        self.assertIn("R    -12 dB |", lines[8])

    def test_rejects_missing_devices_and_unavailable_channels(self):
        spec = StreamSpec("input", "Test", 6)
        with self.assertRaisesRegex(ValueError, "meter device not found"):
            resolve_streams([spec], "[]")
        node = {"type": "PipeWire:Interface:Node", "info": {"props": {
            "node.name": "Test", "media.class": "Audio/Source",
            "audio.channels": 2, "audio.position": "FL,FR",
        }}}
        with self.assertRaisesRegex(ValueError, "unavailable configured channel"):
            resolve_streams([spec], json.dumps([node]))


class RenderBarTests(unittest.TestCase):
    def test_clamps_to_width_bounds(self):
        self.assertEqual(render_bar(-60.0), "")
        self.assertEqual(render_bar(0.0), "#" * 50)
        self.assertEqual(render_bar(-120.0), "")
        self.assertEqual(render_bar(60.0), "#" * 50)

    def test_scales_within_range(self):
        self.assertEqual(render_bar(-30.0), "#" * 25)


class StreamReaderTests(unittest.TestCase):
    def test_publishes_decoded_levels_until_stream_closes(self):
        spec = StreamSpec(name="TEST", target="dummy", channels=2)
        frames = 2
        block = _pcm_bytes([[1.0, -1.0]] * frames)
        stdout = io.BytesIO(block + block)
        level_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1)

        reader = StreamReader(spec, stdout, frames, level_queue)
        reader.run()

        levels = level_queue.get_nowait()
        np.testing.assert_allclose(levels, [0.0, 0.0], atol=1e-5)
        self.assertTrue(level_queue.empty())

    def test_publish_drops_stale_reading_when_queue_full(self):
        spec = StreamSpec(name="TEST", target="dummy", channels=1)
        level_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1)
        reader = StreamReader(spec, io.BytesIO(b""), frames=1, level_queue=level_queue)

        reader.publish(np.array([-10.0]))
        reader.publish(np.array([-5.0]))

        self.assertEqual(level_queue.get_nowait(), -5.0)


class TerminalMeterTests(unittest.TestCase):
    def test_readout_rounds_levels_to_whole_decibels(self):
        spec = StreamSpec(name="TEST", target="dummy", channels=3)
        entry = MeterEntry(label="Pi", side="input", stream_name="TEST", indices=(0, 1))
        meter = TerminalMeter(streams=[spec], entries=[entry])
        meter.levels["TEST"] = np.array([-12.3, -12.7, 0.0])

        lines = meter.render_lines()

        self.assertEqual(lines[0], "Pi")
        self.assertIn(f"L {-12:6.0f} dB |", lines[1])
        self.assertIn(f"R {-13:6.0f} dB |", lines[2])
        self.assertFalse(any("." in line for line in lines[:3]))

    def test_render_lines_only_shows_configured_channels_inputs_left_zones_right(self):
        spec = StreamSpec(name="S", target="dummy", channels=6)
        entries = [
            MeterEntry(label="TV", side="input", stream_name="S", indices=(2, 3)),
            MeterEntry(label="Bureau Martijn", side="zone", stream_name="S", indices=(4, 5)),
        ]
        meter = TerminalMeter(streams=[spec], entries=entries)
        meter.levels["S"] = np.array([-60.0, -60.0, -10.0, -20.0, -30.0, -40.0])

        header_line = meter.render_lines()[0]
        self.assertEqual(header_line.split("   ")[0], "TV")
        self.assertTrue(header_line.endswith("Bureau Martijn"))
        self.assertNotIn("-", header_line)
        self.assertLess(header_line.index("TV"), header_line.index("Bureau Martijn"))

        joined = "\n".join(meter.render_lines())
        for level in (-10, -20, -30, -40):
            self.assertIn(f"{level:6.0f} dB", joined)
        self.assertNotIn(f"{-60:6.0f} dB", joined)

    def test_status_block_stays_fixed_size_regardless_of_message_history(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_path = Path(directory) / "temp"
            temp_path.write_text("42000")
            meter = TerminalMeter(
                streams=[], entries=[], footer=lambda: ["Routes active: 2", "Last: ok"], cpu_temp_path=temp_path
            )

            first_render = meter.render_lines()
            second_render = meter.render_lines()

            self.assertEqual(len(first_render), len(second_render))
            self.assertIn("CPU temp: 42.0\u00b0C", first_render)
            self.assertIn("Routes active: 2", first_render)

    def _fake_proc(self, stdout: io.BytesIO) -> Mock:
        proc = Mock()
        proc.stdout = stdout
        return proc

    def test_start_launches_one_pw_record_process_per_planned_stream(self):
        streams, entries = plan_streams(
            SAMPLE_CONFIG, source_devices=["UMC1820 Pro"], sink_devices=["UMC1820 Pro", "Built-in Audio Stereo"]
        )
        popen = Mock(side_effect=lambda *a, **k: self._fake_proc(io.BytesIO(b"")))
        meter = TerminalMeter(streams=streams, entries=entries, popen=popen)
        meter.start()

        self.assertEqual(popen.call_count, len(streams))
        targets = {call.args[0][call.args[0].index("--target") + 1] for call in popen.call_args_list}
        self.assertEqual(targets, {"UMC1820 Pro", "Built-in Audio Stereo"})
        meter.stop()

    def test_run_renders_collected_levels_until_stopped(self):
        block = _pcm_bytes([[1.0, -1.0]] * 2)
        spec = StreamSpec(name="TEST", target="dummy", channels=2)
        popen = Mock(side_effect=lambda *a, **k: self._fake_proc(io.BytesIO(block * 5)))
        meter = TerminalMeter(streams=[spec], entries=[], frames=2, refresh=0.0, popen=popen)

        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        stop_event.wait.return_value = None

        meter.run(stop_event)

        self.assertIsNotNone(meter.levels["TEST"])
        np.testing.assert_allclose(meter.levels["TEST"], [0.0, 0.0], atol=1e-5)


if __name__ == "__main__":
    unittest.main()
