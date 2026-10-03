"""
Pickup model (prototype): a night's final hotel occupancy from the rooms
already booked for it.

The FTAS reservation feeds (sources/hotel.py) take a snapshot every day, so
each night is seen about 90 times as its bookings come in. Standing L days
before a night, with OTB ("on the books") rooms booked so far:

    final = OTB at lead L + expected pickup after lead L

and the expected pickup comes from similar nights that have already ended:

    recent      mean pickup after lead L of the last RECENT_NIGHTS nights of
                the same type, ended within RECENT_DAYS of the forecast date
    last_year   mean pickup after lead L of the same type of night in the
                same weeks last year (364 days back, so weekdays line up)
    blend       the mean of the two (either alone when the other is missing)

At lead 60 the recent nights are two to four months earlier, in another
season, which is why last year's pickup is tried too (review by Dina).

Night types (a hotel night is busy when the next day is off):
    before_off  Saturdays and the night before a holiday
    Mon..Fri, Sun  the other nights; a holiday night followed by a working day
                counts as a Sunday
    peak        Golden Week, Obon and New Year (as in forecast.calendar). Kept
                out of the other types' averages, and forecast from last year's
                peak nights, falling back to recent peak nights.

Everything is in occupancy (rooms / capacity), so a feed whose hotel count
changes shows up as a capacity change, not as demand. Pickup keeps its sign
(cancellations make it negative) and the forecast is capped at 0-100%.

The two baselines a pickup method has to beat:
    otb            bookings so far are the final number
    last_year_final  the same night last year (364 days back)

backtest() stands at each past night's lead L, forecasts it with only the
nights that had ended by then, and scores it against the final. The low/high
range is the 10th-90th percentile of a method's errors before the held-out
period, the ~80% range the app shows for its other forecasts (5th-95th at 60
days, see RANGE_PCT_LONG). score_by_season() repeats the scoring on each
season of the last year in turn.
"""
from __future__ import annotations

import jpholiday
import numpy as np
import pandas as pd

from .config import resolve_path
from .sources import hotel

LEADS = (7, 14, 30, 60)
RECENT_NIGHTS = 8
RECENT_DAYS = 56
LAST_YEAR_DAYS = 364
LAST_YEAR_WINDOW = 14  # days either side of the night 364 days back
METHODS = ("recent", "last_year", "blend")
BASELINES = ("otb", "last_year_final")
RANGE_PCT = (10, 90)
# At 60 days the season moves between the training errors and the night, so
# the 10-90 range covered only 63-70% of held-out nights on Echizen Coast and
# Awara (review by Dina): use 5-95 there, and flag any range covering < 70%.
RANGE_PCT_LONG = (5, 95)
LONG_LEAD = 60
RANGE_MIN_COVERAGE = 0.70
SEASON_STARTS = (3, 6, 9, 12)  # spring, summer, autumn, winter
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def is_peak(dates: pd.DatetimeIndex) -> np.ndarray:
    """Golden Week, Obon and New Year, as forecast.calendar flags them."""
    md = dates.month * 100 + dates.day
    return np.asarray(((md >= 429) & (md <= 506)) | ((md >= 813) & (md <= 816)) | (md >= 1229) | (md <= 103))


def night_type(dates: pd.DatetimeIndex) -> pd.Series:
    """Each night's type (see the module docstring)."""
    holiday = np.array([jpholiday.is_holiday(d) for d in dates])
    next_holiday = np.array([jpholiday.is_holiday(d + pd.Timedelta(days=1)) for d in dates])
    out = []
    for d, hol, nxt, peak in zip(dates, holiday, next_holiday, is_peak(dates)):
        if peak:
            out.append("peak")
        elif d.dayofweek == 5 or nxt:
            out.append("before_off")
        elif hol:
            out.append("Sun")
        else:
            out.append(WEEKDAYS[d.dayofweek])
    return pd.Series(out, index=dates)


def load_clean(repo: str) -> pd.DataFrame:
    """Every snapshot row of a reservation repo, cleaned the way load_hotel
    cleans it (one row per night x lead)."""
    root = resolve_path(repo)
    raw = hotel._load_all_snapshots(f"{root}/data")
    adr_min, adr_max = hotel._derive_adr_bounds(raw)
    params = dict(hotel.DEFAULT_PARAMS, ADR_MIN=adr_min, ADR_MAX=adr_max)
    clean, _, _ = hotel._run_pipeline(raw, hotel._load_capacity(root), params)
    return clean


def curves(clean: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Occupancy by night x lead from hotel._run_pipeline's output, and each
    night's final occupancy (its own day's snapshot). Stale snapshots are
    dropped at every lead: a frozen feed repeats an earlier day, which is
    neither the final nor that lead's bookings (review by Dina)."""
    clean = clean[~clean["is_stale"].astype(bool)]
    occ = clean.pivot_table(index="date_visit", columns="lead_time", values="occ", aggfunc="first")
    occ.index = pd.DatetimeIndex(occ.index)
    day0 = clean[clean["lead_time"] == 0]
    final = pd.Series(day0["occ"].to_numpy(), index=pd.DatetimeIndex(day0["date_visit"])).sort_index()
    return occ, final


def _mean_pickup(nights: pd.DatetimeIndex, occ: pd.DataFrame, final: pd.Series, lead: int) -> float:
    if not len(nights):
        return np.nan
    p = final.reindex(nights) - occ[lead].reindex(nights)
    return float(p.mean()) if p.notna().any() else np.nan


def forecast_night(night: pd.Timestamp, lead: int, occ: pd.DataFrame, final: pd.Series,
                   types: pd.Series) -> dict[str, float]:
    """Every method's and baseline's forecast for one night, made `lead` days
    before it with only the nights that had ended by then."""
    origin = night - pd.Timedelta(days=lead)
    otb = occ.at[night, lead] if lead in occ.columns and night in occ.index else np.nan
    kind = types.get(night)
    done = final.index[final.index <= origin]
    done_types = types.reindex(done)

    recent = done[(done_types == kind).to_numpy() & (done > origin - pd.Timedelta(days=RECENT_DAYS))][-RECENT_NIGHTS:]
    ly_mid = night - pd.Timedelta(days=LAST_YEAR_DAYS)
    win = pd.Timedelta(days=LAST_YEAR_WINDOW)
    last_year = done[(done_types == kind).to_numpy() & (done >= ly_mid - win) & (done <= ly_mid + win)]

    p_recent = _mean_pickup(recent, occ, final, lead)
    p_ly = _mean_pickup(last_year, occ, final, lead)
    if kind == "peak":
        # Peak nights: last year's peak first; recent peaks (often a different
        # holiday) only when there is no last year to go on.
        p_recent = p_ly if not np.isnan(p_ly) else p_recent
    p_blend = np.nanmean([p_recent, p_ly]) if not (np.isnan(p_recent) and np.isnan(p_ly)) else np.nan

    clip = lambda x: float(np.clip(otb + x, 0.0, 1.0)) if not np.isnan(x) else np.nan  # noqa: E731
    return {
        "otb": otb,
        "last_year_final": final.get(ly_mid, np.nan),
        "recent": clip(p_recent),
        "last_year": clip(p_ly),
        "blend": clip(p_blend),
    }


def backtest(occ: pd.DataFrame, final: pd.Series, leads=LEADS) -> pd.DataFrame:
    """One row per night x lead: the type, OTB, final and every forecast."""
    types = night_type(pd.DatetimeIndex(sorted(set(occ.index) | set(final.index))))
    rows = []
    for lead in leads:
        if lead not in occ.columns:
            continue
        for night in final.index:
            if np.isnan(occ[lead].get(night, np.nan)):
                continue
            f = forecast_night(night, lead, occ, final, types)
            rows.append({"night": night, "lead": lead, "type": types[night], "final": final[night], **f})
    return pd.DataFrame(rows)


def score(bt: pd.DataFrame, test_from: pd.Timestamp, test_to: pd.Timestamp | None = None) -> pd.DataFrame:
    """Error per lead and method on the held-out nights (from `test_from`, and
    before `test_to` if given), in occupancy points, on the nights every method
    and baseline could forecast. The range comes from the errors before
    `test_from`."""
    cols = list(METHODS) + list(BASELINES)
    out = []
    for lead, g in bt.groupby("lead"):
        g = g.dropna(subset=cols)
        train = g[g["night"] < test_from]
        test = g[(g["night"] >= test_from) & ((g["night"] < test_to) if test_to is not None else True)]
        if test.empty:
            continue
        mae = {m: float((test[m] - test["final"]).abs().mean() * 100) for m in cols}
        for m in cols:
            err = (train[m] - train["final"]) if len(train) else pd.Series(dtype=float)
            if len(err) >= 30:
                lo, hi = np.percentile(err, RANGE_PCT_LONG if lead >= LONG_LEAD else RANGE_PCT)
                inside = ((test["final"] >= test[m] - hi) & (test["final"] <= test[m] - lo)).mean()
                width = (hi - lo) * 100
            else:
                inside = width = np.nan
            out.append({
                "lead": lead, "method": m, "nights": len(test),
                "mae_pp": round(mae[m], 2),
                "bias_pp": round(float((test[m] - test["final"]).mean() * 100), 2),
                "skill_vs_otb": round(1 - mae[m] / mae["otb"], 3) if mae["otb"] else np.nan,
                "skill_vs_last_year": round(1 - mae[m] / mae["last_year_final"], 3) if mae["last_year_final"] else np.nan,
                "range_width_pp": round(float(width), 1) if not np.isnan(width) else np.nan,
                "range_coverage": round(float(inside), 2) if not np.isnan(inside) else np.nan,
                "range_ok": bool(inside >= RANGE_MIN_COVERAGE) if not np.isnan(inside) else np.nan,
            })
    return pd.DataFrame(out)


def season_folds(last_night: pd.Timestamp, months: int = 12) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """Held-out windows, one per season, covering about the last `months`
    months up to `last_night`: (name, from, to). A last season shorter than
    45 days joins the one before it."""
    end = last_night + pd.Timedelta(days=1)
    start = (last_night - pd.DateOffset(months=months)).to_period("M").start_time
    edges = [d for d in pd.date_range(start, end, freq="MS") if d.month in SEASON_STARTS] + [end]
    folds = []
    for a, b in zip(edges, edges[1:]):
        if folds and (b - a).days < 45:
            folds[-1] = (folds[-1][0], folds[-1][1], b)
            continue
        name = {3: "spring", 6: "summer", 9: "autumn", 12: "winter"}[a.month] + f" {a:%Y}"
        folds.append((name, a, b))
    return folds


def score_by_season(bt: pd.DataFrame, folds) -> pd.DataFrame:
    """score() on each held-out season in turn (review by Dina: one summer is
    too little to decide on). Each season's range comes from the nights before
    it, so every fold only learns from the past."""
    parts = [score(bt, a, b).assign(season=name) for name, a, b in folds]
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
