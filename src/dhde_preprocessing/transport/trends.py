"""
Collect Google Trends search interest for transport terms in Fukui (transport_trends.json).

Google Trends has no official API; this uses pytrends. All terms go in one
request, so their 0-100 values are relative to each other (100 = the
busiest week of the busiest term). The values are an interest index, not
traveller counts, so the app labels them Illustrative.

If Google refuses the request (it rate-limits often), nothing is written and
the app keeps its previous file, so a failed run never blanks the chart.

Run by scripts/build_transport.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[3] / "config" / "transport" / "config.json"


def build(out: Path, *, config: Path = CONFIG) -> bool:
    """Write transport_trends.json to out. False (nothing written) if Google refuses."""
    cfg = json.loads(Path(config).read_text(encoding="utf-8"))["trends"]
    try:
        from pytrends.request import TrendReq
        p = TrendReq(hl="ja-JP", tz=-540, timeout=(10, 30))  # no retries=: pytrends passes method_whitelist, gone in urllib3 2
        p.build_payload([t["term"] for t in cfg["terms"]], timeframe=cfg["timeframe"], geo=cfg["geo"])
        df = p.interest_over_time()
    except Exception as e:  # rate limits, network, pytrends API drift
        print(f"[SKIP] Google Trends unavailable ({type(e).__name__}: {e}); not built today")
        return False
    if df.empty:
        print("[SKIP] Google Trends returned no rows; not built today")
        return False
    partial = bool(df["isPartial"].iloc[-1]) if "isPartial" in df else False
    result = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Google Trends (pytrends), web search, Japan",
        "status": "illustrative",
        "geo": cfg["geo"],
        "timeframe": cfg["timeframe"],
        "note": "Weekly search interest, 0-100 relative to the busiest week of any term in the set. Not traveller counts.",
        "last_week_partial": partial,
        "weeks": [d.strftime("%Y-%m-%d") for d in df.index],
        "terms": [t | {"values": [int(v) for v in df[t["term"]]]} for t in cfg["terms"]],
    }
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[OK] wrote {path} ({len(result['weeks'])} weeks)")
    return True

