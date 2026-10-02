from datetime import datetime, timezone

import pandas as pd
import pytest
import requests

from dhde_preprocessing.sources import weather_warnings as ww

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


def _report(report_dt, areas):
    """One r8 report: {area code: [(code, status), ...]}."""
    return {"reportDatetime": report_dt, "dataTypeCode": "VPWW55", "warning": {"class20Items": [
        {"areaCode": a, "kinds": [{"code": c, "status": s} if c else {"status": s} for c, s in kinds]}
        for a, kinds in areas.items()]}}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ww, "resolve_live_path", lambda p: str(tmp_path / p))
    return tmp_path


def _cfg(areas=("1821000",)):
    return {"node_key": "n", "sources": {"jma_warning": {"office": "180000", "areas": list(areas)}}}


def _serve(monkeypatch, payload, status=200):
    calls = []

    def _get(url, timeout):
        calls.append(url)
        if isinstance(payload, Exception):
            raise payload
        return _Resp(payload, status)
    monkeypatch.setattr(ww.requests, "get", _get)
    return calls


def test_names_follow_jma_levels():
    assert ww.name_of("rain", 2) == ("レベル２大雨注意報", "Heavy rain advisory (level 2)")
    assert ww.name_of("rain", 4) == ("レベル４大雨危険警報", "Heavy rain danger warning (level 4)")
    assert ww.name_of("wind", 2) == ("強風注意報", "Strong wind advisory")
    assert ww.name_of("wind", 3) == ("暴風警報", "Storm warning")
    assert ww.name_of("fog", 2) == ("濃霧注意報", "Dense fog advisory")


def test_lifted_and_none_are_not_in_force():
    reports = [_report("2026-10-01T09:00:00+09:00", {
        "1821000": [("10", "発表"), ("15", "解除")], "1820100": [("14", "継続")], "1820600": [(None, "発表警報・注意報はなし")]})]
    assert ww.in_force(reports, ["1821000"])[0] == {"10": "2026-10-01T09:00:00+09:00"}
    assert set(ww.in_force(reports, ["1821000", "1820100"])[0]) == {"10", "14"}


def test_a_spell_opens_extends_and_closes(env, monkeypatch):
    _serve(monkeypatch, [_report("2026-10-01T11:40:00+09:00", {"1821000": [("10", "発表")]})])
    df, report = ww.collect(_cfg(), now=T0)
    assert report.status == "ok" and list(df["active"]) == [True]
    _serve(monkeypatch, [_report("2026-10-01T12:30:00+09:00", {"1821000": [("10", "継続"), ("14", "発表")]})])
    ww.collect(_cfg(), now=T0.replace(hour=4))
    _serve(monkeypatch, [_report("2026-10-01T13:10:00+09:00", {"1821000": [("10", "解除"), ("14", "継続")]})])
    df, report = ww.collect(_cfg(), now=T0.replace(hour=5))

    saved = pd.read_csv(env / ww.HISTORY_DIR / "n.csv", dtype={"code": str}).set_index("code")
    rain, thunder = saved.loc["10"], saved.loc["14"]
    assert rain["first_seen"] == "2026-10-01T03:00+00:00" and rain["last_seen"] == "2026-10-01T04:00+00:00"
    assert not rain["active"] and rain["name_ja"] == "レベル２大雨注意報"
    assert thunder["active"] and thunder["first_seen"] == "2026-10-01T04:00+00:00"
    assert report.notes[0] == "in force now: Thunderstorm advisory"


def test_a_second_spell_of_the_same_warning_is_a_new_row(env, monkeypatch):
    on = [_report("2026-10-01T09:00:00+09:00", {"1821000": [("15", "発表")]})]
    off = [_report("2026-10-01T10:00:00+09:00", {"1821000": [("15", "解除")]})]
    for k, payload in enumerate([on, off, on]):
        _serve(monkeypatch, payload)
        df, _ = ww.collect(_cfg(), now=T0.replace(hour=k))
    assert list(df["active"]) == [False, True] and list(df["code"]) == ["15", "15"]


def test_any_of_the_nodes_areas_counts(env, monkeypatch):
    _serve(monkeypatch, [_report("2026-10-01T09:00:00+09:00", {"1844200": [(None, "発表警報・注意報はなし")], "1850100": [("16", "発表")]})])
    df, _ = ww.collect(_cfg(("1844200", "1850100")), now=T0)
    assert list(df["name_en"]) == ["High waves advisory"]


def test_an_office_is_fetched_once_per_run(env, monkeypatch):
    calls = _serve(monkeypatch, [_report("2026-10-01T09:00:00+09:00", {"1821000": [("10", "発表")]})])
    shared: dict = {}
    ww.collect(_cfg(), now=T0, fetched=shared)
    ww.collect({**_cfg(), "node_key": "m"}, now=T0, fetched=shared)
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [requests.ConnectionError(), 503])
def test_a_failed_fetch_leaves_the_history_alone(env, monkeypatch, failure):
    _serve(monkeypatch, [_report("2026-10-01T09:00:00+09:00", {"1821000": [("10", "発表")]})])
    ww.collect(_cfg(), now=T0)
    _serve(monkeypatch, failure) if isinstance(failure, Exception) else _serve(monkeypatch, {}, status=failure)
    df, report = ww.collect(_cfg(), now=T0.replace(hour=4))
    assert report.status == "error" and list(df["active"]) == [True]
    assert pd.read_csv(env / ww.HISTORY_DIR / "n.csv")["last_seen"].iloc[0] == "2026-10-01T03:00+00:00"


@pytest.mark.parametrize("payload", [{}, [{"warning": "?"}], [_report("2026-10-01T10:00:00+09:00", {"1820100": [("10", "発表")]})]])
def test_a_file_that_doesnt_cover_the_node_leaves_the_history_alone(env, monkeypatch, payload):
    # Not a list, a malformed report, or a file without the node's area: none of them means "lifted".
    _serve(monkeypatch, [_report("2026-10-01T09:00:00+09:00", {"1821000": [("10", "発表")]})])
    ww.collect(_cfg(), now=T0)
    _serve(monkeypatch, payload)
    df, report = ww.collect(_cfg(), now=T0.replace(hour=4))
    assert report.status == "error" and "history unchanged" in report.notes[0]
    assert list(pd.read_csv(env / ww.HISTORY_DIR / "n.csv")["active"]) == [True]


def test_codes_not_in_the_table_are_reported(env, monkeypatch):
    _serve(monkeypatch, [_report("2026-10-01T09:00:00+09:00", {"1821000": [("10", "発表"), ("98", "発表"), ("97", "解除")]})])
    df, report = ww.collect(_cfg(), now=T0)
    assert list(df["code"]) == ["10"]
    assert report.notes[-1] == "in force but not in the code table, so not saved: 98"


def test_no_warnings_on_a_first_run(env, monkeypatch):
    _serve(monkeypatch, [_report("2026-10-01T09:00:00+09:00", {"1821000": [(None, "発表警報・注意報はなし")]})])
    df, report = ww.collect(_cfg(), now=T0)
    assert report.status == "ok" and df.empty and report.notes[0] == "in force now: none"


def test_no_config_is_unavailable():
    df, report = ww.collect({"node_key": "n", "sources": {}})
    assert df is None and report.status == "unavailable"
