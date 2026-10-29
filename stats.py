"""Counts how many monitor button presses Lumi has saved you.

Estimates are based on the Acer menu: open the menu, find the section, enter it,
then press up/down to change the value.
"""
import json
from pathlib import Path

_FILE = Path(__file__).with_name("stats.json")
STEP_PER_PRESS = {"black_boost": 1}  # how much one button press changes a value (default 5)


def load():
    try:
        return int(json.loads(_FILE.read_text())["buttons_saved"])
    except (OSError, ValueError, KeyError):
        return 0


def save(total):
    try:
        _FILE.write_text(json.dumps({"buttons_saved": total}))
    except OSError:
        pass


def presses_for(setting, old, new):
    """Rough number of monitor button presses one change would have taken."""
    if setting == "focus":
        return 1
    if setting in ("display_mode", "laptop_brightness"):
        return 2  # Windows shortcuts rather than monitor buttons
    if setting == "mode":
        return 5
    try:
        delta = abs(int(float(new)) - int(float(old)))
    except (TypeError, ValueError):
        return 4  # picking an option from a list
    return 3 + max(1, round(delta / STEP_PER_PRESS.get(setting, 5)))
