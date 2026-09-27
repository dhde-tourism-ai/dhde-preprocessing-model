import pandas as pd
import pytest

from dhde_preprocessing.integrate import blank_camera_outages, integrate_node

START, END = pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-05")


def _report(**source_cols):
    return {"sources": [{"source": s, "null_rates": {c: 0.0 for c in cols}} for s, cols in source_cols.items()]}


def _cfg(node_key="tojinbo", area_name="坂井市", hotel_scope="regional"):
    return {"node_key": node_key, "sources": {
        "rsi": {"enabled": True, "area_name": area_name},
        "hotel": {"enabled": True, "scope": hotel_scope},
        "weather": {"enabled": True, "station_name": "三国 (Mikuni)"},
    }}


def test_every_day_gets_a_row_and_nothing_after_end():
    master = pd.DataFrame({"date": pd.to_datetime(["2025-01-02", "2025-01-09"]), "temp": [3.0, 4.0]})
    out, _ = integrate_node(master, _report(weather=["temp"]), _cfg(), START, END)
    assert list(out["date"]) == list(pd.date_range(START, END))
    assert out["weather_temp"].notna().sum() == 1
    assert list(out["has_weather"]) == [0, 1, 0, 0, 0]


def test_repeated_dates_are_refused():
    master = pd.DataFrame({"date": pd.to_datetime(["2025-01-02", "2025-01-02"]), "temp": [3.0, 3.0]})
    with pytest.raises(ValueError, match="repeated dates"):
        integrate_node(master, _report(weather=["temp"]), _cfg(), START, END)


def test_survey_days_without_responses_are_zero_inside_the_span_only():
    master = pd.DataFrame({"date": pd.to_datetime(["2025-01-02", "2025-01-04"]),
                           "survey_response_count": [5.0, 2.0]})
    out, _ = integrate_node(master, _report(), _cfg(), START, END)
    counts = out.set_index("date")["survey_response_count"]
    assert pd.isna(counts[pd.Timestamp("2025-01-01")])  # before collection started
    assert counts[pd.Timestamp("2025-01-03")] == 0
    assert pd.isna(counts[pd.Timestamp("2025-01-05")])  # after the last collected day


def test_prefecture_filled_rsi_is_dropped_when_node_has_a_town_file():
    master = pd.DataFrame({"date": pd.to_datetime(["2025-01-01", "2025-01-02"]),
                           "directions": [9999.0, 12.0], "rsi_level": ["prefecture", "area"]})
    report = _report(rsi=["directions", "rsi_level"])
    out, _ = integrate_node(master, report, _cfg(area_name="坂井市"), START, END)
    assert pd.isna(out.loc[0, "rsi_directions"]) and out.loc[1, "rsi_directions"] == 12
    out, _ = integrate_node(master, report, _cfg(node_key="fukui_station", area_name=None), START, END)
    assert out.loc[0, "rsi_directions"] == 9999  # total is all Fukui Station has


def test_partial_and_dead_counter_traffic_days_are_missing():
    master = pd.DataFrame({"date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]),
                           "volume_total": [5000.0, 0.0, 1200.0], "hours_observed": [24.0, 24.0, 8.0]})
    out, _ = integrate_node(master, _report(traffic=["volume_total", "hours_observed"]), _cfg(), START, END)
    assert list(out["traffic_volume_total"].head(3).isna()) == [False, True, True]
    assert list(out["has_traffic"].head(3)) == [1, 0, 0]


def test_flags_become_0_1_and_face_columns_are_left_out():
    master = pd.DataFrame({"date": pd.to_datetime(["2025-01-01"]), "count": [7000.0], "face_male_range00to05": [3],
                           "occ": [0.5], "was_imputed": [True], "neg_fee_adjustment": [0.0]})
    report = _report(camera=["count", "face_male_range00to05"], hotel=["occ", "was_imputed", "neg_fee_adjustment"])
    out, _ = integrate_node(master, report, _cfg(), START, END)
    assert "camera_face_male_range00to05" not in out.columns
    assert out.loc[0, "camera_count"] == 7000
    assert str(out["hotel_was_imputed"].dtype) == "Int8" and out.loc[0, "hotel_was_imputed"] == 1
    assert out.loc[0, "hotel_neg_fee_adjustment"] == 0
    assert out.loc[0, "hotel_scope"] == "regional"


def _camera_rows(node_key, col, values):
    return pd.DataFrame({"date": pd.date_range("2025-09-25", periods=len(values)), "node_key": node_key, col: values})


def test_rainbow_line_zeros_blanked_only_when_all_people_cameras_are_down():
    table = pd.concat([
        _camera_rows("tojinbo", "camera_count", [7201.0, None, 2464.0, None]),
        _camera_rows("fukui_station", "camera_count", [7394.0, None, 3622.0, None]),
        # day 4: people cameras both missing, but gate 1 counted cars, so the system was up
        _camera_rows("rainbow_line", "camera_gate1_vehicle_count", [85.0, 0.0, 41.0, 75.0]),
    ], ignore_index=True)
    table["camera_gate2_vehicle_count"] = [None] * 8 + [2.0, 0.0, 0.0, 0.0]
    out, days = blank_camera_outages(table)
    rainbow = out[out["node_key"] == "rainbow_line"].set_index("date")
    assert days == ["2025-09-26"]
    assert rainbow["camera_gate1_vehicle_count"].isna().tolist() == [False, True, False, False]
    assert rainbow.loc[pd.Timestamp("2025-09-28"), "camera_gate2_vehicle_count"] == 0  # kept: gate 1 was up
    assert rainbow["has_camera"].tolist() == [1, 0, 1, 1]


def test_full_table_keeps_future_rows_training_table_stops_at_end(tmp_path, monkeypatch):
    import json
    from dhde_preprocessing import integrate

    master = pd.DataFrame({"date": pd.to_datetime(["2025-01-01", "2025-01-03"]), "occ": [0.5, 0.6]})
    master.to_parquet(tmp_path / "tojinbo_master.parquet")
    (tmp_path / "tojinbo_coverage_report.json").write_text(json.dumps(_report(hotel=["occ"])))
    monkeypatch.setattr(integrate, "load_node_config", lambda key: _cfg())
    full, train, summary = integrate.build_integrated(["tojinbo"], input_dir=str(tmp_path),
                                                      start="2025-01-01", end="2025-01-02")
    assert full["date"].max() == pd.Timestamp("2025-01-03") and full["hotel_occ"].notna().sum() == 2
    assert train["date"].max() == pd.Timestamp("2025-01-02") and len(train) == 2
    assert summary["train_end"] == "2025-01-02" and summary["full_end"] == "2025-01-03"


def test_table_always_has_the_expected_columns_and_warns_on_empty_ones(tmp_path, monkeypatch):
    """A source failing on a run (or TomTom history arriving) must not
    change the table's shape; an expected column with no data is a warning."""
    import json
    from dhde_preprocessing import integrate

    master = pd.DataFrame({"date": pd.to_datetime(["2025-01-01"]), "occ": [0.5], "brand_new": [1.0]})
    master.to_parquet(tmp_path / "tojinbo_master.parquet")
    (tmp_path / "tojinbo_coverage_report.json").write_text(json.dumps(_report(hotel=["occ", "brand_new"])))
    monkeypatch.setattr(integrate, "load_node_config", lambda key: _cfg())
    full, train, summary = integrate.build_integrated(["tojinbo"], input_dir=str(tmp_path),
                                                      start="2025-01-01", end="2025-01-01")
    assert list(full.columns) == integrate.EXPECTED_COLUMNS == list(train.columns)
    assert train.loc[0, "has_road_congestion"] == 0 and train.loc[0, "has_hotel"] == 1
    warnings = " ".join(summary["warnings"])
    assert "weather_temp" in warnings and "road_congestion" in warnings
    assert "hotel_occ" not in warnings.split("no data")[-1]
    assert "hotel_brand_new" in warnings  # unexpected column is named, not silently dropped
