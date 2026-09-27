from datetime import date, timedelta

import pandas as pd
import pytest

from dhde_preprocessing.sources import rakuten


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("RAKUTEN_APP_ID", "test-id")
    monkeypatch.setenv("RAKUTEN_ACCESS_KEY", "test-key")
    monkeypatch.setattr(rakuten, "resolve_path", lambda p: str(tmp_path / p))
    return tmp_path


def _cfg(leads=(1, 7)):
    return {"node_key": "kanazawa", "coordinates": {"lat": 36.58, "lon": 136.65},
            "sources": {"rakuten": {"enabled": True, "lead_days": list(leads)}}}


def test_snapshots_are_logged_and_pivoted_by_lead(env, monkeypatch):
    calls = {"listed": 0, "vacant": []}

    def _listed(geo):
        calls["listed"] += 1
        return 200

    def _vacant(geo, stay, adult_num):
        calls["vacant"].append(stay)
        return (150 if stay == date.today() + timedelta(days=1) else 180), 8000.0
    monkeypatch.setattr(rakuten, "count_listed", _listed)
    monkeypatch.setattr(rakuten, "count_vacant", _vacant)

    df, report = rakuten.load_rakuten(_cfg())
    assert report.status == "ok"
    assert list(df.columns) == ["date", "rakuten_vacant_share_d1", "rakuten_min_charge_d1",
                                "rakuten_vacant_share_d7", "rakuten_min_charge_d7"]
    tomorrow = pd.Timestamp(date.today() + timedelta(days=1))
    assert df.loc[df["date"] == tomorrow, "rakuten_vacant_share_d1"].item() == 0.75
    assert (env / "rakuten_snapshots" / "kanazawa.csv").exists()

    # The hotel count is asked once per run, not once per lead time.
    assert calls["listed"] == 1
    assert len(calls["vacant"]) == 2

    # A second run the same day doesn't re-fetch anything.
    rakuten.load_rakuten(_cfg())
    assert calls["listed"] == 1
    assert len(calls["vacant"]) == 2


def test_failed_hotel_count_takes_no_snapshots(env, monkeypatch):
    def _raise(geo):
        raise RuntimeError("503")
    monkeypatch.setattr(rakuten, "count_listed", _raise)
    monkeypatch.setattr(rakuten, "count_vacant", lambda *a: pytest.fail("must not query vacancies"))
    df, report = rakuten.load_rakuten(_cfg())
    assert df is None
    assert report.status == "error"
    assert any("hotel count failed" in n for n in report.notes)


def test_missing_credentials_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.delenv("RAKUTEN_APP_ID", raising=False)
    monkeypatch.delenv("RAKUTEN_ACCESS_KEY", raising=False)
    df, report = rakuten.load_rakuten(_cfg())
    assert df is None
    assert report.status == "error"


def test_one_failed_lead_keeps_the_other(env, monkeypatch):
    def _vacant(geo, stay, adult_num):
        if stay == date.today() + timedelta(days=7):
            raise RuntimeError("429")
        return 40, None
    monkeypatch.setattr(rakuten, "count_listed", lambda geo: 100)
    monkeypatch.setattr(rakuten, "count_vacant", _vacant)
    df, report = rakuten.load_rakuten(_cfg())
    assert report.status == "ok"
    assert df["rakuten_vacant_share_d1"].dropna().tolist() == [0.4]
    assert any("lead 7d snapshot failed" in n for n in report.notes)


def test_zero_listed_hotels_gives_missing_share_not_divide_by_zero():
    snaps = pd.DataFrame([{"snapshot_date": "2026-09-27", "stay_date": "2026-09-28", "lead_days": 1,
                           "hotels_listed": 0, "hotels_vacant": 0, "min_charge": None}])
    daily = rakuten.to_daily(snaps, [1])
    assert daily["rakuten_vacant_share_d1"].isna().all()


def test_cheapest_charge_reads_room_daily_totals():
    hotel = [{"hotelBasicInfo": {"hotelNo": 1}},
             {"roomInfo": [{"roomBasicInfo": {}}, {"dailyCharge": {"total": 9000}}]},
             {"roomInfo": [{"roomBasicInfo": {}}, {"dailyCharge": {"total": 7500}}]}]
    assert rakuten._cheapest_charge(hotel) == 7500.0


def test_failed_request_never_puts_the_keys_in_the_report(env, monkeypatch):
    """Regression: requests' errors embed the full URL, and the keys are
    query parameters — they leaked into notes / coverage_report.json."""
    monkeypatch.setenv("RAKUTEN_ACCESS_KEY", "SECRET-KEY-123")
    monkeypatch.setattr(rakuten.time, "sleep", lambda *a: None)

    class _Resp:
        status_code, ok, text = 403, False, "forbidden"

    def _get(url, params, timeout):
        assert params["accessKey"] == "SECRET-KEY-123"
        return _Resp()
    monkeypatch.setattr(rakuten.requests, "get", _get)
    df, report = rakuten.load_rakuten(_cfg())
    assert "SECRET-KEY-123" not in str(report.to_dict())
    assert "test-id" not in str(report.to_dict())
    assert any("HTTP 403" in n for n in report.notes)

    def _raise(url, params, timeout):
        raise rakuten.requests.ConnectionError(f"failed for {url}?accessKey={params['accessKey']}")
    monkeypatch.setattr(rakuten.requests, "get", _raise)
    df, report = rakuten.load_rakuten(_cfg())
    assert "SECRET-KEY-123" not in str(report.to_dict())
    assert any("ConnectionError" in n for n in report.notes)
