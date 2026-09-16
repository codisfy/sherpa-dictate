import unittest

import numpy as np

from sherpa_dictate import split_offline_chunks


SAMPLE_RATE = 16000


def make_audio(total_seconds: float, split_seconds: float = 15.0) -> np.ndarray:
    """Loud noise before split_seconds, silence afterwards."""
    total = int(total_seconds * SAMPLE_RATE)
    boundary = min(int(split_seconds * SAMPLE_RATE), total)
    audio = np.zeros(total, dtype=np.float32)
    audio[:boundary] = np.sin(
        np.arange(boundary, dtype=np.float32) * 0.1
    ) * 0.5
    return audio


class SplitOfflineChunksTests(unittest.TestCase):
    def test_short_audio_returns_single_chunk(self) -> None:
        audio = make_audio(10)
        chunks = split_offline_chunks(audio, SAMPLE_RATE)
        self.assertEqual(len(chunks), 1)
        self.assertIs(chunks[0], audio)

    def test_long_audio_is_split_within_limit(self) -> None:
        audio = make_audio(75)
        chunks = split_offline_chunks(audio, SAMPLE_RATE, max_chunk_seconds=30)
        self.assertGreater(len(chunks), 1)
        max_samples = int(30 * SAMPLE_RATE)
        for chunk in chunks:
            self.assertLessEqual(chunk.size, max_samples)
        self.assertEqual(sum(chunk.size for chunk in chunks), audio.size)

    def test_split_prefers_quiet_region(self) -> None:
        audio = make_audio(75, split_seconds=20)
        chunks = split_offline_chunks(audio, SAMPLE_RATE, max_chunk_seconds=30)
        first_end = chunks[0].size
        # The first chunk should not cut into the loud region if it can end
        # in the silence after 20 s instead.
        self.assertGreaterEqual(first_end, int(15 * SAMPLE_RATE))
        self.assertLessEqual(first_end, int(25.1 * SAMPLE_RATE))
        split_segment = audio[first_end - 1 : first_end + 1]
        self.assertLess(float(np.max(np.abs(split_segment))), 0.01)

    def test_all_loud_audio_still_splits(self) -> None:
        audio = np.ones(75 * SAMPLE_RATE, dtype=np.float32) * 0.5
        chunks = split_offline_chunks(audio, SAMPLE_RATE, max_chunk_seconds=30)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(sum(chunk.size for chunk in chunks), audio.size)
        self.assertLessEqual(max(chunk.size for chunk in chunks), 30 * SAMPLE_RATE)


if __name__ == "__main__":
    unittest.main()
