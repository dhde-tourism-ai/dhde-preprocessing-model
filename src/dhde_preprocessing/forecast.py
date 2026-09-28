"""
Daily visitor forecast, 1 to 7 days ahead, per node, from the integrated
training table (output/integrated_fukui_train.parquet, see integrate.py).

What is forecast, per node (TARGETS): the one column that actually counts
visitors there. For Awara Onsen that is overnight guests from its own
hotel feed (not day visitors; its footfall proxy is Tojinbo's camera, so
it was never used as a target). Eiheiji has no such count (pooled survey
answers and the regional hotel feed shared with two other nodes), so it
is reported as pending instead of forecasting a stand-in that no backtest
could check.

Three models, scored the same way:

- **baseline**: same weekday last week (the number to beat).
- **regression**: one ridge regression per node on log visitors. Readable:
  each coefficient is a percentage effect (a holiday adds X%).
- **lightgbm**: one gradient-boosted model pooled across nodes (node is a
  feature), since each node has under two years of days.

**No leakage by construction.** Every feature of day d uses only the
target up to d - 7 (`MIN_LAG`), so a forecast for any of the next 7 days
needs nothing later than the day it's made. Calendar features are known
in advance, and so is `booked_lead7`: bookings for day d as of 7+ days
before it (Katsuyama: museum entries, which also show the closing days
ahead of time; Awara Onsen: hotel guests, read from the raw snapshots
because the hotel cleaning looks at later ones). If a feed is late and a
forecast day has no week-ahead value, the regression falls back to its
version without it and the row is flagged (`week_ahead_missing`). Weather, hotel and RSI are left out
for now: at forecast time we only have weather *forecasts*, hotel
bookings *so far* (the training table holds final bookings, and hotel.py
doesn't yet expose a week-ahead value) and RSI five days late.

**Backtest.** Rolling origin: for each of the last `BACKTEST_WEEKS` weeks,
train on days up to the origin and forecast the 7 days after it, so the
model is always tested on dates it has never seen, later than its
training dates. All models are scored on the same days (a day one model
can't forecast is dropped for all). WAPE = sum |error| / sum actual.

**Visitors.** Each node's count is a different thing (camera detections,
cars, bookings, hotel guests), so the forecast is also converted to one
unit, visitors: `visitors_* = count * factor`, where factor = the node's
official annual visitors (config `official_visitors`) / (mean daily count
that year * days in the year). The mean, not the sum, so days a sensor was
down don't inflate the factor; it needs 300+ measured days. It makes the
yearly total match the official figure; the day-to-day pattern is still the
node's own count. No official figure (Fukui Station) means no conversion.
Error percentages (WAPE) are the same in either unit.

**Range.** low/high = the 5th/95th percentile of the backtest's log
errors, per node and model (see INTERVAL). Its honest coverage is measured by fitting
those percentiles on the earlier half of the backtest weeks and counting
how often the later half falls inside (`coverage_holdout`); the published
range then uses all weeks.
"""
from __future__ import annotations

import json
from pathlib import Path

import jpholiday
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from .config import load_node_config

HORIZON = 7
MIN_CALIBRATION_DAYS = 300
MIN_LAG = 7  # >= HORIZON, so no feature ever needs a day after the forecast is made
BACKTEST_WEEKS = 26
# 5th/95th percentile of past errors. A 10/90 band only caught 58-77% of
# later, unseen days (errors grow in busy summer weeks); this one caught
# 75-89%, so it is published as a roughly 80% range with its measured
# coverage per node (`range_coverage`), never as a guaranteed 90%.
INTERVAL = (0.05, 0.95)

# node -> (columns summed into the target, what the number is)
TARGETS = {
    "tojinbo": (["camera_count"], "camera people count"),
    "fukui_station": (["camera_count"], "camera people count"),
    "rainbow_line": (["camera_gate1_vehicle_count", "camera_gate2_vehicle_count"],
                     "vehicles, both parking gates summed"),
    "katsuyama": (["attraction_reserved_visitors"], "Dinosaur Museum advance bookings"),
    "awara_onsen": (["hotel_n_people"], "overnight hotel guests (Awara Onsen feed), not day visitors"),
}
PENDING = {
    "eiheiji": "no visitor count: only pooled survey answers and the regional hotel feed shared with two other nodes",
}

CALENDAR_FEATURES = ["dow", "month", "week_of_month", "is_day_off", "off_run_len", "prev_day_off", "next_day_off",
                     "is_golden_week", "is_obon", "is_new_year"]
LAG_FEATURES = ["lag7", "lag14", "lag21", "lag28", "same_weekday_mean", "roll28", "lag7_day_off"]
# Values for day d that are already known 7+ days before it: node -> {feature: integrated column}.
KNOWN_AHEAD = {
    "katsuyama": {"booked_lead7": "attraction_reserved_visitors_lead7"},
    "awara_onsen": {"booked_lead7": "hotel_n_people_lead7"},
}
KNOWN_AHEAD_FEATURES = ["booked_lead7"]
FEATURES = CALENDAR_FEATURES + LAG_FEATURES + KNOWN_AHEAD_FEATURES


# --- features -------------------------------------------------------------

def calendar(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Calendar features, all known in advance."""
    md = dates.month * 100 + dates.day
    new_year = (md >= 1229) | (md <= 103)
    off = (dates.dayofweek >= 5) | np.array([jpholiday.is_holiday(d) for d in dates]) | new_year
    off = pd.Series(off, index=dates)
    run_id = (off != off.shift()).cumsum()
    run_len = off.groupby(run_id).transform("size").where(off, 0)
    return pd.DataFrame({
        "dow": dates.dayofweek,
        "month": dates.month,
        # 1-5: which Wednesday of the month etc. The Dinosaur Museum closes on the
        # 2nd and 4th Wednesday (36 of Katsuyama's 47 zero-booking days are Wednesdays).
        "week_of_month": (dates.day - 1) // 7 + 1,
        "is_day_off": off.astype(int).to_numpy(),
        "off_run_len": run_len.to_numpy(),  # 2 = normal weekend, 3+ = long weekend or holiday block
        "prev_day_off": off.shift(1, fill_value=False).astype(int).to_numpy(),
        "next_day_off": off.shift(-1, fill_value=False).astype(int).to_numpy(),
        "is_golden_week": ((md >= 429) & (md <= 506)).astype(int),
        "is_obon": ((md >= 813) & (md <= 816)).astype(int),
        "is_new_year": new_year.astype(int),
    }, index=dates)


def node_target(table: pd.DataFrame, node_key: str) -> pd.Series:
    """Daily target for one node on a full calendar; missing where any part is missing."""
    cols, _ = TARGETS[node_key]
    rows = table[table["node_key"] == node_key].set_index("date").sort_index()
    y = rows[cols].sum(axis=1, min_count=len(cols))
    return y.reindex(pd.date_range(y.index.min(), y.index.max(), name="date"))


def build_features(y: pd.Series, extra_days: int = 0) -> pd.DataFrame:
    """Feature frame for every day of `y` plus `extra_days` future days.

    Lags are on log1p(target) and never shorter than MIN_LAG, so they're
    the same whether a day is in the past or up to 7 days ahead.
    """
    dates = pd.date_range(y.index.min(), y.index.max() + pd.Timedelta(days=extra_days), name="date")
    y = y.reindex(dates)
    ly = np.log1p(y)
    f = calendar(dates)
    for k in (7, 14, 21, 28):
        f[f"lag{k}"] = ly.shift(k)
    f["same_weekday_mean"] = f[["lag7", "lag14", "lag21", "lag28"]].mean(axis=1)
    f["roll28"] = ly.shift(MIN_LAG).rolling(28, min_periods=14).mean()
    # Lets the model see that last week's value was a holiday spike, instead of copying it.
    f["lag7_day_off"] = f["is_day_off"].shift(7)
    f["y"] = y
    return f


# --- models ---------------------------------------------------------------

def _regression_matrix(f: pd.DataFrame, ahead: list[str]) -> pd.DataFrame:
    """Design matrix. `ahead` (the node's KNOWN_AHEAD features) is fixed per
    node, never inferred from which values happen to be present, so train
    and test always have the same columns."""
    x = f[["is_day_off", "off_run_len", "prev_day_off", "next_day_off", "is_golden_week",
           "is_obon", "is_new_year", "same_weekday_mean", "roll28", "lag7_day_off"]].copy()
    x["same_weekday_mean"] = x["same_weekday_mean"].fillna(x["roll28"])
    x["lag7_day_off"] = x["lag7_day_off"].fillna(0)
    for col in ahead:
        x[col] = f[col]
        # Nothing booked a week ahead = closed (Katsuyama: exactly its 47 closing days).
        # A linear model on log bookings can't reach 0 on its own; this lets it.
        x[f"{col}_is_zero"] = (f[col] == 0).astype(float).where(f[col].notna())
    dow = pd.get_dummies(f["dow"].astype(pd.CategoricalDtype(range(7))), prefix="dow", drop_first=True)
    month = pd.get_dummies(f["month"].astype(pd.CategoricalDtype(range(1, 13))), prefix="m", drop_first=True)
    return pd.concat([x, dow, month], axis=1).astype(float)


def _fit_ridge(tr: pd.DataFrame, ahead: list[str]) -> Ridge | None:
    x = _regression_matrix(tr, ahead)
    ok = x.notna().all(axis=1)
    return Ridge(alpha=1.0).fit(x[ok], np.log1p(tr.loc[ok, "y"])) if ok.sum() >= 30 else None


def fit_predict_regression(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """One ridge regression per node, on log1p(target).

    Nodes with week-ahead features get two fits: with them, and without
    them. A test day with its week-ahead value uses the first; a day
    without one (a late feed) uses the second instead of failing or
    returning NaN.
    """
    pred = pd.Series(np.nan, index=test.index)
    for node_key, tr in train.groupby("node_key"):
        te = test[test["node_key"] == node_key]
        if te.empty:
            continue
        ahead = list(KNOWN_AHEAD.get(node_key, {}))
        for cols in ([ahead] if ahead else []) + [[]]:  # full model first, then the fallback
            todo = te.index[pred[te.index].isna()]
            model = _fit_ridge(tr, cols)
            if model is None or todo.empty:
                continue
            x = _regression_matrix(te.loc[todo], cols)
            usable = x.notna().all(axis=1)
            if usable.any():
                pred[todo[usable.to_numpy()]] = np.expm1(model.predict(x[usable]))
    return pred.to_numpy()


def fit_predict_lightgbm(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """One model pooled across nodes, on log1p(target)."""
    cats = pd.CategoricalDtype(sorted(TARGETS))

    def x(df):
        out = df[FEATURES].copy()
        out["node"] = df["node_key"].astype(cats)
        return out

    model = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.03, num_leaves=15,
                              min_child_samples=20, subsample=0.8, subsample_freq=1,
                              colsample_bytree=0.8, random_state=0, verbose=-1)
    model.fit(x(train), np.log1p(train["y"]))
    return np.expm1(model.predict(x(test)))


def predict_baseline(test: pd.DataFrame) -> np.ndarray:
    return np.expm1(test["lag7"]).to_numpy()


MODELS = {
    "baseline": lambda train, test: predict_baseline(test),
    "regression": fit_predict_regression,
    "lightgbm": fit_predict_lightgbm,
}


# --- backtest and forecast ------------------------------------------------

def feature_table(table: pd.DataFrame, extra_days: int = 0, full: pd.DataFrame | None = None) -> pd.DataFrame:
    """Features per node. Targets come from `table` (the training table);
    KNOWN_AHEAD values come from `full` (the full integrated table, which
    has them for the days being forecast), or from `table` if not given."""
    ahead_src = table if full is None else full
    frames = []
    for node_key in TARGETS:
        if node_key not in set(table["node_key"]):
            continue
        f = build_features(node_target(table, node_key), extra_days=extra_days)
        rows = ahead_src[ahead_src["node_key"] == node_key].set_index("date")
        for feat in KNOWN_AHEAD_FEATURES:
            col = KNOWN_AHEAD.get(node_key, {}).get(feat)
            f[feat] = np.log1p(rows[col].reindex(f.index)) if col and col in rows else np.nan
        f["node_key"] = node_key
        frames.append(f.reset_index())
    return pd.concat(frames, ignore_index=True)


def _trainable(f: pd.DataFrame) -> pd.DataFrame:
    return f[f["y"].notna() & f["roll28"].notna()]


def backtest(feats: pd.DataFrame, weeks: int = BACKTEST_WEEKS) -> pd.DataFrame:
    """Rolling-origin backtest: one row per (origin, node, date, model)."""
    last = feats.loc[feats["y"].notna(), "date"].max()
    origins = [last - pd.Timedelta(days=HORIZON * k) for k in range(weeks, 0, -1)]
    rows = []
    for origin in origins:
        train = _trainable(feats[feats["date"] <= origin])
        test = _trainable(feats[(feats["date"] > origin) & (feats["date"] <= origin + pd.Timedelta(days=HORIZON))])
        if test.empty:
            continue
        for name, fit_predict in MODELS.items():
            rows.append(pd.DataFrame({
                "origin": origin, "date": test["date"].to_numpy(), "node_key": test["node_key"].to_numpy(),
                "model": name, "actual": test["y"].to_numpy(), "predicted": fit_predict(train, test),
            }))
    out = pd.concat(rows, ignore_index=True)
    out["horizon"] = (out["date"] - out["origin"]).dt.days
    return out


def score(bt: pd.DataFrame) -> pd.DataFrame:
    """Per node and model: WAPE, MAPE on non-zero days, the log-error band
    for the range, and the band's coverage on weeks it wasn't fitted on."""
    every_model = bt.groupby(["origin", "node_key", "date"])["predicted"].transform(lambda p: p.notna().all())
    bt = bt[every_model]  # same days for every model, or the comparison isn't fair
    origins = sorted(bt["origin"].unique())
    calib_origins = set(origins[: len(origins) // 2])

    def one(g):
        err = g["predicted"] - g["actual"]
        nz = g["actual"] > 0
        log_err = np.log1p(g["actual"]) - np.log1p(g["predicted"].clip(lower=0))
        lo, hi = np.quantile(log_err, INTERVAL)
        calib = g["origin"].isin(calib_origins)
        c_lo, c_hi = np.quantile(log_err[calib], INTERVAL)
        later = log_err[~calib]
        return pd.Series({
            "days": len(g),
            "wape": err.abs().sum() / g["actual"].sum(),
            "mape_nonzero": (err[nz].abs() / g.loc[nz, "actual"]).mean(),
            "log_err_low": lo, "log_err_high": hi,
            "coverage_holdout": ((later >= c_lo) & (later <= c_hi)).mean(),
        })

    return bt.groupby(["node_key", "model"]).apply(one, include_groups=False).reset_index()


def calibration(y: pd.Series, node_cfg: dict) -> dict:
    """Factor converting a node's measured count into visitors (see module docstring)."""
    off = node_cfg.get("official_visitors") or {}
    year, count = off.get("year"), off.get("count")
    info = {"official_year": year, "official_visitors": count, "source": off.get("source"),
            "factor": None, "measured_days": 0, "status": "no official figure"}
    if not year or not count:
        return info
    in_year = y[(y.index.year == year)].dropna()
    info["measured_days"] = int(len(in_year))
    if len(in_year) < MIN_CALIBRATION_DAYS or in_year.mean() <= 0:
        info["status"] = f"too few measured days in {year} ({len(in_year)} < {MIN_CALIBRATION_DAYS})"
        return info
    days = 366 if pd.Timestamp(year=year, month=12, day=31).dayofyear == 366 else 365
    info["factor"] = round(count / (in_year.mean() * days), 6)
    info["status"] = "ok"
    return info


def forecast(table: pd.DataFrame, weeks: int = BACKTEST_WEEKS,
             full: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (forecast rows, backtest scores, report).

    Each node gets the model with the lowest backtest WAPE; its forecast
    for the 7 days after the last observed day, with a low/high range
    from that model's backtest errors at that node.
    """
    feats = feature_table(table, extra_days=HORIZON, full=full)
    scores = score(backtest(feats, weeks=weeks))  # future rows have no y, so they're never tested
    best = scores.loc[scores.groupby("node_key")["wape"].idxmin()].set_index("node_key")
    baseline = scores[scores["model"] == "baseline"].set_index("node_key")

    train = _trainable(feats)
    out, calib = [], {}
    for node_key, row in best.iterrows():
        nf = feats[feats["node_key"] == node_key]
        origin = nf.loc[nf["y"].notna(), "date"].max()
        future = nf[(nf["date"] > origin) & (nf["date"] <= origin + pd.Timedelta(days=HORIZON))]
        pred = MODELS[row["model"]](train, future)
        low = np.expm1(np.log1p(pred) + row["log_err_low"]).clip(min=0)
        high = np.expm1(np.log1p(pred) + row["log_err_high"])
        calib[node_key] = calibration(nf.set_index("date")["y"], load_node_config(node_key))
        factor = calib[node_key]["factor"]
        to_visitors = (lambda v: np.round(v * factor)) if factor else (lambda v: np.full(len(v), np.nan))
        out.append(pd.DataFrame({
            "date": future["date"].to_numpy(), "node_key": node_key, "issued_from": origin,
            "target": TARGETS[node_key][1], "model": row["model"],
            "visitors_est": to_visitors(pred), "visitors_low": to_visitors(low), "visitors_high": to_visitors(high),
            "calibration_factor": float(factor) if factor else np.nan,
            # True when the node uses week-ahead bookings but they weren't there for this day
            "week_ahead_missing": (future[list(KNOWN_AHEAD[node_key])].isna().any(axis=1).to_numpy()
                                   if node_key in KNOWN_AHEAD else False),
            "predicted": np.round(pred), "low": np.round(low), "high": np.round(high),
            "backtest_wape": round(row["wape"], 4),
            "baseline_wape": round(baseline.loc[node_key, "wape"], 4),
            "range_coverage": round(row["coverage_holdout"], 3),
        }))
    # Nodes without a visitor factor (Fukui Station) have all-empty visitor columns;
    # leave them out of the concat's dtype decision, then restore them.
    visitor_cols = ["visitors_est", "visitors_low", "visitors_high", "calibration_factor"]
    fc = pd.concat([o.drop(columns=visitor_cols) for o in out], ignore_index=True)
    fc = fc.join(pd.concat([o[visitor_cols].astype(float) for o in out], ignore_index=True))
    fc = fc[list(out[0].columns)]
    warnings = []
    for node_key, cols in KNOWN_AHEAD.items():
        seen = feats.loc[(feats["node_key"] == node_key) & feats["y"].notna(), list(cols)]
        if not seen.empty and seen.isna().all().any():
            warnings.append(f"{node_key}: no week-ahead bookings in the input ({', '.join(cols.values())}), so it was "
                            f"forecast without them and scores worse; rebuild the node tables with this code "
                            f"(build_node.py, then build_integrated.py)")
    if fc["week_ahead_missing"].any():
        late = fc.loc[fc["week_ahead_missing"], ["node_key", "date"]]
        warnings.append(f"{len(late)} forecast day(s) had no week-ahead bookings (late feed?) and used the "
                        f"model without them: " + ", ".join(f"{n} {d.date()}" for n, d in late.itertuples(index=False)))
    report = {
        "warnings": warnings,
        "backtest_weeks": weeks, "horizon_days": HORIZON, "interval": list(INTERVAL),
        "chosen_model": best["model"].to_dict(),
        "calibration": calib,
        "pending": PENDING,
        "scores": json.loads(scores.round(4).to_json(orient="records")),
    }
    return fc, scores, report


def write_forecast(fc: pd.DataFrame, scores: pd.DataFrame, report: dict, output_dir: str = "output",
                   name: str = "forecast_fukui") -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    fc.to_parquet(out / f"{name}.parquet", index=False)
    fc.to_csv(out / f"{name}.csv", index=False)
    scores.to_csv(out / f"{name}_backtest.csv", index=False)
    with open(out / f"{name}_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"[OK] wrote {out / f'{name}.parquet'} ({len(fc)} rows) and {name}_report.json")
