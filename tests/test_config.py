import pytest

import pandas as pd

from dhde_preprocessing.config import (
    get_workspace_root, list_configured_nodes, load_node_config, read_csv_if_exists, resolve_path, write_csv,
)


def test_workspace_root_env_override(monkeypatch):
    monkeypatch.setenv("DHDE_WORKSPACE_ROOT", "s3://my-bucket/dhde")
    assert get_workspace_root() == "s3://my-bucket/dhde"


def test_resolve_path_joins_without_mangling_s3_uri(monkeypatch):
    monkeypatch.setenv("DHDE_WORKSPACE_ROOT", "s3://my-bucket/dhde")
    assert resolve_path("some-repo/data.csv") == "s3://my-bucket/dhde/some-repo/data.csv"


def test_list_configured_nodes_finds_all_twenty_two():
    # Fukui: the six priority nodes (Mikuni Port, Ono and Maruoka dropped for
    # now, see docs/data_gaps.md). Ishikawa, Toyama, Kyoto and Osaka: four each.
    nodes = list_configured_nodes()
    assert nodes == [
        "arashiyama", "awara_onsen", "eiheiji", "fukui_station", "fushimi_inari", "higashiyama", "himi",
        "kaga_onsen", "kanazawa", "katsuyama", "komatsu", "kyoto_station", "namba", "nanao",
        "osaka_castle", "osaka_station", "rainbow_line", "takaoka", "tateyama", "tojinbo",
        "toyama_station", "usj",
    ]


def test_every_integration_region_node_has_a_config():
    from dhde_preprocessing.integrate import REGIONS

    configured = set(list_configured_nodes())
    for region, nodes in REGIONS.items():
        assert set(nodes) <= configured, region


@pytest.mark.parametrize("node_key", ["kyoto_station", "arashiyama", "fushimi_inari", "higashiyama",
                                      "osaka_station", "namba", "osaka_castle", "usj"])
def test_kyoto_osaka_nodes_use_only_their_own_prefecture(node_key):
    cfg = load_node_config(node_key)["sources"]
    pref = 26 if cfg["weather"]["prec_no"] == "61" else 27  # JMA 61 = Kyoto, 62 = Osaka
    assert cfg["weather"]["prec_no"] in ("61", "62")
    assert cfg["monthly_visitors"]["pref_lgcode"] == pref
    assert str(cfg["monthly_visitors"]["city_lgcode"]).startswith(str(pref))
    for fukui_only in ("camera", "rsi", "hotel", "survey"):
        assert not cfg[fukui_only]["enabled"] and cfg[fukui_only]["reason"]
    assert cfg["rakuten"]["search_radius_km"] == 1  # dense cities: keep neighbouring circles apart


def test_kyoto_osaka_traffic_points_are_not_shared():
    keys = ["kyoto_station", "arashiyama", "fushimi_inari", "higashiyama",
            "osaka_station", "namba", "osaka_castle", "usj"]
    traffic = [load_node_config(k)["sources"]["traffic"] for k in keys]
    points = [t["point_code"] for t in traffic if t["enabled"]]
    assert len(points) == len(set(points))


@pytest.mark.parametrize("node_key", list_configured_nodes())
def test_every_node_config_loads(node_key):
    cfg = load_node_config(node_key)
    required = {"camera", "weather", "rsi", "hotel", "survey", "traffic"}
    optional = {"info_desk", "monthly_visitors", "rakuten",
                "footfall_proxy", "visitor_reservation", "road_congestion", "google_reviews",
                "jma_warning", "instagram", "social_listening"}
    assert required <= set(cfg["sources"]) <= required | optional
    # A proxy only makes sense where the node has no camera of its own.
    if cfg["sources"].get("footfall_proxy", {}).get("enabled"):
        assert not cfg["sources"]["camera"].get("enabled")


def test_load_node_config_mismatched_key_raises(tmp_path):
    nodes_dir = tmp_path / "nodes"
    nodes_dir.mkdir()
    (nodes_dir / "tojinbo.yaml").write_text("node_key: wrong_key\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_node_config("tojinbo", config_dir=tmp_path)


def test_load_node_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_node_config("nonexistent_node", config_dir=tmp_path)


def test_csv_helpers_take_plain_strings_and_create_local_dirs(tmp_path):
    path = f"{tmp_path}/new_dir/snapshots.csv"
    assert read_csv_if_exists(path) is None
    write_csv(pd.DataFrame({"a": [1, 2]}), path)
    assert read_csv_if_exists(path)["a"].tolist() == [1, 2]
