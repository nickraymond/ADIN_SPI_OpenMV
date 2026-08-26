"""Tests for the heat-map accumulation math (needs numpy; skipped where
absent so the pure-stdlib suites stay runnable anywhere)."""
import os
import sys

import pytest

np = pytest.importorskip("numpy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hil_harness import CamMap                                # noqa: E402
from hil_heatmap import GRID_H, GRID_W, accumulate, heat_lut  # noqa: E402


def _rec(tiles, cells):
    return {"tiles": tiles, "cells": cells}


def test_cell_center_lands_where_decode_np_puts_it():
    """Identity-ish mapping: camera px == still px/1000 (H = diag(1000)
    forward, so cam_to_frac divides by 1000). A cell at grid (y=1, x=2)
    of an hh=32 head (stride 8) in a tile at (192, 144) has center
    (192+(0.5+2)*8, 144+(0.5+1)*8) = (212, 156)."""
    M = CamMap(np.diag([1000.0, 1000.0, 1.0]))
    grid = np.zeros((GRID_H, GRID_W), np.float32)
    accumulate(_rec([[192, 144]],
                    [[[32, 1, 2, 0.5, 0.5, 0.1, 0.1, 0.9, 0.8]]]),
               M, grid, conf_floor=0.05)
    gy, gx = np.unravel_index(np.argmax(grid), grid.shape)
    assert (gx, gy) == (int(0.212 * GRID_W), int(0.156 * GRID_H))
    assert grid[gy, gx] == pytest.approx(0.72)      # obj*cls deposited


def test_conf_floor_drops_noise_cells():
    M = CamMap(np.diag([1000.0, 1000.0, 1.0]))
    grid = np.zeros((GRID_H, GRID_W), np.float32)
    accumulate(_rec([[0, 0]],
                    [[[32, 0, 0, 0.5, 0.5, 0.1, 0.1, 0.11, 0.2]]]),
               M, grid, conf_floor=0.05)             # 0.022 < floor
    assert grid.sum() == 0.0


def test_out_of_frame_cells_are_dropped_not_wrapped():
    M = CamMap(np.diag([1.0, 1.0, 1.0]))             # frac >> 1
    grid = np.zeros((GRID_H, GRID_W), np.float32)
    accumulate(_rec([[600, 380]],
                    [[[32, 30, 30, 0.5, 0.5, 0.1, 0.1, 0.9, 0.9]]]),
               M, grid, conf_floor=0.05)
    assert grid.sum() == 0.0


def test_lut_shape_and_endpoints():
    lut = heat_lut()
    assert lut.shape == (256, 3)
    assert tuple(lut[0]) == (0, 0, 0)
    assert lut[255].min() > 200                      # hot end is bright
