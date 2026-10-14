import unittest

import numpy as np

from app.ear_sequence import EarSequenceBuffer, WINDOW_SECONDS


class EarSequenceTests(unittest.TestCase):
    def test_equal_time_windows_at_different_camera_rates(self):
        sequences = []
        for rate in (10, 30):
            buffer = EarSequenceBuffer()
            for timestamp in np.linspace(0, 2, 2 * rate + 1):
                buffer.append(timestamp, 0.2 + timestamp * 0.05)
            sequences.append(buffer.sequence())
        self.assertEqual(sequences[0].shape, (30, 1))
        np.testing.assert_allclose(sequences[0], sequences[1])
        self.assertAlmostEqual(sequences[0][0, 0], 0.2 + (2 - WINDOW_SECONDS) * 0.05)

    def test_missing_observations_require_a_new_window(self):
        buffer = EarSequenceBuffer()
        for timestamp in np.linspace(0, 2, 21):
            buffer.append(timestamp, 0.3)
        self.assertIsNotNone(buffer.sequence())
        buffer.append(3, 0.1)
        self.assertIsNone(buffer.sequence())

    def test_short_window_does_not_repeat_samples_to_fake_readiness(self):
        buffer = EarSequenceBuffer()
        for timestamp in np.linspace(0, 0.5, 31):
            buffer.append(timestamp, 0.3)
        self.assertIsNone(buffer.sequence())
