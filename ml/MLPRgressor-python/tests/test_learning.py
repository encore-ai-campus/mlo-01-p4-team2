import unittest
from unittest.mock import Mock, patch
import numpy as np

from data import features
from errors import JobError
from model import train, predict
from helpers import row


class LearningTests(unittest.TestCase):
    def test_initial_and_incremental_learning(self):
        x = [features(row()), features(row(category=1, mlo_ctr=.2))]
        first = train(None, x, 2)
        self.assertEqual((first.n_features_in_, first.n_outputs_), (14, 3))
        before = first.t_
        second = train(first, x, 2)
        self.assertIs(second, first)
        self.assertGreater(second.t_, before)
        self.assertEqual(predict(second, x).shape, (2, 3))

    def test_no_model_created_for_empty_rows(self):
        with patch('sklearn.neural_network.MLPRegressor') as constructor, self.assertRaises(JobError):
            train(None, [], 2)
        constructor.assert_not_called()

    def test_invalid_input_and_prediction(self):
        for x in ([[0] * 13], [[float('nan')] * 14]):
            with self.assertRaises(JobError):
                train(None, x, 1)
        for result in (np.zeros((1, 2)), np.array([[float('nan'), 0, 0]])):
            with self.assertRaises(JobError):
                predict(Mock(predict=Mock(return_value=result)), [[0] * 14])
