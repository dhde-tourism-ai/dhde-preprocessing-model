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


def test_list_configured_nodes_finds_all_fourteen():
    # Fukui: the six priority nodes (Mikuni Port, Ono and Maruoka dropped for
    # now, see docs/data_gaps.md). Ishikawa and Toyama: four nodes each.
    nodes = list_configured_nodes()
    assert nodes == [
        "awara_onsen", "eiheiji", "fukui_station", "himi", "kaga_onsen", "kanazawa", "katsuyama",
        "komatsu", "nanao", "rainbow_line", "takaoka", "tateyama", "tojinbo", "toyama_station",
    ]


@pytest.mark.parametrize("node_key", list_configured_nodes())
def test_every_node_config_loads(node_key):
    cfg = load_node_config(node_key)
    required = {"camera", "weather", "rsi", "hotel", "survey", "traffic"}
    optional = {"info_desk", "monthly_visitors", "rakuten",
                "footfall_proxy", "visitor_reservation", "road_congestion"}
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
