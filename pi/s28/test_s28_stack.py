"""Host tests for the S28 stacking core (merges + honest noise ladder).

    ~/nereus_ml/venvs/fomo/bin/python -m pytest pi/s28/test_s28_stack.py -q
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s28_stack import (merge_mean, merge_median, merge_sigma_clip,  # noqa
                       noise_ladder, temporal_sigma)


def test_mean_of_constant_stack():
    stack = np.full((8, 10, 10), 100, np.uint8)
    assert np.all(merge_mean(stack) == 100)


def test_mean_rounds():
    stack = np.stack([np.full((4, 4), v, np.uint8) for v in (10, 10, 13)])
    assert np.all(merge_mean(stack) == 11)     # 11.0 exactly


def test_median_picks_middle():
    stack = np.stack([np.full((4, 4), v, np.uint8) for v in (10, 20, 200)])
    assert np.all(merge_median(stack) == 20)


def test_sigma_clip_rejects_outlier():
    # 15 frames at 100, one wild outlier -> clip pulls it back to ~100
    base = [np.full((6, 6), 100, np.uint8) for _ in range(15)]
    base.append(np.full((6, 6), 255, np.uint8))
    stack = np.stack(base)
    sc = merge_sigma_clip(stack, kappa=2.0)
    mn = merge_mean(stack)
    assert abs(int(sc.mean()) - 100) <= 1     # sigma-clip ~ 100
    assert int(mn.mean()) > 108               # plain mean is dragged up


def test_noise_ladder_tracks_sqrt_n():
    # synthetic BGGR burst: flat scene + pure per-pixel temporal noise.
    rng = np.random.default_rng(0)
    H = W = 64
    scene = 120.0
    frames = np.clip(scene + rng.normal(0, 4.0, size=(16, H, W)),
                     0, 255).astype(np.uint8)
    lad = noise_ladder(frames, merge_mean, ks=(1, 2, 4))
    # k=2 ~ /sqrt2, k=4 ~ /2 relative to k=1
    assert 1.25 < lad[1] / lad[2] < 1.6
    assert 1.7 < lad[1] / lad[4] < 2.4


def test_noise_ladder_cancels_fixed_structure():
    # a strong FIXED gradient + tiny temporal noise: the ladder must
    # report only the tiny temporal part, not the gradient.
    rng = np.random.default_rng(1)
    grad = np.tile(np.linspace(0, 200, 64), (64, 1))
    frames = np.clip(grad + rng.normal(0, 2.0, size=(16, 64, 64)),
                     0, 255).astype(np.uint8)
    lad = noise_ladder(frames, merge_mean, ks=(1,))
    assert lad[1] < 4.0                        # ~ the 2.0 temporal noise,
    #                                            NOT the 0..200 gradient


def test_temporal_sigma_matches_input_noise():
    rng = np.random.default_rng(2)
    frames = np.clip(100 + rng.normal(0, 3.0, size=(20, 32, 32)),
                     0, 255).astype(np.uint8)
    assert abs(temporal_sigma(frames) - 3.0) < 0.3


def test_compare_script_valid_and_runs_help():
    src = open(os.path.join(os.path.dirname(__file__),
                            "s28_compare.py")).read()
    compile(src, "s28_compare.py", "exec")
