import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

import sherpa_dictate
from sherpa_dictate import DictationEngine


class MediaTranscriptionTests(unittest.TestCase):
    def test_common_media_is_decoded_to_mono_16khz_float_audio(self) -> None:
        engine = DictationEngine.__new__(DictationEngine)
        engine.listening = False
        engine._transcribe_audio = Mock(
            return_value={"ok": True, "text": "transcribed"}
        )
        decoded_audio = np.array([0.25, -0.25], dtype="<f4")

        def decode(command, **_kwargs):
            Path(command[-1]).write_bytes(decoded_audio.tobytes())
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=None, stderr=b""
            )

        with tempfile.NamedTemporaryFile(suffix=".mp4") as media_file, patch.object(
            sherpa_dictate, "np", np, create=True
        ), patch(
            "sherpa_dictate.shutil.which", return_value="/usr/bin/ffmpeg"
        ), patch(
            "sherpa_dictate.subprocess.run",
            side_effect=decode,
        ) as run:
            result = engine.transcribe_file(media_file.name)

        self.assertEqual(result["text"], "transcribed")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertIn("0:a:0", command)
        self.assertIn("pcm_f32le", command)
        samples, sample_rate = engine._transcribe_audio.call_args.args
        np.testing.assert_array_equal(samples, decoded_audio)
        self.assertEqual(sample_rate, 16000)

    def test_non_wav_media_explains_when_ffmpeg_is_missing(self) -> None:
        engine = DictationEngine.__new__(DictationEngine)
        engine.listening = False
        with tempfile.NamedTemporaryFile(suffix=".mp3") as media_file, patch(
            "sherpa_dictate.shutil.which", return_value=None
        ):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg is required"):
                engine.transcribe_file(media_file.name)


if __name__ == "__main__":
    unittest.main()
