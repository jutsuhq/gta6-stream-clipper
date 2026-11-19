#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "yt-dlp>=2025.1.1",
#   "numpy>=1.26",
#   "pillow>=10.0",
#   "opencv-python-headless>=4.8",
#   "faster-whisper>=1.0",
# ]
# ///
"""gta6-clipper: find, cut and check vertical clips from GTA 6 stream VODs.

Pipeline (one work folder per VOD):
  prepare   -> metadata, audio, chat replay, per-second loudness
  signals   -> chat + loudness + replay heatmap -> candidate windows
  transcribe-> faster-whisper on candidate windows only -> candidates.md
  (Claude reads candidates.md and writes picks.json)
  render    -> 1080x1920 clips with facecam split, word captions, hook, -14 LUFS
  check     -> format, loudness, campaign rules, Rockstar and platform warnings

Run with uv:  uv run clip.py <command> ...
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper.captions import STYLES  # noqa: E402
from clipper.common import FONT, log  # noqa: E402


def cmd_doctor(_args):
    checks = [(f"{tool} {shutil.which(tool) or ''}", bool(shutil.which(tool))) for tool in ("ffmpeg", "ffprobe")]
    checks += [(mod, importlib.util.find_spec(mod) is not None)
               for mod in ("yt_dlp", "numpy", "PIL", "cv2", "faster_whisper")]
    checks.append((f"font {FONT}", FONT.exists()))
    for name, found in checks:
        log(f"{'ok ' if found else 'MISSING'} {name}")
    sys.exit(0 if all(found for _, found in checks) else 1)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="clip.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="check ffmpeg, Python packages and font")

    p = sub.add_parser("prepare", help="metadata, audio, chat and loudness for a VOD URL or local file")
    p.add_argument("source")
    p.add_argument("--work", help="work folder (default clips/<video id>)")
    p.add_argument("--chat", help="chat file: yt-dlp live_chat.json, JSON list, or 'seconds<TAB>text' lines")
    p.add_argument("--no-chat", action="store_true", help="skip chat replay download")
    p.add_argument("--uploader", help="streamer name for credit (local files)")

    p = sub.add_parser("signals", help="score every second and pick candidate windows")
    p.add_argument("work")
    p.add_argument("--top", type=int, default=12)
    p.add_argument("--pre", type=int, default=35, help="seconds before the peak")
    p.add_argument("--post", type=int, default=15, help="seconds after the peak")
    p.add_argument("--gap", type=int, default=90, help="minimum seconds between peaks")
    p.add_argument("--chat-lag", type=int, default=8, help="seconds chat reacts after the moment")
    p.add_argument("--skip-start", type=int, default=120, help="ignore the stream intro")
    p.add_argument("--w-chat", type=float, default=0.5)
    p.add_argument("--w-loud", type=float, default=0.3)
    p.add_argument("--w-heat", type=float, default=0.2)

    p = sub.add_parser("transcribe", help="transcribe candidate windows and write candidates.md")
    p.add_argument("work")
    p.add_argument("--model", default="small", help="faster-whisper model: tiny, base, small, medium, large-v3")
    p.add_argument("--lang", default=None)

    p = sub.add_parser("render", help="render picks.json to 1080x1920 clips with captions")
    p.add_argument("work")
    p.add_argument("--ids")
    p.add_argument("--layout", choices=["auto", "cam-top", "blur", "center"], default="auto")
    p.add_argument("--style", choices=sorted(STYLES), default="bold")
    p.add_argument("--cam", default="auto", help="auto, none, or x,y,w,h in source pixels")
    p.add_argument("--campaign")
    p.add_argument("--font")
    p.add_argument("--hook-seconds", type=float, default=3.0)
    p.add_argument("--preset", default="veryfast")
    p.add_argument("--no-captions", action="store_true")

    p = sub.add_parser("check", help="check rendered clips; exit 1 on any FAIL")
    p.add_argument("work")
    p.add_argument("--campaign")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    from clipper import checks, prepare, render, signals, transcribe
    {"prepare": prepare.cmd_prepare, "signals": signals.cmd_signals, "transcribe": transcribe.cmd_transcribe,
     "render": render.cmd_render, "check": checks.cmd_check}[args.cmd](args)


if __name__ == "__main__":
    main()
