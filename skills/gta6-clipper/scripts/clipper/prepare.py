"""Step 1: read a VOD URL or local file into a work folder with audio, chat and per-second loudness."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .chat import parse_chat
from .common import audio_path, die, fmt_ts, log, need, print_json, save_json
from .media import ffprobe, is_url, loudness_per_second, ydl

AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio/worstvideo+bestaudio/best"


def safe_name(s: str) -> str:
    return re.sub(r"[^\w-]", "_", s)


def prepare_url(url: str, args) -> tuple[Path, dict]:
    log("reading video metadata ...")
    info = ydl({}).extract_info(url, download=False)
    vid = safe_name(str(info["id"]))
    work = Path(args.work or Path("clips") / vid)
    work.mkdir(parents=True, exist_ok=True)
    if not list(work.glob("audio.*")):
        log("downloading audio only ...")
        ydl({"format": AUDIO_FORMAT, "outtmpl": str(work / "audio.%(ext)s")}).download([url])
    audio = next(p for p in work.glob("audio.*") if p.suffix != ".part")
    if not args.chat and not args.no_chat and "live_chat" in (info.get("subtitles") or {}):
        args.chat = str(download_chat(url, work))
    return work, {"url": info.get("webpage_url") or url, "id": vid, "title": info.get("title", ""),
                  "uploader": info.get("uploader") or info.get("channel") or "",
                  "duration": float(info.get("duration") or 0), "heatmap": info.get("heatmap") or [],
                  "chapters": info.get("chapters") or [], "audio": audio.name}


def download_chat(url: str, work: Path) -> Path:
    path = work / "chat.live_chat.json"
    if not path.exists():
        log("downloading chat replay (long streams take a few minutes) ...")
        ydl({"skip_download": True, "writesubtitles": True, "subtitleslangs": ["live_chat"],
             "outtmpl": str(work / "chat")}).download([url])
    return path


def prepare_file(src: str, args) -> tuple[Path, dict]:
    path = Path(src).resolve()
    if not path.exists():
        die(f"{path} not found")
    work = Path(args.work or Path("clips") / safe_name(path.stem))
    work.mkdir(parents=True, exist_ok=True)
    duration = float(ffprobe(str(path))["format"].get("duration") or 0)
    return work, {"path": str(path), "id": path.stem, "title": path.stem, "uploader": args.uploader or "",
                  "duration": duration, "heatmap": [], "chapters": [], "audio": str(path)}


def cmd_prepare(args):
    need("ffmpeg")
    work, state = (prepare_url if is_url(args.source) else prepare_file)(args.source, args)
    if args.uploader:
        state["uploader"] = args.uploader
    log("measuring loudness per second ...")
    loud = loudness_per_second(audio_path(work, state))
    np.save(work / "loudness.npy", loud)
    state["duration"] = state["duration"] or float(len(loud))
    has_chat = bool(args.chat) and Path(args.chat).exists()
    msgs = parse_chat(Path(args.chat)) if has_chat else []
    if has_chat:
        save_json(work / "chat.json", msgs)
    save_json(work / "state.json", state)
    print_json({"work": str(work), "title": state["title"], "uploader": state["uploader"],
                "duration": fmt_ts(state["duration"]), "chat_messages": len(msgs),
                "heatmap": bool(state["heatmap"]), "loudness_seconds": len(loud)})
