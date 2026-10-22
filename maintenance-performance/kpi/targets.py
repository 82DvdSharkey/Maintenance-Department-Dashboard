"""Target status evaluation."""
from __future__ import annotations

import pandas as pd

ON_TARGET = "On Target"
WATCH = "Watch"
NO_DATA = "No Data"
NO_TARGET = "No Target"


def evaluate_status(value, target, direction: str | None, watch_band: float | None = 0.0) -> str:
    if value is None or pd.isna(value):
        return NO_DATA
    if target is None or pd.isna(target) or direction not in ("higher", "lower"):
        return NO_TARGET
    band = 0.0 if watch_band is None or pd.isna(watch_band) else float(watch_band)
    if direction == "higher":
        if value >= target:
            return ON_TARGET
        return WATCH if value >= target - band else "Below Target"
    if value <= target:
        return ON_TARGET
    return WATCH if value <= target + band else "Above Target"
