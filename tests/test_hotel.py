import pandas as pd
import pytest

from dhde_preprocessing.sources import hotel


@pytest.fixture
def patch_resolve_path(monkeypatch, tmp_path):
    monkeypatch.setattr(hotel, "resolve_path", lambda p: str(tmp_path / p))
    return tmp_path


def _write_snapshot(path, rows):
    pd.DataFrame(rows, columns=["date_visit", "n_stay", "n_people", "n_room", "amount_fee", "n_reserve"]).to_csv(path, index=False)


def test_uses_day_of_snapshot_not_earlier_incomplete_one(patch_resolve_path):
    """Each day's file is a forward-looking snapshot covering many future
    dates — an earlier snapshot's row for a date is less complete than
    that date's own day-of snapshot (more bookings arrive as it
    approaches). The loader must prefer the day-of file."""
    tmp_path = patch_resolve_path
    data_dir = tmp_path / "reservations" / "data"
    data_dir.mkdir(parents=True)

    # 2024-12-19's snapshot: shows a PRELIMINARY (low) count for 12-21,
    # taken 2 days before that date — bookings still incoming.
    _write_snapshot(data_dir / "2024-12-19.csv", [
        ["2024-12-19", 22, 79, 28, 2415021, 22],
        ["2024-12-20", 5, 10, 5, 100000, 5],
        ["2024-12-21", 2, 4, 2, 40000, 2],   # preliminary, incomplete
    ])
    # 2024-12-21's own day-of snapshot: the FINAL, more complete count.
    _write_snapshot(data_dir / "2024-12-21.csv", [
        ["2024-12-21", 9, 25, 9, 300000, 9],  # final — more bookings arrived
        ["2024-12-22", 1, 2, 1, 20000, 1],
    ])

    node_cfg = {"node_key": "tojinbo", "sources": {"hotel": {"enabled": True, "repo": "reservations"}}}
    df, report = hotel.load_hotel(node_cfg)
    assert report.status == "ok"
    df = df.set_index("date")
    assert df.loc[pd.Timestamp("2024-12-21"), "n_reserve"] == 9  # final, not the preliminary 2
    assert df.loc[pd.Timestamp("2024-12-19"), "n_reserve"] == 22


def test_disabled_hotel_returns_unavailable():
    node_cfg = {"node_key": "x", "sources": {"hotel": {"enabled": False, "reason": "nope"}}}
    df, report = hotel.load_hotel(node_cfg)
    assert df is None
    assert report.status == "unavailable"
