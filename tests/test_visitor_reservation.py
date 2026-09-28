import pandas as pd
import pytest

from dhde_preprocessing.sources import visitor_reservation as vr


@pytest.fixture
def repo(monkeypatch, tmp_path):
    monkeypatch.setattr(vr, "resolve_path", lambda p: str(tmp_path / p))
    data = tmp_path / "dino" / "data"
    data.mkdir(parents=True)

    def snap(name, rows):
        pd.DataFrame(rows, columns=["date_visit", "n_people", "amount_fee"]).to_csv(data / name, index=False)

    # 2025-01-02 is booked up over time; the visit-day snapshot is the most complete.
    snap("2025-01-01.csv", [["2025-01-01", 50, 500], ["2025-01-02", 100, 1000], ["2025-01-03", 30, 300]])
    snap("2025-01-02.csv", [["2025-01-01", 0, 0], ["2025-01-02", 180, 1800], ["2025-01-03", 60, 600]])
    # No 2025-01-03 snapshot: that date must fall back to 2025-01-02's reading and be flagged.
    (data / "2025-01-04.csv").write_text("", encoding="utf-8")  # empty pull, skipped
    (data / ".keep").write_text("", encoding="utf-8")
    return tmp_path


def _cfg(enabled=True):
    return {"node_key": "katsuyama", "sources": {"visitor_reservation": {"enabled": enabled, "repo": "dino"}}}


def test_disabled_is_unavailable():
    df, report = vr.load_visitor_reservation(_cfg(enabled=False))
    assert df is None
    assert report.status == "unavailable"


def test_uses_visit_day_snapshot_and_flags_fallback(repo):
    df, report = vr.load_visitor_reservation(_cfg())
    assert report.status == "ok"
    df = df.set_index("date")
    assert df.loc["2025-01-01", "reserved_visitors"] == 50   # lead 0, not the later 0
    assert df.loc["2025-01-02", "reserved_visitors"] == 180  # lead 0 beats lead 1 (100)
    assert df.loc["2025-01-03", "reserved_visitors"] == 60   # latest earlier snapshot
    assert not df.loc["2025-01-02", "from_earlier_snapshot"]
    assert df.loc["2025-01-03", "from_earlier_snapshot"]
    # 2025-01-03 is after the latest snapshot (01-02): bookings so far, not final.
    assert df.loc["2025-01-02", "bookings_final"]
    assert not df.loc["2025-01-03", "bookings_final"]
    assert any("empty snapshot" in n for n in report.notes)


def test_week_ahead_bookings_use_a_snapshot_at_least_7_days_early(monkeypatch, tmp_path):
    """reserved_visitors_lead7 feeds the forecast, so it must only use a
    snapshot taken 7+ days before the visit, never a later, fuller one."""
    monkeypatch.setattr(vr, "resolve_path", lambda p: str(tmp_path / p))
    data = tmp_path / "dino" / "data"
    data.mkdir(parents=True)
    for snap, booked in [("2025-01-01", 100), ("2025-01-03", 200), ("2025-01-10", 900)]:
        pd.DataFrame([["2025-01-10", booked, 0]], columns=["date_visit", "n_people", "amount_fee"]).to_csv(
            data / f"{snap}.csv", index=False)
    pd.DataFrame([["2025-01-05", 5, 0]], columns=["date_visit", "n_people", "amount_fee"]).to_csv(
        data / "2025-01-04.csv", index=False)
    df = vr.load_visitor_reservation(_cfg())[0].set_index("date")
    assert df.loc["2025-01-10", "reserved_visitors"] == 900      # visit day
    assert df.loc["2025-01-10", "reserved_visitors_lead7"] == 200  # 2025-01-03 (lead 7), not 01-01 (lead 9)
    assert pd.isna(df.loc["2025-01-05", "reserved_visitors_lead7"])  # no snapshot 7+ days ahead
