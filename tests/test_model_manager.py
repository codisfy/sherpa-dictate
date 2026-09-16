import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sherpa_app.catalog import ModelSpec
from sherpa_app.model_manager import _safe_extract, download_model, is_model_complete


class ModelManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.settings_file = self.root / "settings.json"
        self.environment = patch.dict(
            os.environ,
            {"SHERPA_SETTINGS_PATH": str(self.settings_file)},
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary_directory.cleanup()

    def _make_archive(self) -> Path:
        source = self.root / "source" / "upstream-name"
        source.mkdir(parents=True)
        for filename in ("model.onnx", "tokens.txt"):
            (source / filename).write_text(filename, encoding="utf-8")
        archive = self.root / "model.tar.bz2"
        with tarfile.open(archive, "w:bz2") as output:
            output.add(source, arcname="upstream-name")
        return archive

    def test_downloads_file_url_and_normalizes_directory_name(self) -> None:
        archive = self._make_archive()
        spec = ModelSpec(
            id="test-model",
            name="Test model",
            kind="asr",
            summary="Test",
            size="1 KB",
            url=archive.as_uri(),
            required_paths=("model.onnx", "tokens.txt"),
        )
        progress = []
        installed = download_model(
            spec,
            self.root / "models",
            progress=lambda received, total: progress.append((received, total)),
        )
        self.assertEqual(installed.name, "test-model")
        self.assertTrue(is_model_complete(spec, installed))
        self.assertTrue(progress)

    def test_rejects_archive_path_traversal(self) -> None:
        archive = self.root / "unsafe.tar"
        with tarfile.open(archive, "w") as output:
            member = tarfile.TarInfo("../outside.txt")
            payload = b"unsafe"
            member.size = len(payload)
            output.addfile(member, io.BytesIO(payload))

        destination = self.root / "extract"
        destination.mkdir()
        with self.assertRaises(ValueError):
            _safe_extract(archive, destination)
        self.assertFalse((self.root / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
