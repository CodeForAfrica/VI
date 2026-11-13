"""Prediction-powered inference (AIPW) for the Vulnerability Index content signal.

Follows the notebook's "AIPW per (actor, country, intent)" cell:

    theta[a, c, k] = mean over articles i of pair (a, c) of
                     m_ik + (xi_i / pi_ac) * (y_ik - m_ik)

    m_ik   calibrated model probability that article i carries intent k
    y_ik   1 if the human label of article i is intent k, else 0
    xi_i   1 if article i is a held-out calibration anchor with a human label
    pi_ac  share of the pair's articles that are such anchors

theta is the estimated share of the pair's articles that carry intent k, with
the model's bias corrected by the human labels.
"""
import numpy as np


def _clip(x):
    return float(min(1.0, max(0.0, x)))


def intent_vector(intent_probs, predicted_intent, confidence, intents, map_fn):
    """Probability per index intent for one article.

    Returns (vector, used_fallback) or (None, False) if nothing usable.
    - With intent_probs (all classes): raw classes are mapped to index intents
      and summed; classes that map to no intent are left out, as in the notebook.
    - Without it (articles classified before probabilities were stored): an
      approximation from the top label and its confidence, the rest spread
      evenly over the other intents. Reported separately by the caller.
    """
    k = len(intents)
    index = {name: i for i, name in enumerate(intents)}
    if isinstance(intent_probs, dict) and intent_probs:
        vec = np.zeros(k)
        for label, p in intent_probs.items():
            target = map_fn(label)
            if target in index:
                vec[index[target]] += float(p)
        return np.clip(vec, 0.0, 1.0), False

    target = map_fn(predicted_intent) if predicted_intent else None
    if target not in index or confidence is None:
        return None, False
    conf = _clip(confidence)
    vec = np.full(k, (1.0 - conf) / (k - 1) if k > 1 else 0.0)
    vec[index[target]] = conf
    return vec, True


def label_vector(true_label, intents, map_fn):
    """One-hot vector of the human label over index intents (all zeros if the
    label maps to none of them)."""
    vec = np.zeros(len(intents))
    target = map_fn(true_label) if true_label else None
    if target in intents:
        vec[intents.index(target)] = 1.0
    return vec


def apply_calibration(vec, params, intents):
    """Apply per-intent isotonic breakpoints {"x": [...], "y": [...]}."""
    if not params:
        return vec
    out = vec.copy()
    for i, name in enumerate(intents):
        bp = params.get(name)
        if bp and len(bp.get("x", [])) >= 2:
            out[i] = float(np.interp(vec[i], bp["x"], bp["y"]))
    total = out.sum()
    if total > 1.0:          # keep it a probability distribution, as the notebook renormalises
        out = out / total
    return out


def aipw(m, y, xi):
    """AIPW estimate per intent for one pair.

    m:  (N, K) model probabilities     y: (N, K) human one-hot (rows of non-anchors ignored)
    xi: (N,) 1 for calibration anchors
    Returns (theta (K,), n_labelled, pi).
    """
    m = np.asarray(m, dtype=float)
    y = np.asarray(y, dtype=float)
    xi = np.asarray(xi, dtype=float)
    n = len(xi)
    n_lab = int(xi.sum())
    if n == 0:
        return None, 0, 0.0
    if n_lab == 0:
        return np.clip(m.mean(axis=0), 0.0, 1.0), 0, 0.0
    pi = n_lab / n
    theta = (m + (xi[:, None] / pi) * (y - m)).mean(axis=0)
    return np.clip(theta, 0.0, 1.0), n_lab, pi


def intent_priors(thetas, sizes):
    """Article-weighted share of each intent over all pairs: sum(N * theta) / sum(N)."""
    if not thetas:
        return None
    total = sum(sizes.values())
    return sum(sizes[key] * np.asarray(theta, dtype=float) for key, theta in thetas.items()) / total


def shrink(theta, n, prior, k=20.0):
    """Pull a pair's shares towards the prior in proportion to how few articles it has:
    (n * theta + k * prior) / (n + k). With n >> k the pair keeps its own estimate."""
    theta = np.asarray(theta, dtype=float)
    if prior is None or k <= 0:
        return theta
    return np.clip((n * theta + k * np.asarray(prior, dtype=float)) / (n + k), 0.0, 1.0)


def fit_isotonic(m, y, intents, min_positives=5):
    """Fit one isotonic map per intent (one-vs-rest), as in the notebook's
    calibration cell. Returns {intent: {"x": [...], "y": [...]}}; an intent with
    too few positive labels is left uncalibrated."""
    from sklearn.isotonic import IsotonicRegression
    params = {}
    for i, name in enumerate(intents):
        yk = y[:, i]
        if yk.sum() < min_positives or yk.sum() == len(yk):
            continue
        ir = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        ir.fit(m[:, i], yk)
        params[name] = {"x": [float(v) for v in ir.X_thresholds_],
                        "y": [float(v) for v in ir.y_thresholds_]}
    return params


def brier(m, y):
    return float(np.mean((np.asarray(m) - np.asarray(y)) ** 2))
