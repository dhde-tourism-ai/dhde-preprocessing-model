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
