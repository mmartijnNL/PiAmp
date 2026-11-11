import io
import queue
import threading
import unittest
from unittest.mock import Mock

import numpy as np

from app.meter import (
    DEFAULT_STREAMS,
    StreamReader,
    StreamSpec,
    TerminalMeter,
    levels_from_frame,
    render_bar,
)


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
        meter = TerminalMeter(streams=[spec])
        meter.levels["TEST"] = np.array([-12.3, -12.7, 0.0])

        lines = meter.render_lines()

        self.assertIn("01    -12 dB |", lines[1])
        self.assertIn("02    -13 dB |", lines[2])
        self.assertIn("03      0 dB |", lines[3])
        self.assertFalse(any("." in line for line in lines))

    def _fake_proc(self, stdout: io.BytesIO) -> Mock:
        proc = Mock()
        proc.stdout = stdout
        return proc

    def test_start_launches_pw_record_per_stream_with_expected_command(self):
        popen = Mock(side_effect=lambda *a, **k: self._fake_proc(io.BytesIO(b"")))
        meter = TerminalMeter(popen=popen)
        meter.start()

        self.assertEqual(popen.call_count, len(DEFAULT_STREAMS))
        first_command = popen.call_args_list[0].args[0]
        self.assertIn("pw-record", first_command)
        self.assertIn(DEFAULT_STREAMS[0].target, first_command)
        meter.stop()

    def test_run_renders_collected_levels_until_stopped(self):
        block = _pcm_bytes([[1.0, -1.0]] * 2)
        spec = StreamSpec(name="TEST", target="dummy", channels=2)
        popen = Mock(side_effect=lambda *a, **k: self._fake_proc(io.BytesIO(block * 5)))
        meter = TerminalMeter(streams=[spec], frames=2, refresh=0.0, popen=popen)

        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        stop_event.wait.return_value = None

        meter.run(stop_event)

        self.assertIsNotNone(meter.levels["TEST"])
        np.testing.assert_allclose(meter.levels["TEST"], [0.0, 0.0], atol=1e-5)


if __name__ == "__main__":
    unittest.main()
