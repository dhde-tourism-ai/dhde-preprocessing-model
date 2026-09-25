import pandas as pd
import pytest

from dhde_preprocessing.sources import hotel


@pytest.fixture
def patch_resolve_path(monkeypatch, tmp_path):
    monkeypatch.setattr(hotel, "resolve_path", lambda p: str(tmp_path / p))
    return tmp_path


def _write_repo(tmp_path, snapshots: dict[str, list[list]], capacity_rows):
    """snapshots: {filename_date: [[date_visit, n_stay, n_people, n_room, amount_fee, n_reserve], ...]}"""
    repo = tmp_path / "hotelrepo"
    (repo / "data").mkdir(parents=True)
    for fname, rows in snapshots.items():
        pd.DataFrame(rows, columns=["date_visit", "n_stay", "n_people", "n_room", "amount_fee", "n_reserve"]) \
            .to_csv(repo / "data" / f"{fname}.csv", index=False)
    pd.DataFrame(capacity_rows, columns=["nrooms", "create_file_begin_date"]).to_csv(repo / "latest_hotel.csv", index=False)
    return repo


def test_disabled_hotel_returns_unavailable():
    node_cfg = {"node_key": "x", "sources": {"hotel": {"enabled": False, "reason": "nope"}}}
    df, report = hotel.load_hotel(node_cfg)
    assert df is None
    assert report.status == "unavailable"


def test_negative_revenue_blanks_only_amount_fee():
    """A refund (negative revenue) should blank amount_fee but leave
    n_room/n_people alone — those still look correct."""
    df = pd.DataFrame({
        "date_visit": pd.to_datetime(["2024-01-01"]),
        "snapshot_date": pd.to_datetime(["2024-01-01"]),
        "lead_time": [0],
        "n_room": [50], "n_people": [80], "amount_fee": [-100000],
        "n_stay": [50], "n_reserve": [20],
    })
    log: list[dict] = []
    P = dict(hotel.DEFAULT_PARAMS, ADR_MIN=3_000, ADR_MAX=30_000)
    hotels = pd.DataFrame({"nrooms": [100], "create_file_begin_date": pd.to_datetime(["2020-01-01"])})
    out = hotel._step_rules(df, P, log, hotels)
    assert pd.isna(out.loc[0, "amount_fee"])
    assert out.loc[0, "n_room"] == 50  # untouched
    assert out.loc[0, "neg_fee_adjustment"] == 1


def test_rooms_exceeding_capacity_blanks_core_columns():
    df = pd.DataFrame({
        "date_visit": pd.to_datetime(["2024-01-01"]),
        "snapshot_date": pd.to_datetime(["2024-01-01"]),
        "lead_time": [0],
        "n_room": [150], "n_people": [200], "amount_fee": [1000000],
        "n_stay": [50], "n_reserve": [20],
    })
    log: list[dict] = []
    P = dict(hotel.DEFAULT_PARAMS, ADR_MIN=3_000, ADR_MAX=30_000)
    hotels = pd.DataFrame({"nrooms": [100], "create_file_begin_date": pd.to_datetime(["2020-01-01"])})
    out = hotel._step_rules(df, P, log, hotels)
    assert pd.isna(out.loc[0, "n_room"])
    assert pd.isna(out.loc[0, "n_people"])
    assert pd.isna(out.loc[0, "amount_fee"])
    assert any(entry["reason"] == "rooms exceed capacity" for entry in log)


def test_capacity_only_counts_hotels_open_by_that_date():
    """A hotel whose create_file_begin_date is after date_visit must not
    count toward capacity on that date."""
    df = pd.DataFrame({
        "date_visit": pd.to_datetime(["2024-01-01", "2024-06-01"]),
        "snapshot_date": pd.to_datetime(["2024-01-01", "2024-06-01"]),
        "lead_time": [0, 0],
        "n_room": [10, 10], "n_people": [15, 15], "amount_fee": [50000, 50000],
        "n_stay": [10, 10], "n_reserve": [5, 5],
    })
    log: list[dict] = []
    P = dict(hotel.DEFAULT_PARAMS, ADR_MIN=3_000, ADR_MAX=30_000)
    hotels = pd.DataFrame({
        "nrooms": [100, 50],
        "create_file_begin_date": pd.to_datetime(["2020-01-01", "2024-03-01"]),
    })
    out = hotel._step_rules(df, P, log, hotels)
    assert out.loc[0, "capacity"] == 100   # second hotel not open yet on 2024-01-01
    assert out.loc[1, "capacity"] == 150   # both open by 2024-06-01


def test_adr_bounds_are_derived_per_repo_not_a_shared_constant():
    """Regression test for a real bug found in production: a fixed
    3,000-30,000 JPY ADR range (validated for one repo's prices) excluded
    87% of a different repo's rows, whose real median price was ~64,500
    JPY — a pricier market, not bad data. Bounds must come from each
    repo's own distribution."""
    import numpy as np
    rng = np.random.default_rng(0)
    cheap = pd.DataFrame({"amount_fee": 10_000 + rng.normal(0, 500, 100), "n_room": [1] * 100})
    expensive = pd.DataFrame({"amount_fee": 70_000 + rng.normal(0, 3000, 100), "n_room": [1] * 100})

    lo_cheap, hi_cheap = hotel._derive_adr_bounds(cheap)
    lo_exp, hi_exp = hotel._derive_adr_bounds(expensive)

    assert lo_cheap < 10_000 < hi_cheap
    assert lo_exp < 70_000 < hi_exp
    # The expensive repo's bounds must not be anywhere near the cheap
    # repo's — a fixed shared constant would fail one or the other.
    assert hi_cheap < lo_exp


def test_end_to_end_load_hotel_with_tiny_synthetic_repo(patch_resolve_path):
    tmp_path = patch_resolve_path
    rows_0101 = [["2024-01-01", 10, 20, 15, 150000, 8], ["2024-01-02", 5, 8, 6, 60000, 3]]
    rows_0102 = [["2024-01-02", 6, 10, 7, 70000, 4], ["2024-01-03", 4, 6, 5, 50000, 2]]
    repo = _write_repo(
        tmp_path,
        {"2024-01-01": rows_0101, "2024-01-02": rows_0102},
        [[100, "2020-01-01"]],
    )
    node_cfg = {"node_key": "tojinbo", "sources": {"hotel": {
        "enabled": True, "repo": repo.name, "scope": "regional",
    }}}
    df, report = hotel.load_hotel(node_cfg)
    assert report.status == "ok"
    df = df.set_index("date")
    # 2024-01-01 only has its own day-of snapshot (lead_time=0).
    assert df.loc[pd.Timestamp("2024-01-01"), "n_room"] == 15
    assert df.loc[pd.Timestamp("2024-01-01"), "occ"] == 0.15
    # 2024-01-02 appears in both snapshots; day-of (lead_time=0, from the
    # 2024-01-02 file) must win over the lead_time=1 row from 2024-01-01's file.
    assert df.loc[pd.Timestamp("2024-01-02"), "n_room"] == 7
