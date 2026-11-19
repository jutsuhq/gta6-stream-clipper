"""Step 4: render picks.json to 1080x1920 clips with facecam split, word captions, hook and -14 LUFS audio."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .captions import STYLES, draw_hook, snap, words_in, write_caption_track
from .common import (CAM_H, FONT, FPS, H, W, die, fmt_ts, load_campaign, load_json, load_transcripts, log, need,
                     parse_ts, run, save_json, work_dir)
from .facecam import resolve_cam
from .media import input_args, stream_inputs

KINDS = {"gameplay", "reaction", "chase", "fail", "funny", "skill", "cutscene", "in-game-tv", "other"}
CAPTION_Y = {"cam-top": 1330, "blur": 1480, "center": 1400}


def load_picks(work: Path) -> list[dict]:
    picks = load_json(work / "picks.json")
    if not isinstance(picks, list) or not picks:
        die("picks.json is missing or empty; write it after reading candidates.md")
    for p in picks:
        for k in ("id", "start", "end", "title", "hook"):
            if k not in p:
                die(f"pick {p.get('id', '?')} lacks {k!r}")
        p["start"], p["end"] = parse_ts(p["start"]), parse_ts(p["end"])
        if p.get("kind", "other") not in KINDS:
            die(f"pick {p['id']}: kind must be one of {sorted(KINDS)}")
        if p["end"] <= p["start"]:
            die(f"pick {p['id']}: end must be after start")
    return picks


def post_text(pick: dict, state: dict, campaign: dict) -> str:
    tags = list(dict.fromkeys(campaign.get("required_hashtags", []) + pick.get("hashtags", ["#gta6"])))
    credit = f"Streamer: {state.get('uploader') or 'unknown'} · {state.get('url') or state.get('path')}"
    caption = pick.get("caption") or pick["hook"]
    return "\n".join([f"# {pick['title']}", "", caption, "", " ".join(tags + campaign.get("required_mentions", [])),
                      "", credit, ""])


def base_filter(layout: str, vi: int, cam, fw: int, fh: int) -> str:
    if layout == "cam-top":
        cx, cy, cw, chh = cam
        gw = int(fh * W / (H - CAM_H)) // 2 * 2
        gx = (fw - gw) // 2
        return (f"[{vi}:v]split=2[a][b];[a]crop={cw}:{chh}:{cx}:{cy},scale={W}:{CAM_H},setsar=1[cam];"
                f"[b]crop={min(gw, fw)}:{fh}:{gx}:0,scale={W}:{H - CAM_H},setsar=1[game];"
                f"[cam][game]vstack,fps={FPS}[base]")
    if layout == "blur":
        return (f"[{vi}:v]split=2[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=24:2,eq=brightness=-0.08[bg];[b]scale={W}:-2[fgv];"
                f"[bg][fgv]overlay=0:(H-h)/2,setsar=1,fps={FPS}[base]")
    return f"[{vi}:v]scale=-2:{H},crop={W}:{H},setsar=1,fps={FPS}[base]"


def ffmpeg_command(inputs, vi: int, ai: int, size, layout: str, cam, start: float, dur: float,
                   captions: Path | None, hook: Path, args, out: Path) -> list[str]:
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for src, hdr in inputs:
        cmd += input_args(src, hdr, start, dur + 0.5)
    fg, last, idx = base_filter(layout, vi, cam, *size), "base", len(inputs)
    if captions:
        cmd += ["-f", "concat", "-safe", "0", "-i", str(captions)]
        fg += f";[{idx}:v]format=rgba[cap];[{last}][cap]overlay=0:0:eof_action=repeat[v1]"
        last, idx = "v1", idx + 1
    cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(hook)]
    fg += f";[{idx}:v]format=rgba[hk];[{last}][hk]overlay=0:0:enable='lt(t,{args.hook_seconds})'[v]"
    fg += f";[{ai}:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]"
    return cmd + ["-filter_complex", fg, "-map", "[v]", "-map", "[aout]", "-t", f"{dur:.3f}",
                  "-c:v", "libx264", "-preset", args.preset, "-crf", "20", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]


def contact_sheet(video: Path, dur: float, out: Path):
    run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", f"fps=12/{dur:.3f},scale=270:-2,tile=4x3",
         "-frames:v", "1", str(out)])


def cmd_render(args):
    need("ffmpeg")
    work = work_dir(args.work)
    state = load_json(work / "state.json")
    campaign = load_campaign(args.campaign)
    picks = load_picks(work)
    if args.ids:
        keep = set(args.ids.split(","))
        picks = [p for p in picks if p["id"] in keep]
    style = STYLES[args.style]
    font = Path(args.font) if args.font else FONT
    transcripts = load_transcripts(work)
    inputs, vi, ai, size = stream_inputs(state)

    cam = None
    if args.layout in ("auto", "cam-top"):
        cam = resolve_cam(args.cam, work, picks, *inputs[vi], size)
        log(f"facecam: {cam or 'not found'}")
    layout = args.layout if args.layout != "auto" else ("cam-top" if cam else "blur")
    if layout == "cam-top" and not cam:
        die("cam-top layout needs a facecam; pass --cam x,y,w,h or use --layout blur")

    out_dir = work / "out"
    out_dir.mkdir(exist_ok=True)
    for p in picks:
        words = words_in(transcripts, p["start"] - 2, p["end"] + 2)
        s, e = snap(p["start"], p["end"], words, campaign.get("min_seconds", 10), campaign.get("max_seconds", 90))
        dur = e - s
        rdir = work / "render" / p["id"]
        shutil.rmtree(rdir, ignore_errors=True)
        rdir.mkdir(parents=True)
        rel = [{"w": w["w"], "s": w["s"] - s, "e": w["e"] - s} for w in words if s <= w["s"] < e]
        captions = None
        if rel and not args.no_captions:
            captions = write_caption_track(rdir, rel, dur, style, font, CAPTION_Y[layout])
        hook = rdir / "hook.png"
        draw_hook(p["hook"], font, CAM_H if layout == "cam-top" else 300, hook)

        out = out_dir / f"{p['id']}.mp4"
        log(f"rendering {p['id']} {fmt_ts(s)}-{fmt_ts(e)} ({dur:.1f}s, {layout}) ...")
        run(ffmpeg_command(inputs, vi, ai, size, layout, cam, s, dur, captions, hook, args, out))
        sheet = out_dir / f"{p['id']}_sheet.jpg"
        contact_sheet(out, dur, sheet)
        (out_dir / f"{p['id']}.md").write_text(post_text(p, state, campaign), encoding="utf-8")
        save_json(out_dir / f"{p['id']}.json", {**p, "cut_start": s, "cut_end": e, "layout": layout})
        print(json.dumps({"id": p["id"], "video": str(out), "sheet": str(sheet), "seconds": round(dur, 1)}))
