"""Host tests for the S28 stacking-compare workbench card + wrapper.

    ~/nereus_ml/venvs/fomo/bin/python -m pytest pi/s28/test_s28_card.py -q

Pins the recipe against the workbench schema and the SPEC board identity,
and covers the wrapper's non-board logic (placeholder + error page). The
capture/compare children are exercised by the live card, not here.
"""
import os
import sys
import tomllib

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "pi", "workbench"))

RECIPE = os.path.join(_ROOT, "pi", "workbench", "recipes",
                      "s28_stack_compare.toml")
# SPEC §Board identity on nereus000 (the names are backwards from the
# guess: the "Pyboard" IS the N6).
AE3_BY_ID = "usb-OpenMV_OpenMV_Camera_0829c14000000000-if00"
N6_BY_ID = ("usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_"
            "020023000450433547373200-if00")


def test_recipe_validates_clean():
    import workbench as wb
    obj = tomllib.load(open(RECIPE, "rb"))
    recipe, errs = wb.validate_recipe(obj, "s28_stack_compare.toml")
    assert errs == [], errs
    assert recipe["name"] == "s28-stack-compare"
    assert recipe["opens"] == ":8093"
    assert recipe["health"]["http"].endswith(":8093/")


def test_recipe_boards_are_ae3_and_n6():
    obj = tomllib.load(open(RECIPE, "rb"))
    byid = {b["label"]: b["by_id"] for b in obj["boards"]}
    assert byid == {"AE3": AE3_BY_ID, "N6": N6_BY_ID}   # pinned to SPEC


def test_recipe_frames_param_is_enum():
    obj = tomllib.load(open(RECIPE, "rb"))
    assert obj["params"]["frames"] == ["8", "16", "32"]


def test_wrapper_pages_are_wellformed():
    import s28_compare_run as w
    assert "refresh" in w.PLACEHOLDER and "Capturing" in w.PLACEHOLDER
    ep = w.error_page("burst capture failed (rc=1)")
    assert "capture failed" in ep and "reboot" in ep
    assert "rc=1" in ep


def test_wrapper_compiles_and_defaults_ae3():
    src = open(os.path.join(_HERE, "s28_compare_run.py")).read()
    compile(src, "s28_compare_run.py", "exec")
    assert AE3_BY_ID in src                    # default --port is the AE3
