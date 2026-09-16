"""Download, validate, and install models without loading the inference stack."""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .catalog import ModelSpec, legacy_model_path
from .settings import load_settings, remember_model_path


ProgressCallback = Callable[[int, int], None]


class DownloadCancelled(RuntimeError):
    pass


def is_model_complete(spec: ModelSpec, path: Path) -> bool:
    return all((path / relative).exists() for relative in spec.required_paths)


def installed_model_path(spec: ModelSpec) -> Path:
    settings = load_settings()
    paths = settings.get("model_paths", {})
    if isinstance(paths, dict) and paths.get(spec.id):
        return Path(str(paths[spec.id])).expanduser()

    legacy = legacy_model_path(spec.id)
    if is_model_complete(spec, legacy):
        return legacy

    storage = Path(str(settings["model_storage_dir"])).expanduser()
    return storage / spec.id


def _safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with tarfile.open(archive, mode="r:*") as tar:
        for member in tar.getmembers():
            member_path = (destination / member.name).resolve()
            if member_path != destination_resolved and destination_resolved not in member_path.parents:
                raise ValueError(f"Unsafe path in model archive: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"Links are not allowed in model archives: {member.name}")
        tar.extractall(destination, filter="data")


def _find_model_directory(root: Path, spec: ModelSpec) -> Path:
    candidates = [root, *(path for path in root.rglob("*") if path.is_dir())]
    for candidate in candidates:
        if is_model_complete(spec, candidate):
            return candidate
    expected = ", ".join(spec.required_paths)
    raise ValueError(f"Downloaded archive does not contain the expected model files: {expected}")


def download_model(
    spec: ModelSpec,
    storage_directory: Path,
    progress: ProgressCallback | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    storage_directory = storage_directory.expanduser().resolve()
    storage_directory.mkdir(parents=True, exist_ok=True)
    final_path = storage_directory / spec.id
    if is_model_complete(spec, final_path):
        remember_model_path(spec.id, final_path)
        return final_path

    downloads = storage_directory / ".downloads"
    downloads.mkdir(exist_ok=True)
    archive = downloads / (spec.archive_name + ".part")
    extraction_root = Path(tempfile.mkdtemp(prefix=f".{spec.id}-", dir=storage_directory))

    try:
        request = urllib.request.Request(
            spec.url,
            headers={"User-Agent": "Sherpa-Desktop/0.1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            total = int(response.headers.get("Content-Length", "0"))
            received = 0
            while True:
                if cancelled and cancelled():
                    raise DownloadCancelled("Download cancelled")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)

        _safe_extract(archive, extraction_root)
        extracted_model = _find_model_directory(extraction_root, spec)
        staged_path = storage_directory / f".{spec.id}.installing"
        if staged_path.exists():
            shutil.rmtree(staged_path)
        if extracted_model == extraction_root:
            os.replace(extraction_root, staged_path)
        else:
            os.replace(extracted_model, staged_path)

        if final_path.exists():
            shutil.rmtree(final_path)
        os.replace(staged_path, final_path)
        if not is_model_complete(spec, final_path):
            raise RuntimeError("Model installation did not pass validation")
        remember_model_path(spec.id, final_path)
        return final_path
    finally:
        archive.unlink(missing_ok=True)
        if extraction_root.exists():
            shutil.rmtree(extraction_root, ignore_errors=True)
