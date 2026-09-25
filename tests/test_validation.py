import pandas as pd

from dhde_preprocessing.validation import unavailable_report, validate_daily


def test_unavailable_report():
    r = unavailable_report("camera", "katsuyama", "no sensor exists")
    assert r.status == "unavailable"
    assert r.notes == ["no sensor exists"]
    assert r.row_count == 0


def test_validate_daily_ok_no_gaps():
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=5, freq="D"),
        "count": [1, 2, 3, 4, 5],
    })
    r = validate_daily(df, source="camera", node_key="tojinbo")
    assert r.status == "ok"
    assert r.row_count == 5
    assert r.missing_days == 0
    assert r.date_min == "2024-01-01"
    assert r.date_max == "2024-01-05"


def test_validate_daily_flags_calendar_gaps():
    dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-05"])  # skips 3rd, 4th
    df = pd.DataFrame({"date": dates, "count": [1, 2, 3]})
    r = validate_daily(df, source="camera", node_key="tojinbo")
    assert r.missing_days == 2
    assert any("missing" in n for n in r.notes)


def test_validate_daily_flags_high_null_rate():
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=10, freq="D"),
        "mostly_null": [None] * 8 + [1, 2],
    })
    r = validate_daily(df, source="rsi", node_key="tojinbo")
    assert r.null_rates["mostly_null"] == 0.8
    assert any("mostly_null" in n for n in r.notes)


def test_validate_daily_empty_df_is_error():
    df = pd.DataFrame(columns=["date", "count"])
    r = validate_daily(df, source="camera", node_key="tojinbo")
    assert r.status == "error"
