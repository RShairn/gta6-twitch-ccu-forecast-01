"""Forecast GTA VI launch-day Twitch CCU, hours 2..24, as five quantiles an hour.

Model (v2): a per-hour log-ratio regression over the case's own comparables.

For each forecast hour h, every past big launch in the case's `comparables`
contributes one point: x is log10 of that launch's hour-1 viewers and y is the
natural log of its hour-h viewers divided by its hour-1 viewers. A least-squares
line through those points, evaluated at this case's own hour-1 level (clamped to
the range the points actually cover), gives the median log-ratio; the spread of
the residuals around that line gives the log spread. The five quantiles are
then p1 * exp(median + z_tau * spread), with z the standard normal quantiles.

Big launches tend to stay flat after hour 1 while smaller ones keep climbing,
so the slope of that line captures the size-to-shape gradient continuously
instead of assuming one fixed shape for every launch. Hours with too few
comparable points, and cases whose library is too thin overall, fall back to
the house persistence forecast (flat median, spread widening like sqrt(h-1)).

Standard library only, no network, no randomness, no clock: the same case
always produces the same bytes.
"""

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# The quantile levels the contest scores, in the order each row lists them.
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
# The standard normal's quantile at each level.
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# How many comparable points an hour needs before the regression is trusted.
MIN_POINTS_PER_HOUR = 8
# How many qualifying comparables a case needs before any regression is used.
MIN_COMPARABLES = 5
# A comparable only counts as a "big launch" if its hour-1 print is this large.
MIN_HOUR1_VIEWERS = 20_000
# ...and it must have prints for at least this many of the 23 following hours.
MIN_FOLLOWING_HOURS = 12
# The residual spread is never allowed below this (log units).
MIN_SPREAD = 0.05
# The house baseline's per-hour log spread (used by the fallback).
HOUSE_SIGMA = 0.15
# Output bounds the rules enforce on every quantile.
MAX_VIEWERS = 1_000_000_000


def parse_instant(text: str) -> datetime:
    """Parse an ISO 8601 instant; the trailing Z means UTC."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def round2(x: float):
    """x rounded to 2 decimals, half up, as JavaScript's Math.round(x*100)/100.

    A whole number comes back as an int, so json.dumps writes 1240 where
    JavaScript writes 1240 (not 1240.0).
    """
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value.is_integer() else value


def house_quantiles(p1: float, h: int) -> list:
    """The house persistence forecast for hour h: flat median, widening spread."""
    spread = HOUSE_SIGMA * math.sqrt(h - 1)
    return [round2(p1 * math.exp(z * spread)) for z in Z]


def tidy(row: list) -> list:
    """Force a row non-decreasing and inside (0, 1e9], after rounding."""
    out = []
    prev = 0.0
    for value in row:
        value = min(max(value, 0.01), MAX_VIEWERS)
        if value < prev:
            value = prev
        out.append(value)
        prev = value
    return out


def hourly_viewers(comp: dict) -> dict:
    """Map hour-offset from the comparable's day_start to its viewers print.

    Offset 0 is the release day's first hour, offset h-1 is hour h. Later rows
    at the same offset cannot happen in these files; the first one wins.
    """
    start = parse_instant(comp["day_start"])
    by_offset = {}
    for row in comp.get("hourly") or []:
        try:
            when = parse_instant(row["from"])
            viewers = row["viewers"]
        except (KeyError, TypeError, ValueError):
            continue
        offset = int((when - start).total_seconds() // 3600)
        if 0 <= offset <= 23 and offset not in by_offset:
            if isinstance(viewers, (int, float)) and math.isfinite(viewers) and viewers > 0:
                by_offset[offset] = viewers
    return by_offset


def qualifying_library(case: dict, game_name: str) -> list:
    """The comparables usable as past big launches, each as {hour1, by_hour}.

    Skips the pre-release GTA VI category curve (not a release day), any
    comparable named the same as the case's own game, and any launch whose
    hour-1 print is too small or whose following hours are too sparse.
    """
    library = []
    for comp in case.get("comparables") or []:
        name = comp.get("name") or ""
        if "Grand Theft Auto VI" in name:
            continue
        if name == game_name:
            continue
        by_hour = hourly_viewers(comp)
        hour1 = by_hour.get(0)
        if hour1 is None or hour1 < MIN_HOUR1_VIEWERS:
            continue
        following = sum(1 for h in range(1, 24) if h in by_hour)
        if following < MIN_FOLLOWING_HOURS:
            continue
        library.append({"hour1": hour1, "by_hour": by_hour})
    library.sort(key=lambda entry: entry["hour1"])
    return library


def fit_line(points: list) -> tuple:
    """Least-squares line y = a + b*x through (x, y) points; returns (a, b)."""
    n = len(points)
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    denom = sum((p[0] - mean_x) ** 2 for p in points)
    if denom == 0:
        return mean_y, 0.0
    slope = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points) / denom
    return mean_y - slope * mean_x, slope


def residual_spread(points: list, intercept: float, slope: float) -> float:
    """Population standard deviation of the residuals around the fitted line."""
    n = len(points)
    total = sum((p[1] - (intercept + slope * p[0])) ** 2 for p in points)
    return math.sqrt(total / n)


def regression_quantiles(library: list, p1: float, h: int):
    """The five quantiles for hour h from the log-ratio regression.

    Returns None when the hour has too few comparable points, so the caller
    can fall back to the house baseline for that hour.
    """
    points = []
    for entry in library:
        later = entry["by_hour"].get(h - 1)
        if later is None:
            continue
        points.append((math.log10(entry["hour1"]), math.log(later / entry["hour1"])))
    if len(points) < MIN_POINTS_PER_HOUR:
        return None
    intercept, slope = fit_line(points)
    xs = [p[0] for p in points]
    # Winsorise: never extrapolate the line past the data's own range.
    lp = min(max(math.log10(p1), min(xs)), max(xs))
    median_log_ratio = intercept + slope * lp
    spread = max(residual_spread(points, intercept, slope), MIN_SPREAD)
    return [round2(p1 * math.exp(median_log_ratio + z * spread)) for z in Z]


def forecast_case(case: dict, name: str) -> dict:
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    first = next((k for k in case["known"] if k["hour"] == 1), None)
    if first is None:
        raise ValueError(f"{name} has no hour-1 print")
    p1 = max(1, first["viewers"])

    game = case.get("game") or {}
    game_name = game.get("name") if isinstance(game, dict) else str(game)
    library = qualifying_library(case, game_name)

    rows = []
    hours = [row["hour"] for row in case["forecast"]]
    if sorted(hours) != list(range(2, 25)):
        raise ValueError(f"{name} asks for hours {hours}, not 2..24 each once")
    for h in sorted(hours):
        if len(library) >= MIN_COMPARABLES:
            quantiles = regression_quantiles(library, p1, h)
        else:
            quantiles = None
        if quantiles is None:
            quantiles = house_quantiles(p1, h)
        rows.append({"hour": h, "q": tidy(quantiles)})
    rows.sort(key=lambda r: r["hour"])
    return {"hours": rows}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file, in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
