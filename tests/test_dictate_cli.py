import argparse
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import sherpa_dictate
import sherpa_entry


class DictateStatusTests(unittest.TestCase):
    @patch("sherpa_dictate.notify")
    @patch("sherpa_dictate.send_with_autostart")
    def test_status_prints_json_without_notification(
        self,
        send_with_autostart,
        notify,
    ) -> None:
        send_with_autostart.return_value = {
            "ok": True,
            "state": "listening",
            "mode": "continuous",
            "model_name": "parakeet",
            "message": "Listening (continuous)",
        }
        output = io.StringIO()
        with patch.object(sherpa_dictate, "SOCKET_PATH", Path(__file__)), redirect_stdout(output):
            result = sherpa_dictate.client_main(argparse.Namespace(command="status"))

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["state"], "listening")
        notify.assert_not_called()

    @patch("sherpa_entry._run_engine", return_value=0)
    def test_public_start_action_uses_non_toggling_command(self, run_engine) -> None:
        result = sherpa_entry.main(["action", "dictation-start"])
        self.assertEqual(result, 0)
        run_engine.assert_called_once_with("dictate", ["continuous-start"])

    def test_continuous_start_does_not_use_toggle(self) -> None:
        engine = Mock()
        engine.start_continuous.return_value = {"ok": True, "state": "listening"}
        response, should_stop = sherpa_dictate.handle_request(
            engine,
            {"action": "continuous-start", "paste": True},
        )
        self.assertEqual(response["state"], "listening")
        self.assertFalse(should_stop)
        engine.start_continuous.assert_called_once_with(paste=True)
        engine.toggle_continuous.assert_not_called()

    def test_restart_refuses_to_interrupt_active_dictation(self) -> None:
        with (
            patch.object(sherpa_dictate, "SOCKET_PATH") as socket_path,
            patch("sherpa_dictate.request_daemon") as request_daemon,
            patch("sherpa_dictate.start_daemon") as start_daemon,
        ):
            socket_path.exists.return_value = True
            request_daemon.return_value = {"ok": True, "state": "listening"}
            with redirect_stderr(io.StringIO()):
                result = sherpa_dictate.restart_client()

        self.assertEqual(result, 1)
        request_daemon.assert_called_once_with({"action": "status"}, timeout=3)
        start_daemon.assert_not_called()


if __name__ == "__main__":
    unittest.main()
