"""
Fetch + cache for sources published as CSV over HTTP rather than as a
sibling git repo — Milli's public Google Sheets (Ishikawa), Toyama's
CKAN open-data portal and Toyama City's AI camera export.

Each fetch writes its result to `{workspace_root}/open_data_cache/{cache_name}.csv`.
If a later live fetch fails (network, or a sheet stops being public),
the last cached copy is used instead and the caller is told so — a flaky
fetch shouldn't blank out a source that worked yesterday.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from ..config import resolve_path

SHEET_EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
CACHE_DIR = "open_data_cache"


def fetch_csv(url: str, cache_name: str, *, params: dict | None = None, encoding: str = "utf-8",
              require_csv_content_type: bool = False, timeout: int = 120,
              **read_csv_kwargs) -> tuple[pd.DataFrame, str]:
    """Returns (CSV as DataFrame, one-line note on where it came from).

    Raises only when the live fetch fails AND there's no cached copy. The
    cache holds the parsed table, so it's re-read without read_csv_kwargs.
    """
    cache_path = Path(resolve_path(f"{CACHE_DIR}/{cache_name}.csv"))
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        # A Google Sheet that's no longer public redirects to a sign-in
        # page (HTML, status 200) instead of failing outright.
        if require_csv_content_type and "text/csv" not in resp.headers.get("Content-Type", ""):
            raise ValueError(f"expected CSV, got {resp.headers.get('Content-Type')!r} — may no longer be public")
        df = pd.read_csv(StringIO(resp.content.decode(encoding)), low_memory=False, **read_csv_kwargs)
    except Exception as e:  # noqa: BLE001 - fall back to cache on any fetch/parse failure
        if cache_path.exists():
            return pd.read_csv(cache_path, low_memory=False), f"live fetch failed ({e!r}); used cached copy at {cache_path}"
        raise

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    return df, f"fetched live from {resp.url.split('?')[0]}"


def fetch_sheet(sheet_id: str, cache_name: str) -> tuple[pd.DataFrame, str]:
    """A public Google Sheet's first tab, via its CSV export URL."""
    return fetch_csv(SHEET_EXPORT_URL.format(sheet_id=sheet_id), cache_name, require_csv_content_type=True)
