"""Coordinate reader playback with microphone capture across Linux daemons."""

from __future__ import annotations

import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


PLAYBACK_TAIL_SECONDS = 0.3


def _open_activity_file(runtime_dir: Path) -> int:
    # Keep this file in place: unlinking it could give the daemons different locks.
    return os.open(runtime_dir / "sherpa-tts-playback.lock", os.O_CREAT | os.O_RDWR, 0o600)


class TtsPlaybackActivity:
    """Hold playback activity while sound is audible, with support for pauses."""

    def __init__(self, runtime_dir: Path) -> None:
        self.descriptor = _open_activity_file(runtime_dir)
        self.lock = threading.Lock()
        self._active = False
        self._closed = False
        try:
            self.resume()
        except BaseException:
            os.close(self.descriptor)
            raise

    def resume(self) -> None:
        with self.lock:
            if not self._closed and not self._active:
                fcntl.flock(self.descriptor, fcntl.LOCK_EX)
                self._active = True

    def pause(self, output_latency: float = 0.0) -> None:
        with self.lock:
            if not self._closed and self._active:
                # The stream now emits silence. Let already scheduled audio
                # and its acoustic tail finish before allowing dictation.
                time.sleep(max(0.0, output_latency) + PLAYBACK_TAIL_SECONDS)
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
                self._active = False

    def close(self) -> None:
        with self.lock:
            if self._closed:
                return
            if self._active:
                time.sleep(PLAYBACK_TAIL_SECONDS)
            os.close(self.descriptor)
            self._closed = True
            self._active = False


@contextmanager
def tts_playback(runtime_dir: Path) -> Iterator[TtsPlaybackActivity]:
    """Release activity after output closes, including errors or reader exits.

    The OS releases the lock on a crash, preventing a stale muted state.
    """
    activity = TtsPlaybackActivity(runtime_dir)
    try:
        yield activity
    finally:
        activity.close()


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
