"""Approved training targets and storage weights; learning is added in CP02."""
import numpy as np

from errors import JobError

TARGET_RULE = 'click1-converted5-normalized-v1'


def target(x):
    scores = np.array([x[i] * (1 + 4 * x[i + 1]) for i in (0, 2, 4)], dtype=float)
    return scores / scores.sum() if scores.sum() else np.zeros(3)


def weights(prediction):
    values = np.asarray(prediction)
    if values.shape != (3,) or values.dtype.kind not in 'iuf' or not np.isfinite(values).all():
        raise JobError('invalid_model_output')
    values = np.clip(values.astype(float), 0, 1) * 100
    if values.sum() > 100:
        values *= 100 / values.sum()
    result = np.floor(values + .5).astype(int)
    while result.sum() > 100:
        result[np.argmax(result - values)] -= 1
    return [int(v) for v in result]
