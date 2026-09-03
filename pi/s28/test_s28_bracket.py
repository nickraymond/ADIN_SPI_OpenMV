"""Host tests for the S28 bite-3 red-channel bracket merge.

    ~/nereus_ml/venvs/fomo/bin/python -m pytest pi/s28/test_s28_bracket.py -q
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s28_bracket import (bayer_planes, clip_frac, merge_red_from_long,  # noqa
                         red_stack, temporal_noise)


def _bggr(R, G, B, h=8, w=8):
    """Build a BGGR frame (h,w) from constant channel levels."""
    f = np.zeros((h, w), np.uint8)
    f[0::2, 0::2] = B          # B site
    f[1::2, 1::2] = R          # R site
    f[0::2, 1::2] = G          # G sites
    f[1::2, 0::2] = G
    return f


def test_bayer_planes_pick_the_right_sites():
    f = _bggr(200, 100, 50)
    R, G, B = bayer_planes(f)
    assert np.all(R == 200) and np.all(G == 100) and np.all(B == 50)


def test_merge_takes_red_from_long_gb_from_normal():
    normal = _bggr(R=20, G=120, B=90)
    long = _bggr(R=80, G=250, B=250)      # 4x red, clipped g/b (ignored)
    R, G, B = merge_red_from_long(normal, long, ratio=4.0)
    assert np.allclose(R, 80 / 4)          # long red / ratio = 20
    assert np.allclose(G, 120) and np.allclose(B, 90)   # normal G/B


def test_temporal_noise_and_clip():
    rng = np.random.default_rng(0)
    # a red-plane stack with known noise
    reds = np.clip(40 + rng.normal(0, 3.0, size=(12, 16, 16)), 0, 255)
    assert abs(temporal_noise(reds) - 3.0) < 0.4
    hot = np.full((4, 8, 8), 255.0)
    assert clip_frac(hot) == 1.0
    assert clip_frac(np.zeros((4, 8, 8))) == 0.0


def test_red_merge_improves_snr_in_read_noise_limit():
    # read-noise-limited red: constant read noise sigma, signal scales
    # with exposure. Merged red (long/ratio) should have ratio-times
    # lower noise than the normal red -> improvement ~ ratio.
    rng = np.random.default_rng(1)
    ratio = 4.0
    read = 5.0
    # normal: dim red ~10 + read noise; long: 4x signal + SAME read noise
    normal = np.stack([_bggr(R=10, G=100, B=80) for _ in range(8)])
    long = np.stack([_bggr(R=40, G=200, B=200) for _ in range(8)])
    nr = red_stack(normal).astype(float) + rng.normal(0, read,
                                                      (8, 4, 4))
    lr = red_stack(long).astype(float) + rng.normal(0, read, (8, 4, 4))
    n_norm = float(nr.std(axis=0, ddof=1).mean())
    n_merged = float((lr / ratio).std(axis=0, ddof=1).mean())
    # merged red noise is ~read/ratio; improvement ~= ratio
    assert 3.0 < (n_norm / n_merged) < 5.0
