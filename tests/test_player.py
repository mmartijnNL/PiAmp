import signal
import threading
from pathlib import Path
from unittest.mock import Mock, patch

from app.models import InputDefinition, SystemConfig
from app.player import FolderPlayer, PlayerManager


def test_folder_decoder_waits_when_unrouted_and_resumes_same_process(tmp_path):
    (tmp_path / "track.wav").touch()
    decoder_started = threading.Event()
    decoder_done = threading.Event()
    sink = Mock()
    sink.poll.return_value = None
    decoder = Mock()
    decoder.poll.return_value = None
    decoder.wait.side_effect = lambda: decoder_done.wait(2)
    decoder.terminate.side_effect = decoder_done.set

    def start_decoder(*args, **kwargs):
        decoder_started.set()
        return decoder

    player = FolderPlayer("Jazz", tmp_path)
    with patch.object(player, "_start_sink", return_value=sink), patch(
        "app.player.subprocess.Popen", side_effect=start_decoder
    ) as popen:
        player.start()
        try:
            assert not decoder_started.wait(0.05)
            player.set_active(True)
            assert decoder_started.wait(1)
            player.set_active(False)
            decoder.send_signal.assert_called_with(signal.SIGSTOP)
            player.set_active(False)
            assert decoder.send_signal.call_count == 1
            player.set_active(True)
            decoder.send_signal.assert_called_with(signal.SIGCONT)
            assert popen.call_count == 1
            player.set_active(False)
        finally:
            player.stop()
        assert not player._thread.is_alive()
        assert decoder.send_signal.call_args_list[-1].args == (signal.SIGCONT,)
        sink.terminate.assert_called()


def test_folder_playback_is_active_while_any_zone_selects_it():
    manager = PlayerManager()
    player = Mock()
    manager._players = {"Jazz": player}
    manager.set_routes({"Office": {}, "WC": {"Jazz": -40}})
    player.set_active.assert_called_with(True)
    manager.set_routes({"Office": {}, "WC": {}})
    player.set_active.assert_called_with(False)


def test_replacement_player_inherits_current_route_activity():
    config = SystemConfig(inputs=(InputDefinition("Jazz", {"folder": "/music"}),))
    manager = PlayerManager()
    manager.set_routes({"WC": {"Jazz": -40}})
    with patch("app.player.FolderPlayer") as players:
        players.return_value.folder = Path("/music")
        manager.sync(config)
        players.return_value.set_active.assert_called_with(True)
