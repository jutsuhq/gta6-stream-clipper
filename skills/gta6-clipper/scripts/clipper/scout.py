"""Scout: find what already works before clipping.

Reads channel, shorts or clip listings with yt-dlp (YouTube, Twitch clips, TikTok and anything else
yt-dlp lists) and ranks every entry two ways:
  ratio - views divided by the median views of the same source, so a small channel's hit counts as much
          as a big channel's hit (outliers are the titles and thumbnails worth studying);
  vph   - views per hour since upload, for picking which fresh clips to cut first.
"""
from __future__ import annotations

import csv
import statistics
import time
import urllib.request
from pathlib import Path

from .common import log, save_json
from .media import ydl

FIELDS = ["rank", "ratio", "vph", "views", "median", "age_h", "source", "title", "url", "thumb"]


def thumb_url(e: dict) -> str:
    thumbs = [t for t in (e.get("thumbnails") or []) if t.get("url")]
    if thumbs:
        return max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))["url"]
    if (e.get("ie_key") or "").startswith("Youtube") or "youtube.com" in (e.get("url") or ""):
        return f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg"
    return ""


def score_entries(entries: list[dict], source: str, now: float) -> list[dict]:
    """Ratio to the source's median views, and views per hour when the upload time is known."""
    views = [e["view_count"] for e in entries if e.get("view_count")]
    med = statistics.median(views) if views else 0
    rows = []
    for e in entries:
        v = e.get("view_count")
        if not v:
            continue
        ts = e.get("timestamp")
        age_h = max((now - ts) / 3600, 1.0) if ts else None
        rows.append({"ratio": round(v / med, 2) if med else 0.0, "vph": round(v / age_h) if age_h else None,
                     "views": v, "median": round(med), "age_h": round(age_h, 1) if age_h else None,
                     "source": source, "title": (e.get("title") or "").strip(), "url": e.get("url") or "",
                     "thumb": thumb_url(e)})
    return rows


def rank(rows: list[dict], by: str, min_ratio: float) -> list[dict]:
    if by == "vph":
        keep = sorted((r for r in rows if r["vph"] is not None), key=lambda r: -r["vph"])
    else:
        keep = sorted((r for r in rows if r["ratio"] >= min_ratio), key=lambda r: -r["ratio"])
    return [{"rank": i, **r} for i, r in enumerate(keep, 1)]


def read_source(url: str, limit: int) -> tuple[str, list[dict]]:
    info = ydl({"extract_flat": "in_playlist", "playlistend": limit}).extract_info(url, download=False)
    name = info.get("channel") or info.get("uploader") or info.get("title") or url
    return name, [e for e in (info.get("entries") or []) if e][:limit]


def report(rows: list[dict], by: str, min_ratio: float, thumbs: dict[int, str]) -> str:
    what = "views per hour since upload" if by == "vph" else f"at least {min_ratio}x the source's median views"
    lines = ["# Scout", "", f"Ranked by {what}. Thumbnails of the top entries are in `thumbs/`.", "",
             "| # | ratio | views/h | views | source | title |", "|---|---|---|---|---|---|"]
    for r in rows:
        title = r["title"].replace("|", "/")
        lines.append(f"| {r['rank']} | {r['ratio']}x | {r['vph'] if r['vph'] is not None else '-'} | {r['views']} "
                     f"| {r['source']} | [{title}]({r['url']}) |")
    if thumbs:
        lines += ["", "## Study these", "",
                  ("Read each thumbnail with the title next to it. Write what repeats (words, length, faces, colours, "
                   "text size, what the first frame promises) into `patterns.md` in this folder."), ""]
        lines += [f"- {n}: `thumbs/{Path(p).name}` · {rows[n - 1]['title']}" for n, p in thumbs.items()]
    return "\n".join(lines) + "\n"


def save_thumbs(rows: list[dict], out: Path, count: int) -> dict[int, str]:
    (out / "thumbs").mkdir(parents=True, exist_ok=True)
    saved = {}
    for r in rows[:count]:
        if not r["thumb"]:
            continue
        p = out / "thumbs" / f"{r['rank']:02d}.jpg"
        try:
            with urllib.request.urlopen(urllib.request.Request(r["thumb"], headers={"User-Agent": "Mozilla/5.0"}),
                                        timeout=20) as resp:
                p.write_bytes(resp.read())
            saved[r["rank"]] = str(p)
        except OSError as e:
            log(f"thumbnail {r['rank']} skipped: {e}")
    return saved


def cmd_scout(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    now = time.time()
    rows = []
    for url in args.sources:
        log(f"reading {url} ...")
        try:
            name, entries = read_source(url, args.limit)
        except Exception as e:  # noqa: BLE001 - one dead source should not stop the scan
            log(f"skipped {url}: {e}")
            continue
        scored = score_entries(entries, name, now)
        log(f"  {len(scored)} entries with views from {name}")
        rows += scored
    ranked = rank(rows, args.rank, args.min_ratio)[:args.top]
    with (out / "outliers.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(ranked)
    save_json(out / "outliers.json", ranked)
    thumbs = save_thumbs(ranked, out, args.thumbs)
    text = report(ranked, args.rank, args.min_ratio, thumbs)
    (out / "scout.md").write_text(text, encoding="utf-8")
    print(text)
    if not ranked:
        log("nothing ranked: lower --min-ratio, raise --limit or add sources")
