"""Coordinate reader playback with microphone capture across Linux daemons."""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


PLAYBACK_TAIL_SECONDS = 0.3


def _open_activity_file(runtime_dir: Path) -> int:
    # Keep this file in place: unlinking it could give the daemons different locks.
    return os.open(runtime_dir / "sherpa-tts-playback.lock", os.O_CREAT | os.O_RDWR, 0o600)


@contextmanager
def tts_playback(runtime_dir: Path) -> Iterator[None]:
    """Hold activity until output closes and its brief acoustic tail fades.

    The OS releases the lock if the reader exits unexpectedly, so dictation
    cannot remain muted by a stale activity flag.
    """
    descriptor = _open_activity_file(runtime_dir)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            time.sleep(PLAYBACK_TAIL_SECONDS)
    finally:
        os.close(descriptor)


class TtsPlaybackMonitor:
    """Check activity without waiting or opening files in the audio callback."""

    def __init__(self, runtime_dir: Path) -> None:
        self.descriptor = _open_activity_file(runtime_dir)

    def active(self) -> bool:
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        return False

    def close(self) -> None:
        os.close(self.descriptor)
