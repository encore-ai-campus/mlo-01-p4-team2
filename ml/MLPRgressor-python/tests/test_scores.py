import unittest
import numpy as np
from data import features
from errors import JobError
from model import target, weights
from helpers import row


class ScoreTests(unittest.TestCase):
    def test_reward_five_total_points(self):
        np.testing.assert_allclose(target(features(row())), np.array([14, 15, 4]) / 33)
        np.testing.assert_array_equal(target([0] * 14), [0, 0, 0])

    def test_integer_contract(self):
        samples = [[.6, .5, .3], [1, 1, 1], [-1, 0, 5], [0, 0, 0], [.335] * 3]
        samples += list(np.random.default_rng(42).uniform(-2, 2, (1000, 3)))
        for values in samples:
            actual = weights(values)
            self.assertTrue(all(type(v) is int and 0 <= v <= 100 for v in actual))
            self.assertLessEqual(sum(actual), 100)
        self.assertEqual(weights([.6, .5, .3]), [43, 36, 21])
        self.assertEqual(weights([0, 0, 0]), [0, 0, 0])

    def test_invalid_model_output(self):
        for bad in ([0, 0], [float('nan'), 0, 0], [float('inf'), 0, 0], ['1', '0', '0'], [True, False, True]):
            with self.assertRaises(JobError):
                weights(bad)
