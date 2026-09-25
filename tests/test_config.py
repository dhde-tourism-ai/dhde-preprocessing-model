import pytest

from dhde_preprocessing.config import get_workspace_root, list_configured_nodes, load_node_config, resolve_path


def test_workspace_root_env_override(monkeypatch):
    monkeypatch.setenv("DHDE_WORKSPACE_ROOT", "s3://my-bucket/dhde")
    assert get_workspace_root() == "s3://my-bucket/dhde"


def test_resolve_path_joins_without_mangling_s3_uri(monkeypatch):
    monkeypatch.setenv("DHDE_WORKSPACE_ROOT", "s3://my-bucket/dhde")
    assert resolve_path("some-repo/data.csv") == "s3://my-bucket/dhde/some-repo/data.csv"


def test_list_configured_nodes_finds_all_four():
    nodes = list_configured_nodes()
    assert nodes == ["fukui_station", "katsuyama", "rainbow_line", "tojinbo"]


def test_load_node_config_mismatched_key_raises(tmp_path):
    nodes_dir = tmp_path / "nodes"
    nodes_dir.mkdir()
    (nodes_dir / "tojinbo.yaml").write_text("node_key: wrong_key\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_node_config("tojinbo", config_dir=tmp_path)


def test_load_node_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_node_config("nonexistent_node", config_dir=tmp_path)
