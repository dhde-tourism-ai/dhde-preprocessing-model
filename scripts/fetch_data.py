#!/usr/bin/env python3
"""
Clone (or update) every code4fukui data repo the node configs need, into
the workspace root the pipeline reads from.

The pipeline expects its data repos as siblings under DHDE_WORKSPACE_ROOT
(default: the folder above this repo). Which repos are needed is read from
config/nodes/*.yaml — every `repo:` value and the first folder of every
`*_csv:` path — so a new node's data is picked up without editing this.

Big repos are fetched sparsely (only the files the pipeline reads), e.g.
ishikawa-kanko-survey is ~570MB but only all.csv is used.

Usage:
    python scripts/fetch_data.py            # clone missing, update existing
    python scripts/fetch_data.py --dry-run  # just list what would happen
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dhde_preprocessing.config import get_workspace_root, list_configured_nodes, load_node_config

GITHUB_ORG = "https://github.com/code4fukui"

# Repos where only a few files are read. Paths are relative to the repo root.
SPARSE_FILES = {
    "fukui-kanko-survey": ["all.csv", "area.csv"],
    "ishikawa-kanko-survey": ["all.csv"],
}


def _walk(value, found: dict[str, set[str]]) -> None:
    """Collect repo -> needed paths ('' = whole repo) from a config tree."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "repo" and isinstance(v, str):
                found.setdefault(v, set()).add("")
            elif k.endswith("_csv") and isinstance(v, str) and "/" in v:
                repo, _, rest = v.partition("/")
                found.setdefault(repo, set()).add(rest.rsplit("/", 1)[0] + "/")
            else:
                _walk(v, found)
    elif isinstance(value, list):
        for v in value:
            _walk(v, found)


def required_repos(node_cfgs: list[dict]) -> dict[str, list[str]]:
    """repo -> sparse paths to check out, or [] for the whole repo."""
    found: dict[str, set[str]] = {}
    for cfg in node_cfgs:
        _walk(cfg.get("sources", {}), found)
    out = {}
    for repo, paths in sorted(found.items()):
        if repo in SPARSE_FILES:
            out[repo] = SPARSE_FILES[repo]
        elif "" in paths:
            out[repo] = []            # referenced as a whole repo
        else:
            out[repo] = sorted(paths)  # only specific folders (e.g. camera sensors)
    return out


def _anchored(paths: list[str]) -> list[str]:
    # Non-cone sparse patterns match anywhere unless anchored with "/".
    return ["/" + p.lstrip("/") for p in paths]


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def fetch(repo: str, paths: list[str], root: Path) -> None:
    dest = root / repo
    if (dest / ".git").exists():
        if paths:
            _git("sparse-checkout", "set", "--no-cone", *_anchored(paths), cwd=dest)
        _git("pull", "--ff-only", "--quiet", cwd=dest)
        return
    if dest.exists():
        raise RuntimeError(f"{dest} exists but isn't a git clone; move it aside and re-run")
    url = f"{GITHUB_ORG}/{repo}"
    if paths:
        _git("clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse", url, str(dest))
        _git("sparse-checkout", "set", "--no-cone", *_anchored(paths), cwd=dest)
    else:
        _git("clone", "--quiet", "--depth", "1", url, str(dest))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(get_workspace_root())
    if "://" in str(root):
        print(f"DHDE_WORKSPACE_ROOT is {root}; this script only fills a local folder.")
        return 1
    repos = required_repos([load_node_config(k) for k in list_configured_nodes()])
    print(f"workspace root: {root}")
    failed = []
    for repo, paths in repos.items():
        state = "update" if (root / repo / ".git").exists() else "clone"
        scope = ", ".join(paths) if paths else "whole repo"
        print(f"  {state:6s} {repo}  ({scope})")
        if args.dry_run:
            continue
        try:
            fetch(repo, paths, root)
        except (subprocess.CalledProcessError, RuntimeError) as e:
            failed.append(repo)
            print(f"    failed: {e}")
    if failed:
        print(f"\n{len(failed)} repo(s) failed: {', '.join(failed)}")
        return 1
    print("\ndone" if not args.dry_run else "\n(dry run, nothing fetched)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
