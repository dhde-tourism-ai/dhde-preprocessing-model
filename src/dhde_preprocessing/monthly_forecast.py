"""
Monthly forecast, 12 months ahead: visitors per Fukui node's municipality
(JTTA digital tourism statistics, see sources/monthly_visitors.py) and
Fukui guest-nights (JTA accommodation survey, sources/guest_nights.py),
with Ishikawa and Toyama as candidate leading signals.

Every series is forecast by whichever candidate model has the lowest
backtest error on it, re-chosen on every run:

    seasonal_naive     the same month last year (the baseline)
    own_growth         + half the series' own year-on-year growth, last 6 months
    neighbour_growth   + half Ishikawa's and Toyama's year-on-year growth, last 6 months

A candidate replaces the baseline only if it beats it by more than
MIN_GAIN_PP percentage points on at least MIN_SHARED_CELLS forecasts that
every model could make: with this little history, smaller gaps are
noise. For Fukui's visitor series that bar can't be met yet (growth needs
a year of comparable data, which starts 2025-01), so they stay on the
baseline until enough months accumulate; each run re-tests. The low/high range is the 10th-90th percentile of the chosen
model's backtest errors, so it is as wide as the model has actually been
wrong, not a theoretical interval.

Backtests are rolling-origin: stand at each past month, forecast 1-12
months ahead using only data up to that month, and score against what
happened. Only months on or after a series' `comparable_from` are used,
because both sources change method inside their history: Fukui's visitor
counts are comparable only from 2025-01 (monthly_visitors.REVISED_FROM),
and guest-nights start at 2023-01 to leave out COVID (JTA's 2026-01
sampling change can't be avoided and is noted in the output).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .sources.guest_nights import load_guest_nights
from .sources.monthly_visitors import REVISED_FROM, load_official_table

HORIZON = 12
GROWTH_MONTHS = 6          # year-on-year growth is averaged over this many recent months
GROWTH_DAMPING = 0.5       # and only half of it is carried forward
MIN_GROWTH_PAIRS = 3       # growth models need at least this many year-on-year pairs
MIN_GAIN_PP = 0.5          # a candidate must beat the baseline MAPE by this much
MIN_SHARED_CELLS = 24      # ...on at least this many (origin, h) cells every model scored
MIN_BACKTEST = 12          # fewer backtest forecasts than this: baseline, no range
INTERVAL = (0.10, 0.90)
GUEST_NIGHTS_FROM = "2023-01"

BASELINE = "seasonal_naive"
MODELS = (BASELINE, "own_growth", "neighbour_growth")


@dataclass(frozen=True)
class SeriesSpec:
    name: str
    kind: str                  # "visitors" or "guest_nights"
    label: str
    codes: tuple[int, ...]     # lgcodes summed (visitors) or one pref code (guest_nights)
    column: str = ""           # guest_nights column
    comparable_from: str = ""  # YYYY-MM; earlier months are ignored


FUKUI_FROM = f"{REVISED_FROM[18] // 100}-{REVISED_FROM[18] % 100:02d}"

# The municipality each Fukui node sits in. Rainbow Line runs through both
# Mihama and Wakasa, so they are summed (as in the ingest's trend areas).
SERIES = [
    SeriesSpec("tojinbo", "visitors", "Sakai city (Tojinbo)", (18210,), comparable_from=FUKUI_FROM),
    SeriesSpec("fukui_station", "visitors", "Fukui city (Fukui Station)", (18201,), comparable_from=FUKUI_FROM),
    SeriesSpec("katsuyama", "visitors", "Katsuyama city", (18206,), comparable_from=FUKUI_FROM),
    SeriesSpec("rainbow_line", "visitors", "Mihama + Wakasa (Rainbow Line)", (18442, 18501), comparable_from=FUKUI_FROM),
    SeriesSpec("eiheiji", "visitors", "Eiheiji town", (18322,), comparable_from=FUKUI_FROM),
    SeriesSpec("awara_onsen", "visitors", "Awara city", (18208,), comparable_from=FUKUI_FROM),
    SeriesSpec("fukui_pref", "visitors", "Fukui prefecture", (18,), comparable_from=FUKUI_FROM),
    SeriesSpec("fukui_guest_nights", "guest_nights", "Fukui guest-nights", (18,), "guest_nights", GUEST_NIGHTS_FROM),
    SeriesSpec("fukui_guest_nights_japanese", "guest_nights", "Fukui guest-nights, Japanese", (18,),
               "guest_nights_japanese", GUEST_NIGHTS_FROM),
    SeriesSpec("fukui_guest_nights_foreign", "guest_nights", "Fukui guest-nights, foreign", (18,),
               "guest_nights_foreign", GUEST_NIGHTS_FROM),
]
NEIGHBOUR_PREFS = (17, 16)  # Ishikawa, Toyama


# ── Models ────────────────────────────────────────────────────────────────────
# Each takes log series indexed by monthly Period and returns the log
# forecast for T+h, or None when it can't be computed from data up to T.

def _yoy(y: pd.Series, T: pd.Period) -> float | None:
    pairs = [y[t] - y[t - 12] for t in (T - i for i in range(GROWTH_MONTHS))
             if t in y.index and t - 12 in y.index]
    return float(np.mean(pairs)) if len(pairs) >= MIN_GROWTH_PAIRS else None


def predict(model: str, y: pd.Series, neighbours: list[pd.Series], T: pd.Period, h: int) -> float | None:
    base_month = T + h - 12
    if base_month not in y.index:
        return None
    base = y[base_month]
    if model == BASELINE:
        return base
    if model == "own_growth":
        g = _yoy(y, T)
    elif model == "neighbour_growth":
        gs = [g for g in (_yoy(n, T) for n in neighbours) if g is not None]
        g = float(np.mean(gs)) if gs else None
    else:
        raise ValueError(f"unknown model {model!r}")
    return None if g is None else base + GROWTH_DAMPING * g


# ── Backtest and forecast ─────────────────────────────────────────────────────

def backtest(y: pd.Series, neighbours: list[pd.Series]) -> pd.DataFrame:
    """Rolling-origin errors: one row per (model, origin, horizon) that could
    be scored. `log_error` is log(actual) - log(predicted)."""
    rows = []
    last = y.index.max()
    for T in y.index:
        for h in range(1, HORIZON + 1):
            if T + h > last or T + h not in y.index:
                continue
            # Neighbours only up to T, like the series itself.
            nb = [n[n.index <= T] for n in neighbours]
            for m in MODELS:
                p = predict(m, y[y.index <= T], nb, T, h)
                if p is not None:
                    rows.append((m, str(T), h, y[T + h] - p))
    bt = pd.DataFrame(rows, columns=["model", "origin", "h", "log_error"])
    bt["ape_pct"] = (np.exp(-bt["log_error"]) - 1).abs() * 100 if not bt.empty else []
    return bt


def choose_model(bt: pd.DataFrame) -> tuple[str, dict[str, float]]:
    """(chosen model, MAPE % per model on the cells every model could score).

    Growth models need a few year-on-year pairs, so they can't forecast from
    the earliest origins. Comparing every model on the same (origin, h)
    cells keeps the baseline's easy or hard early cells from deciding it.
    """
    if bt.empty:
        return BASELINE, {}
    per_cell = bt.groupby(["origin", "h"])["model"].nunique()
    shared = per_cell[per_cell == bt["model"].nunique()].index
    common = bt.set_index(["origin", "h"]).loc[shared]
    if len(shared) < MIN_SHARED_CELLS:
        # Too few shared cells to compare fairly: baseline, scored on everything.
        return BASELINE, {BASELINE: bt.loc[bt["model"] == BASELINE, "ape_pct"].mean()}
    mape = common.groupby("model")["ape_pct"].mean().to_dict()
    best = min(mape, key=mape.get)
    if best != BASELINE and mape[best] > mape[BASELINE] - MIN_GAIN_PP:
        best = BASELINE
    return best, mape


def forecast_series(y_raw: pd.Series, neighbours_raw: list[pd.Series], comparable_from: str = "") -> dict:
    """Forecast the next HORIZON months of one series.

    Returns {"forecast": DataFrame(month, predicted, low, high),
             "model", "mape" (per model), "backtest_n", "data_through"}.
    """
    start = pd.Period(comparable_from, "M") if comparable_from else None

    def prep(s: pd.Series) -> pd.Series:
        s = s[s > 0].sort_index()
        if start is not None:
            s = s[s.index >= start]
        return np.log(s)

    y = prep(y_raw)
    neighbours = [prep(n) for n in neighbours_raw]
    bt = backtest(y, neighbours)
    model, mape = choose_model(bt)
    errors = bt.loc[bt["model"] == model, "log_error"]
    n = int(len(errors))
    lo_q, hi_q = (errors.quantile(INTERVAL[0]), errors.quantile(INTERVAL[1])) if n >= MIN_BACKTEST else (np.nan, np.nan)
    if n < MIN_BACKTEST:
        model = BASELINE

    T = y.index.max()
    rows = []
    for h in range(1, HORIZON + 1):
        p = predict(model, y, neighbours, T, h)
        if p is None:  # growth unavailable at the latest origin: fall back to the baseline
            p = predict(BASELINE, y, neighbours, T, h)
        if p is None:
            continue
        # A model that has mostly erred one way gives a range that can sit
        # entirely above or below its own forecast; widen it to include it.
        rows.append((str(T + h), np.exp(p), np.exp(p + min(lo_q, 0)), np.exp(p + max(hi_q, 0))))
    fc = pd.DataFrame(rows, columns=["month", "predicted", "low", "high"])
    return {"forecast": fc, "model": model, "mape": mape, "backtest_n": n, "data_through": str(T)}


# ── Assembling the series ─────────────────────────────────────────────────────

def _to_period_index(month: pd.Series) -> pd.PeriodIndex:
    m = month.astype(int)
    return pd.PeriodIndex([pd.Period(year=v // 100, month=v % 100, freq="M") for v in m])


def visitors_series(table: pd.DataFrame, codes: tuple[int, ...]) -> pd.Series:
    """Sum of the lgcodes' monthly visitors; only months where all are present."""
    rows = table[table["lgcode"].isin(codes)]
    wide = rows.pivot_table(index="month", columns="lgcode", values="n").dropna()
    if set(wide.columns) != set(codes):
        return pd.Series(dtype=float)
    s = wide.sum(axis=1)
    s.index = _to_period_index(s.index.to_series())
    return s


def guest_nights_series(table: pd.DataFrame, pref: int, column: str) -> pd.Series:
    rows = table[table["pref_code"] == pref].dropna(subset=[column])
    s = pd.Series(rows[column].to_numpy(), index=_to_period_index(rows["month"]))
    return s


def build_monthly_forecast(series: list[SeriesSpec] = SERIES) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """(forecast rows, backtest MAPE per series and model, source notes)."""
    visitors, v_notes = load_official_table()
    nights, n_note = load_guest_nights()
    notes = [*v_notes, f"guest-nights: {n_note}",
             "guest-nights: JTA changed its sampling from 2026-01 (stratified by rooms, "
             "not employees); year-on-year changes across that boundary may partly reflect it"]

    out, scores = [], []
    for spec in series:
        if spec.kind == "visitors":
            y = visitors_series(visitors, spec.codes)
            nb = [visitors_series(visitors, (p,)) for p in NEIGHBOUR_PREFS]
        else:
            y = guest_nights_series(nights, spec.codes[0], spec.column)
            nb = [guest_nights_series(nights, p, spec.column) for p in NEIGHBOUR_PREFS]
        if y.empty:
            notes.append(f"{spec.name}: no data for {spec.codes}, skipped")
            continue
        res = forecast_series(y, nb, spec.comparable_from)
        mape = res["mape"]
        fc = res["forecast"].assign(
            series=spec.name, kind=spec.kind, label=spec.label, model=res["model"],
            backtest_mape_pct=round(mape.get(res["model"], np.nan), 1),
            baseline_mape_pct=round(mape.get(BASELINE, np.nan), 1),
            backtest_n=res["backtest_n"], data_through=res["data_through"],
            comparable_from=spec.comparable_from,
        )
        out.append(fc)
        scores.append({"series": spec.name, **{m: round(v, 1) for m, v in mape.items()},
                       "chosen": res["model"], "backtest_n": res["backtest_n"]})

    cols = ["series", "kind", "label", "month", "predicted", "low", "high", "model",
            "backtest_mape_pct", "baseline_mape_pct", "backtest_n", "data_through", "comparable_from"]
    forecast = pd.concat(out, ignore_index=True)[cols] if out else pd.DataFrame(columns=cols)
    for c in ("predicted", "low", "high"):
        forecast[c] = forecast[c].round(0)
    return forecast, pd.DataFrame(scores), notes
