"""Step 5: check rendered clips against format limits, Rockstar's video policy and the campaign rules."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from .captions import words_in
from .common import H, W, die, load_campaign, load_json, load_transcripts, need, work_dir
from .media import ffprobe, measure_lufs, video_stream


def run_checks(meta: dict, pick: dict, post: str, transcript: str, campaign: dict, uploader: str,
               review: dict | None = None):
    out = []

    def add(ok, rule, detail, bad="FAIL"):
        out.append(("PASS" if ok else bad, rule, detail))

    add(meta.get("width") == W and meta.get("height") == H, "format",
        f"{meta.get('width')}x{meta.get('height')}, needs {W}x{H}")
    add(bool(meta.get("has_audio")), "audio", "audio track present" if meta.get("has_audio") else "no audio track")
    dur = float(meta.get("duration") or 0)
    lo, hi = campaign.get("min_seconds", 10), campaign.get("max_seconds", 90)
    add(lo <= dur <= hi, "length", f"{dur:.1f}s, allowed {lo}-{hi}s")
    if (lufs := meta.get("lufs")) is not None:
        add(-16.5 <= lufs <= -11.5, "loudness", f"{lufs:.1f} LUFS, target -14", bad="WARN")

    kind = pick.get("kind", "other")
    add(kind != "cutscene", "rockstar: cutscene",
        "isolated cutscenes are on Rockstar's take-down list" if kind == "cutscene" else "not an isolated cutscene")
    add(kind != "in-game-tv", "rockstar: in-game TV",
        "in-game TV/radio shows in isolation are on Rockstar's take-down list" if kind == "in-game-tv"
        else "not in-game entertainment")
    if pick.get("spoiler"):
        add(bool(campaign.get("allow_spoilers")), "spoiler",
            "story spoiler; Rockstar asks creators not to spoil the plot",
            bad="WARN" if campaign.get("allow_spoilers") is None else "FAIL")
    else:
        add(True, "spoiler", "marked spoiler-free")
    if campaign.get("paid"):
        out.append(("WARN", "rockstar: commercial use",
                    "paid campaign: Rockstar's video policy covers non-commercial use only"))

    text = f"{pick.get('title', '')}\n{post}\n{transcript}".lower()
    hits = [w for w in campaign.get("banned_words", []) if re.search(rf"\b{re.escape(w.lower())}\b", text)]
    add(not hits, "banned words", ", ".join(hits) if hits else "none found")
    post_low = post.lower()
    miss = [t for t in campaign.get("required_hashtags", []) + campaign.get("required_mentions", [])
            if t.lower() not in post_low]
    add(not miss, "required tags", "missing " + ", ".join(miss) if miss else "all present")
    if allowed := [c.lower() for c in campaign.get("allowed_creators", [])]:
        add(uploader.lower() in allowed, "allowed creator", f"source by {uploader!r}")
    add(bool(meta.get("source_url")) and meta["source_url"] in post, "credit", "source link in post text",
        bad="WARN")
    if review is None:
        out.append(("WARN", "review", "no fresh-eyes review yet; run `review`"))
    else:
        add(review.get("verdict") == "yes", "review",
            "fresh reviewer said yes" if review.get("verdict") == "yes" else "; ".join(review.get("faults") or ["no"]))
    return out


def probe_clip(video: Path, source_url: str | None) -> dict:
    pr = ffprobe(str(video))
    v = video_stream(pr)
    return {"width": v.get("width"), "height": v.get("height"),
            "has_audio": any(st["codec_type"] == "audio" for st in pr["streams"]),
            "duration": float(pr["format"].get("duration") or 0), "lufs": measure_lufs(video),
            "source_url": source_url}


def worst(res) -> str:
    levels = {r[0] for r in res}
    return "FAIL" if "FAIL" in levels else "WARN" if "WARN" in levels else "PASS"


def report(campaign: dict, rows) -> str:
    lines = ["# Clip check", "", f"Campaign: {campaign.get('name', 'none (generic limits)')}", ""]
    for cid, title, res in rows:
        lines += [f"## {cid} · {worst(res)} · {title}", "", "| result | rule | detail |", "|---|---|---|"]
        lines += [f"| {lvl} | {rule} | {detail} |" for lvl, rule, detail in res]
        lines.append("")
    return "\n".join(lines)


def cmd_check(args):
    need("ffprobe")
    work = work_dir(args.work)
    state = load_json(work / "state.json")
    campaign = load_campaign(args.campaign)
    transcripts = load_transcripts(work)
    reviews = load_json(work / "review.json", {})
    rows = []
    for meta_path in sorted((work / "out").glob("*.json")):
        video, post_path = meta_path.with_suffix(".mp4"), meta_path.with_suffix(".md")
        if not video.exists():
            continue
        pick = load_json(meta_path)
        post = post_path.read_text(encoding="utf-8") if post_path.exists() else ""
        said = " ".join(w["w"] for w in words_in(transcripts, pick["cut_start"], pick["cut_end"]))
        meta = probe_clip(video, state.get("url") or state.get("path"))
        rows.append((pick["id"], pick["title"], run_checks(meta, pick, post, said, campaign, state.get("uploader", ""),
                                                     reviews.get(pick["id"]))))
    if not rows:
        die("no rendered clips in out/; run `render` first")
    text = report(campaign, rows)
    (work / "check.md").write_text(text, encoding="utf-8")
    print(text)
    sys.exit(1 if any(worst(res) == "FAIL" for _, _, res in rows) else 0)
