# gta6-twitch-ccu-forecast-01 — slot 1 (model v2: OLS log-ratio regression)

## What the program does

`main.py` reads one case (a launch day cut at the end of hour 1) and forecasts
Twitch concurrent viewers in the game's category for hours 2–24, as five
quantiles per hour (levels 0.05 / 0.25 / 0.5 / 0.75 / 0.95). The score is
pinball loss on the log scale, so a good forecast needs both an accurate
median and an honest spread.

## The model

For each forecast hour h, the program regresses the log-ratio of viewers
against the launch's size, using the case's own `comparables` as the training
set:

- Every past big launch contributes one point per hour: `x = log10(viewers at
  hour 1)` and `y = ln(viewers at hour h / viewers at hour 1)`.
- A least-squares line `y = a + b·x` is fitted per hour. Big launches tend to
  stay flat after hour 1 (hour 1 already captured the world) while smaller
  ones keep climbing, so the slope captures that size-to-shape gradient
  continuously instead of forcing one fixed shape on every launch.
- The predictor is winsorised: this case's `log10(p1)` is clamped to the
  range of x the points actually cover, so the line is never extrapolated
  outside the data.
- The median log-ratio is the fitted line at the (clamped) predictor; the
  spread is the population standard deviation of the residuals around the
  line, floored at 0.05 log units.
- Quantiles: `q_tau = p1 · exp(median + z_tau · spread)`, with
  z = (−1.645, −0.674, 0, 0.674, 1.645). Values are rounded to 2 decimals
  half-up, forced non-decreasing within each row, and clamped to
  (0, 1,000,000,000].

## The shape library

Only genuine past release days count, with three filters:

1. Skip any comparable whose name contains "Grand Theft Auto VI" — that is
   the pre-release category curve (premiere 2026-08-27), not a release day.
2. Skip any comparable named the same as the case's own game.
3. Keep only launches whose hour-1 print is at least 20,000 viewers and
   which have prints for at least 12 of the 23 following hours.

On the practice cases this keeps 27 of 38 comparables. Hours are counted
from each comparable's own `day_start` (offset 0 = hour 1).

## Fallbacks

- If an hour has fewer than 8 comparable points, that hour falls back to the
  house baseline formula.
- If the whole library has fewer than 5 qualifying comparables, the entire
  forecast falls back to the house baseline:
  `q_tau = p1 · exp(z_tau · 0.15 · sqrt(h − 1))`.
- A case asking for levels other than [0.05, 0.25, 0.5, 0.75, 0.95], with no
  hour-1 print, or with forecast hours other than 2–24 each once, raises an
  error instead of writing a fake forecast.

## Why v2 replaced v1

The v1 entry used a median-shape model. After it was built, the live dev
leaderboard showed that model family ranking #31–33 (loss 0.32–0.35),
outside the prize zone (the top-20 cutoff is currently 0.240), while the
log-ratio regression family ranks #1–8 (loss 0.169–0.215). v1 was removed
from the repo; this v2 is that regression family, written from scratch.

## Measured results (parent's measurements, supplied with the task)

- Leave-one-out over big launches (n=27): mean pinball loss **0.2524** vs
  house baseline **0.3214**, winning 20 of 27 cases; worst per-case deficit
  vs baseline **+0.045**.
- Leave-one-out over practice-like launches (console-only + rolling
  midnight, n=4): mean **0.175–0.18** vs baseline **0.3006**, winning 4/4.
- Projected practice loss ≈ **0.18–0.22** (current top-20 cutoff on the dev
  board 0.240; #1 on the board 0.169).
- The house AI model's loss is not published, so it cannot be verified
  offline; the prize gate requires beating both the house baseline and the
  house AI model.

## Local verification (this build, 17 Nov 2026)

Driver regression test on all four practice cases (p00–p03), one case per
run, Python 3.12.3 locally (grader uses 3.13, stdlib only):

- All outputs valid: JSON, exactly 23 rows (hours 2–24), 5 quantiles each,
  non-decreasing, all in (0, 1e9], size 1,452–1,541 bytes (limit 65,536).
- Determinism: two runs per case byte-identical.
- Runtime: 0.10–0.16 s per case (limit 8 s).
- Regression active on every hour of every case (27 qualifying comparables,
  27 points per hour, no hour fell back to the house baseline).
- Median sanity (q50 / p1): p00 0.86–3.19, p01 0.75–1.72, p02 0.64–1.25,
  p03 0.75–1.71 across hours 2–24 — no median explodes past 10× p1 or
  collapses below 0.1× p1. Small launches climb through the day; the
  biggest launch (p02, 269,517 at hour 1) stays near flat, as the model
  intends.

### Reading the practice medians

The clearest pattern is the size gradient working as designed. p00
(Marvel's Wolverine, p1 = 23,357 — the smallest hour-1 print in the
practice set, near the bottom of the library's hour-1 range) climbs to
3.19× p1 by hour 19 before easing to 2.75× at hour 24: the regression
reads a small hour 1 as a launch still gathering viewers. p02 (God of War
Ragnarök, p1 = 269,517 — the largest) dips to 0.64× at hour 10 and ends at
1.05×: a big hour 1 is read as a launch already at its peak, decaying
through the morning and recovering into the evening. p01 and p03 sit
between the two. The evening peak (hours 15–19) and the late dip appear
in all four cases, which is the diurnal shape the library carries.

Two things to watch on the final case: p00's climb is the model's most
aggressive extrapolation, and it is exactly where winsorisation binds —
p00's log10(p1) sits near the low end of the fitted range, so the line is
not being extended past the data, but the slope there is doing the most
work. And the final case's hour-1 print will likely be far larger than
any practice print (GTA VI), so the predictor will sit at the top edge of
the library range; the winsorised median there is the library's biggest
launches' behaviour, which is the flattest shape the data supports.

## Determinism and limits

No randomness, no clock, no hash-order-dependent iteration — all iteration
over data structures is explicitly sorted. Standard library only, no
network. Runtime is far under the 8-second per-case limit.
