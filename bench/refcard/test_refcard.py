"""Host tests for the reference-card analyzer's math core (no OpenCV).

    ~/nereus_ml/venvs/fomo/bin/python -m pytest bench/refcard/test_refcard.py -q

Covers the parts that decide correctness: sRGB<->Lab, delta-E on known
pairs, the homography (recovers a known warp + round-trips), the CCM fit
(recovers a known linear transform, and reduces error on noisy data),
and glare-robust patch sampling. detect_tags (OpenCV) is exercised by
the live demo, not here.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refcard_analyze import (apply_ccm, apply_h, delta_e76, fit_ccm,  # noqa
                             linear_to_srgb, rgb_to_lab, sample_patch,
                             saturation, solve_homography, srgb_to_linear)


def test_srgb_linear_roundtrip():
    c = np.array([0, 12, 74, 128, 200, 255], np.float64)
    back = linear_to_srgb(srgb_to_linear(c))
    assert np.allclose(back, c, atol=0.5)


def test_lab_white_and_black():
    assert np.allclose(rgb_to_lab([255, 255, 255]), [100, 0, 0], atol=0.5)
    lab_k = rgb_to_lab([0, 0, 0])
    assert lab_k[0] < 0.5 and abs(lab_k[1]) < 1 and abs(lab_k[2]) < 1


def test_delta_e_zero_and_positive():
    assert delta_e76([120, 80, 40], [120, 80, 40]) < 1e-6
    # a clearly different color has a large dE
    assert delta_e76([240, 74, 24], [24, 74, 240]) > 40


def test_saturation_gray_vs_color():
    assert saturation([128, 128, 128]) < 1e-9
    assert abs(saturation([255, 0, 0]) - 1.0) < 1e-9


def test_homography_recovers_known_warp():
    # square -> quad
    src = [[0, 0], [100, 0], [0, 100], [100, 100]]
    dst = [[10, 5], [210, 15], [20, 205], [230, 215]]
    H = solve_homography(src, dst)
    got = apply_h(H, src)
    assert np.allclose(got, dst, atol=1e-6)


def test_homography_interior_point():
    src = [[0, 0], [10, 0], [0, 10], [10, 10]]
    dst = [[0, 0], [100, 0], [0, 100], [100, 100]]   # pure 10x scale
    H = solve_homography(src, dst)
    assert np.allclose(apply_h(H, [[5, 5]])[0], [50, 50], atol=1e-6)


def test_ccm_recovers_linear_transform():
    rng = np.random.default_rng(0)
    true = rng.integers(20, 235, size=(24, 3)).astype(np.float64)
    # simulate a camera: known linear mix + gain in LINEAR light
    Mtrue = np.array([[0.8, 0.1, 0.05], [0.05, 0.75, 0.1],
                      [0.02, 0.08, 0.7]])
    lin = srgb_to_linear(true) @ Mtrue.T
    meas = linear_to_srgb(lin)
    M = fit_ccm(meas, true)
    corr = apply_ccm(M, meas)
    # CCM should invert the mix to near-perfect
    assert np.mean(delta_e76(corr, true)) < 1.5


def test_ccm_reduces_error_on_noisy_data():
    rng = np.random.default_rng(1)
    true = rng.integers(30, 220, size=(20, 3)).astype(np.float64)
    lin = srgb_to_linear(true) @ np.array([[0.7, 0.2, 0.1],
                                           [0.1, 0.6, 0.2],
                                           [0.1, 0.15, 0.65]]).T
    meas = np.clip(linear_to_srgb(lin) + rng.normal(0, 3, true.shape),
                   0, 255)
    before = np.mean(delta_e76(meas, true))
    M = fit_ccm(meas, true)
    after = np.mean(delta_e76(apply_ccm(M, meas), true))
    assert after < before * 0.5           # at least halves the error


def test_sample_patch_median_is_glare_robust():
    img = np.full((20, 20, 3), 100, np.uint8)
    img[8:12, 8:12] = 255                  # a glare blob at center
    rgb, clip = sample_patch(img, (10, 10), half=8)
    # median ignores the minority-glare pixels
    assert np.allclose(rgb, 100, atol=1)
    assert clip > 0                        # but the clip fraction flags it


def test_spec_file_is_wellformed():
    spec = json.load(open(os.path.join(os.path.dirname(__file__),
                                       "refcard_v1.json")))
    assert set(spec["tags"]) == {"TL", "TR", "BL", "BR"}
    names = {p["name"] for p in spec["patches"]}
    for c in ("red", "green", "blue", "white", "gray50"):
        assert c in names
    for p in spec["patches"]:
        assert len(p["rgb"]) == 3 and 0 < p["cx"] < spec["w"]
