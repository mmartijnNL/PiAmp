import io
from unittest.mock import Mock

import numpy as np
import pytest

from app.audio import InputLevelMonitor
from app.models import InputDefinition, SystemConfig
from app.ui import plan_streams


def test_detection_uses_configured_channels_and_preserves_activity_between_polls():
    config = SystemConfig(inputs=(
        InputDefinition("TV", {"device": "Device", "channels": [2, 3]}),
        InputDefinition("GameCube", {"device": "Device", "channels": [4, 5]}),
    ))
    monitor = InputLevelMonitor(config)
    streams, monitor.entries = plan_streams(config)
    active = np.zeros((4800, 6), dtype="<f4")
    active[:, 0] = 1.0
    active[:, 3] = 0.1
    silent = np.zeros((4800, 6), dtype="<f4")
    monitor._stop = Mock()
    monitor._stop.is_set.side_effect = [False, False, True]
    monitor._read(streams[0], io.BytesIO(active.tobytes() + silent.tobytes()))
    levels = monitor.read_levels()
    assert levels["TV"] == pytest.approx(-20, abs=0.001)
    assert levels["GameCube"] <= -100
    assert monitor.read_levels()["TV"] <= -100


def test_failed_or_stale_capture_is_an_error_not_silence():
    monitor = InputLevelMonitor(SystemConfig())
    monitor.entries = []
    monitor._errors["device"] = "capture ended"
    with pytest.raises(RuntimeError, match="capture ended"):
        monitor.read_levels()
    monitor._errors.clear()
    monitor._updated["device"] = 0
    with pytest.raises(RuntimeError, match="stopped delivering audio"):
        monitor.read_levels()
