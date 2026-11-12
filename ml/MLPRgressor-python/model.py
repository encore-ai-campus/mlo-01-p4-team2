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


def train(previous, rows, epochs):
    from sklearn.neural_network import MLPRegressor
    x = np.asarray(rows, dtype=float)
    if x.size == 0:
        raise JobError('no_training_rows', deferred=True)
    if x.ndim != 2 or x.shape[1] != 14 or not np.isfinite(x).all():
        raise JobError('invalid_training_input')
    if type(epochs) is not int or epochs < 1:
        raise JobError('invalid_train_epochs')
    if previous is not None and (previous.n_features_in_ != 14 or previous.n_outputs_ != 3):
        raise JobError('model_input_output_mismatch')
    model = previous if previous is not None else MLPRegressor(
        hidden_layer_sizes=(32, 16), solver='adam', random_state=42,
        learning_rate_init=.001, alpha=.001, shuffle=True,
    )
    y = np.array([target(item) for item in x])
    for _ in range(epochs):
        model.partial_fit(x, y)
    return model


def predict(model, rows):
    result = np.asarray(model.predict(rows))
    if result.shape != (len(rows), 3) or result.dtype.kind not in 'iuf' or not np.isfinite(result).all():
        raise JobError('invalid_model_prediction')
    return result
