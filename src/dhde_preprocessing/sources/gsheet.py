"""
Public Google Sheets fetch + cache — shared by the Ishikawa sources that
come from Milli (Ishikawa Prefecture's tourism open-data project,
https://sites.google.com/view/milli-ishikawa-pref/).

Milli publishes its tabular data as public Google Sheets rather than a
git repo like code4fukui, so there's no sibling checkout to read from.
Each sheet is pulled through its CSV export URL on every run and the
result is written to `{workspace_root}/milli_cache/{cache_name}.csv`. If
the live fetch fails (network, or the sheet stops being public), the
last cached copy is used instead and the caller is told so — a flaky
fetch shouldn't blank out a source that worked yesterday.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from ..config import resolve_path

EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
CACHE_DIR = "milli_cache"


def fetch_sheet(sheet_id: str, cache_name: str, timeout: int = 120) -> tuple[pd.DataFrame, str]:
    """Returns (sheet as DataFrame, one-line note on where it came from).

    Raises only when the live fetch fails AND there's no cached copy.
    """
    cache_path = Path(resolve_path(f"{CACHE_DIR}/{cache_name}.csv"))
    try:
        resp = requests.get(EXPORT_URL.format(sheet_id=sheet_id), timeout=timeout)
        resp.raise_for_status()
        # A sheet that's no longer public redirects to a Google sign-in
        # page (HTML, status 200) instead of failing outright.
        if "text/csv" not in resp.headers.get("Content-Type", ""):
            raise ValueError(f"expected CSV, got {resp.headers.get('Content-Type')!r} — sheet may no longer be public")
        df = pd.read_csv(StringIO(resp.content.decode("utf-8")), low_memory=False)
    except Exception as e:  # noqa: BLE001 - fall back to cache on any fetch/parse failure
        if cache_path.exists():
            return pd.read_csv(cache_path, low_memory=False), f"live fetch failed ({e!r}); used cached copy at {cache_path}"
        raise

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    return df, f"fetched live from Google Sheet {sheet_id}"
