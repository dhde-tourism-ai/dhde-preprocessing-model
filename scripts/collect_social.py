#!/usr/bin/env python3
"""
Collect public posts and comments that name each node (Bluesky, YouTube,
Reddit), score their sentiment, and append them to {out}/social/ (see
sources/social_listening.py for what's kept and sentiment.py for the
model).

The searching is Anugra's social-collector
(github.com/anugraaa-rizky/social-media-scraping, pinned in
requirements-social.txt). This script writes it a config with every
node's `social_listening.keywords`, runs it once per platform into a
throwaway SQLite database, then reads that database: posts are matched to
the nodes they name, comments follow their post, and only counts, the
language and the scores leave it. The database, with its text, is
deleted at the end.

Keys, from the environment: YOUTUBE_API_KEY for YouTube;
REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT for Reddit;
BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are optional (without them
Bluesky serves one page per search). A platform without its keys is
skipped and stays missing, not 0. Meant to run weekly (see
.github/workflows/collect-social.yml), which commits --out to the
`live-data` branch.

    python scripts/collect_social.py --out history
    python scripts/collect_social.py --out history --platform bluesky
    python scripts/collect_social.py --out history --db fukui.db   # a database someone already collected

--sample-out FILE also writes up to --sample-size items WITH their text,
language and model label, for checking the model by hand. Text is
personal data in public: keep that file on your machine, never commit or
share it. It's refused inside this repo or --out.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from dhde_preprocessing.config import list_configured_nodes, load_node_config
from dhde_preprocessing.sentiment import try_load_scorer
from dhde_preprocessing.sources import social_listening as sl
from dhde_preprocessing.sources.instagram import JST

FIRST_DAYS = 28
# Posts per keyword per run. YouTube: one search page (50) costs 100 of the
# 10,000 daily quota units, so ~30 keywords fit with room for comments.
LIMITS = {"bluesky": 100, "youtube": 50, "reddit": 100}
COMMENT_LIMIT = 100
SECRETS = {
    "bluesky": [],
    "youtube": ["YOUTUBE_API_KEY"],
    "reddit": ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"],
}
# Per-platform settings for the collector, beyond keywords.
PLATFORM_CONFIG = {
    "bluesky": {"requests_per_minute": 150, "sort": "latest", "fetch_comments": True},
    "youtube": {"requests_per_minute": 60, "order": "date", "fetch_comments": True,
                "comment_order": "time"},
    "reddit": {"requests_per_minute": 60, "sort": "new", "time_filter": "month", "fetch_comments": True,
               "subreddits": ["JapanTravel", "japan", "JapanTravelTips", "travel", "solotravel"],
               "comment_sort": "new"},
}
# Weather and quake bots name 永平寺町 and other towns every day.
EXCLUDE = [r"警報|注意報|震度|気象庁"]
USER_AGENT = "dhde-social/0.1 (Fukui tourism research; https://github.com/dhde-tourism-ai)"


def node_keywords() -> dict[str, list[str]]:
    out = {}
    for node_key in list_configured_nodes():
        kws = sl.node_keywords(load_node_config(node_key))
        if kws:
            out[node_key] = kws
    return out


def platform_keywords(platform: str, kws: list[str]) -> list[str]:
    # Reddit is English-speaking: Japanese place names rarely match there.
    return [k for k in kws if k.isascii()] if platform == "reddit" else kws


def union(keywords: dict[str, list[str]], platform: str) -> list[str]:
    return list(dict.fromkeys(k for kws in keywords.values() for k in platform_keywords(platform, kws)))


def write_config(path: Path, keywords: dict[str, list[str]], platforms: list[str]) -> None:
    cfg = {
        "defaults": {"db_path": "social.db", "log_dir": "logs", "export_dir": "exports",
                     "user_agent": USER_AGENT, "exclude_patterns": EXCLUDE},
        "platforms": {p: {"enabled": p in platforms, "keywords": union(keywords, p), **PLATFORM_CONFIG[p]}
                      for p in SECRETS},
    }
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")


def run_collector(cfg: Path, platform: str, since: str) -> None:
    """One platform into the database. Exit 1 is a query that stopped early,
    which the run summary records; anything else is a setup error."""
    env = {**os.environ, "HASH_SALT": secrets.token_hex(32), "STORE_RAW_USERNAMES": "false"}
    cmd = [sys.executable, "-m", "collector", "run", "-p", platform, "-c", str(cfg), "--since", since,
           "--no-resume", "-n", str(LIMITS[platform]), "--comment-limit", str(COMMENT_LIMIT),
           "--log-level", "WARNING"]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8")
    tail = [line for line in res.stdout.splitlines() if "total posts" in line or "stopped:" in line]
    print("\n".join(f"    {line.strip()}" for line in tail))
    if res.returncode not in (0, 1):
        raise RuntimeError(f"social-collector exit {res.returncode}: {res.stderr.strip()[-300:]}")


def read_db(db: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, list[dict]]]:
    """(posts, comments, {platform: query results of its latest run})."""
    with sqlite3.connect(db) as con:
        posts = pd.read_sql("SELECT platform, id, text, created_at, like_count, language, query FROM posts", con)
        comments = pd.read_sql("SELECT platform, id, post_id, text, created_at, like_count FROM comments", con)
        runs = pd.read_sql("SELECT platform, summary FROM runs ORDER BY id", con)
    latest = {p: json.loads(s).get("queries", []) for p, s in zip(runs["platform"], runs["summary"])}
    return posts, comments, latest


def node_coverage(platform: str, kws: list[str], posts: pd.DataFrame, queries: list[dict], run_date: str,
                  since: str | None) -> tuple:
    """(covered_from, covered_to, capped) for one node on one platform."""
    by_query = {q["query"]: q for q in queries}
    capped_oldest = None
    for k in platform_keywords(platform, kws):
        q = by_query.get(k)
        if q is None or q.get("error") or not q.get("completed"):
            return None, None, False  # quota ran out, or the search failed
        if q.get("posts_seen", 0) >= LIMITS[platform]:
            # The posts this search added first are a subset of what it
            # returned, so their oldest is a safe (late) edge.
            mine = posts[(posts["platform"] == platform) & (posts["query"] == k)]
            if mine.empty:
                return None, None, True
            oldest = mine["date"].min()
            capped_oldest = max(capped_oldest or oldest, oldest)
    since = since or (posts.loc[posts["platform"] == platform, "date"].min() if len(posts) else None)
    frm, to = sl.coverage(run_date, since, capped_oldest)
    return frm, to, capped_oldest is not None


def items_for(node_kws: list[str], platform: str, posts: pd.DataFrame, comments: pd.DataFrame) -> pd.DataFrame:
    kws = platform_keywords(platform, node_kws)
    named = posts["text"].map(lambda t: any(sl.matches(t, k) for k in kws)).astype(bool)
    p = posts[(posts["platform"] == platform) & named]
    c = comments[(comments["platform"] == platform) & comments["post_id"].isin(p["id"])]
    return pd.concat([p.assign(kind="post"), c.assign(kind="comment")], ignore_index=True)


def check_sample_path(path: Path, out: Path) -> None:
    target = path.resolve()
    for root in (REPO, out.resolve()):
        if target.is_relative_to(root):
            sys.exit(f"--sample-out {path} is inside {root}: the sample holds post text, keep it outside the repo")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="history")
    parser.add_argument("--platform", nargs="+", choices=list(SECRETS), default=list(SECRETS))
    parser.add_argument("--db", help="read this social-collector database instead of collecting")
    parser.add_argument("--first-days", type=int, default=FIRST_DAYS, help="how far a first run looks back")
    parser.add_argument("--sample-out", type=Path, help="local CSV of items with text, for checking the model")
    parser.add_argument("--sample-size", type=int, default=100)
    args = parser.parse_args()
    out = Path(args.out)
    if args.sample_out:
        check_sample_path(args.sample_out, out)
    os.environ["DHDE_LIVE_DATA_ROOT"] = str(out.resolve())
    today = datetime.now(JST).date()
    keywords = node_keywords()
    print(f"{len(keywords)} node(s) with keywords: {', '.join(sorted(keywords))}")
    if not keywords:
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        sinces: dict[str, str | None] = {}
        if args.db:
            db = Path(args.db)
            platforms = args.platform
            sinces = {p: None for p in platforms}
        else:
            db = Path(tmp) / "social.db"
            platforms = []
            for p in args.platform:
                missing = [k for k in SECRETS[p] if not os.environ.get(k)]
                if missing:
                    print(f"::warning::{p}: {', '.join(missing)} not set, skipped (stays missing, not 0)")
                else:
                    platforms.append(p)
            cfg = Path(tmp) / "config.yaml"
            write_config(cfg, keywords, platforms)
            for p in list(platforms):
                node_since = [sl.since_date(n, p) for n in keywords]
                first = (today - timedelta(days=args.first_days)).isoformat()
                sinces[p] = min((s or first) for s in node_since)
                print(f"\n{p}: since {sinces[p]}")
                try:
                    run_collector(cfg, p, sinces[p])
                except Exception as e:  # noqa: BLE001 - one platform failing shouldn't lose the others
                    print(f"::warning::{p} failed this run: {e}")
                    platforms.remove(p)
        if not platforms or not db.exists():
            print("nothing collected")
            return 1 if not args.db and args.platform and not platforms else 0

        posts, comments, queries = read_db(db)
        for df in (posts, comments):
            df["date"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce").dt.tz_convert(JST) \
                .dt.strftime("%Y-%m-%d")
        print(f"\n{len(posts)} post(s), {len(comments)} comment(s) collected")

        per_node = {n: [items_for(kws, p, posts, comments) for p in platforms] for n, kws in keywords.items()}
        everything = pd.concat([df for dfs in per_node.values() for df in dfs], ignore_index=True)
        everything = everything.drop_duplicates(["platform", "kind", "id"])
        scorer = try_load_scorer()
        if scorer is not None and len(everything):
            hints = everything["language"].where(everything["kind"] == "post")
            results = scorer(everything["text"].tolist(), [h if isinstance(h, str) else None for h in hints])
            scored = {(r.platform, r.kind, r.id): s for r, s in zip(everything.itertuples(), results)}
        else:
            scored = {}
        lang = {}
        if scorer is None and len(everything):
            try:
                from collector.core.text import detect_language
                lang = {(r.platform, r.kind, r.id): detect_language(r.text or "") for r in everything.itertuples()}
            except ImportError:
                print("::warning::social-collector not installed: no language either")

        run_date = today.isoformat()
        for node_key, kws in keywords.items():
            items, runs = [], []
            for p, df in zip(platforms, per_node[node_key]):
                frm, to, capped = node_coverage(p, kws, posts, queries.get(p, []), run_date, sinces[p])
                if frm:
                    df = df[df["date"] >= frm]
                keys = list(zip(df["platform"], df["kind"], df["id"]))
                items.append(sl.normalize(df, [scored.get(k) for k in keys], [lang.get(k) for k in keys]))
                runs.append({"run_date": run_date, "platform": p, "fetched": len(df), "capped": capped,
                             "covered_from": frm, "covered_to": to})
                print(f"  {node_key} {p}: {len(df)} item(s), "
                      + (f"covers {frm} to {to}{' (capped)' if capped else ''}" if frm else "no coverage this run"))
            added, total = sl.append(node_key, pd.concat(items, ignore_index=True), runs)
            print(f"  {node_key}: {added} new, {total} in the log")

        if args.sample_out and scored:
            sample = everything.sample(min(args.sample_size, len(everything)), random_state=0)
            rows = []
            for r in sample.itertuples():
                s = scored.get((r.platform, r.kind, r.id))
                rows.append({"platform": r.platform, "kind": r.kind, "text": r.text,
                             "language": s.language if s else None, "route": s.route if s else None,
                             "model_label": s.label if s else None, "model_score": s.score if s else None,
                             "your_label": ""})
            args.sample_out.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_csv(args.sample_out, index=False, encoding="utf-8-sig")
            print(f"\nsample of {len(rows)} -> {args.sample_out} (has post text: keep it local)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
