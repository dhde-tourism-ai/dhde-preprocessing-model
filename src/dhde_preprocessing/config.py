"""
Node config loading and path resolution.

Every I/O path in this package is a config value, never a hardcoded
string in a source module — that's what lets ``workspace_root`` below
become an s3:// prefix later (pandas/fsspec read s3:// URIs the same
way they read local paths) without touching any ingestion code.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def get_workspace_root() -> str:
    """Root directory (or s3:// prefix) that node-config source paths resolve against.

    Local dev default: the parent of this repo (siblings: fukui-kanko-people-flow-data,
    fukui-kanko-trend-data, echizen-coast-kanko-reservation, fukui-kanko-survey,
    jma_station). Override with the DHDE_WORKSPACE_ROOT env var — set it to an
    s3:// URI in the AWS deployment instead of changing any code here.
    """
    override = os.environ.get("DHDE_WORKSPACE_ROOT")
    if override:
        return override.rstrip("/")
    return str(Path(__file__).resolve().parent.parent.parent.parent)


def resolve_path(relative_path: str) -> str:
    """Join a config-declared relative path onto the workspace root.

    Works unchanged whether the root is a local directory or an s3://
    prefix — this is deliberately plain string joining, not pathlib,
    so it doesn't mangle an s3:// URI.
    """
    root = get_workspace_root()
    return f"{root}/{relative_path.lstrip('/')}"


def read_csv_if_exists(path: str, **kwargs) -> "pd.DataFrame | None":
    """Read a CSV written by a previous run, or None if there isn't one yet.

    Takes the resolve_path() string as-is: wrapping it in pathlib.Path would
    turn an s3:// root into a local "s3:" folder, so a missing-file check has
    to go through pandas (FileNotFoundError works for local and s3 alike).
    """
    import pandas as pd

    try:
        return pd.read_csv(path, **kwargs)
    except FileNotFoundError:
        return None


def write_csv(df: "pd.DataFrame", path: str) -> None:
    """Write a CSV to a resolve_path() string, creating local parent dirs.
    s3 has no directories, so there's nothing to create there."""
    if "://" not in path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)


def get_live_data_root() -> str:
    """Where saved history of the live-only sources lives (JARTIC daily
    rows, TomTom snapshots): the `live-data` branch written by
    scripts/collect_live.py. Override with DHDE_LIVE_DATA_ROOT (e.g. a
    checkout of that branch); defaults to the workspace root.
    """
    override = os.environ.get("DHDE_LIVE_DATA_ROOT")
    return override.rstrip("/") if override else get_workspace_root()


def resolve_live_path(relative_path: str) -> str:
    """Like resolve_path, but under get_live_data_root()."""
    return f"{get_live_data_root()}/{relative_path.lstrip('/')}"


def load_node_config(node_key: str, config_dir: Path | str = DEFAULT_CONFIG_DIR) -> dict[str, Any]:
    """Load one node's YAML config (e.g. config/nodes/tojinbo.yaml)."""
    path = Path(config_dir) / "nodes" / f"{node_key}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No config for node '{node_key}' at {path}")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg.get("node_key") != node_key:
        raise ValueError(f"{path} declares node_key={cfg.get('node_key')!r}, expected {node_key!r}")
    return cfg


def list_configured_nodes(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> list[str]:
    nodes_dir = Path(config_dir) / "nodes"
    return sorted(p.stem for p in nodes_dir.glob("*.yaml"))
