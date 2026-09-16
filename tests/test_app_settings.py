import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sherpa_app.settings import (
    default_settings,
    load_settings,
    remember_model_path,
    resolve_model_path,
    save_settings,
)


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.settings_file = Path(self.temporary_directory.name) / "settings.json"
        self.environment = patch.dict(
            os.environ,
            {"SHERPA_SETTINGS_PATH": str(self.settings_file)},
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_missing_file_uses_defaults(self) -> None:
        settings = load_settings()
        self.assertEqual(settings["model_paths"], {})
        self.assertIn("model_storage_dir", settings)

    def test_settings_are_saved_atomically_and_merged(self) -> None:
        settings = default_settings()
        settings["active_model"] = "nemotron-en"
        settings["start_minimized"] = True
        settings["dictation"] = {"silence_ms": 900}
        save_settings(settings)

        loaded = load_settings()
        self.assertTrue(loaded["start_minimized"])
        self.assertEqual(loaded["active_model"], "nemotron-en")
        self.assertEqual(loaded["dictation"]["silence_ms"], 900)
        self.assertFalse(self.settings_file.with_suffix(".json.tmp").exists())

    def test_invalid_sections_are_replaced_with_safe_defaults(self) -> None:
        self.settings_file.write_text(
            json.dumps({"dictation": "bad", "tts": [], "model_paths": None}),
            encoding="utf-8",
        )
        loaded = load_settings()
        self.assertEqual(loaded["dictation"], {})
        self.assertEqual(loaded["tts"], {})
        self.assertEqual(loaded["model_paths"], {})

    def test_remembered_model_path_overrides_fallback(self) -> None:
        model = Path(self.temporary_directory.name) / "model"
        remember_model_path("example", model)
        self.assertEqual(resolve_model_path("example", Path("fallback")), model.resolve())


if __name__ == "__main__":
    unittest.main()
