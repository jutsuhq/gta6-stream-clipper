"""Paths, output geometry and helpers shared by every pipeline step."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]
FONT = SKILL_DIR / "fonts" / "Anton-Regular.ttf"
CACHE = Path(os.environ.get("GTA6_CLIPPER_CACHE", Path.home() / ".cache" / "gta6-clipper"))
W, H, CAM_H, FPS = 1080, 1920, 700, 30


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def die(msg: str, code: int = 2):
    log(f"error: {msg}")
    sys.exit(code)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def need(tool: str):
    if not shutil.which(tool):
        die(f"{tool} not found on PATH. Install ffmpeg (brew install ffmpeg / winget install ffmpeg / apt install ffmpeg).")


def parse_ts(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    parts = str(v).strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad timestamp: {v!r}")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec


def fmt_ts(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def load_json(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save_json(p: Path, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def print_json(data):
    print(json.dumps(data, indent=1))


def work_dir(p: str) -> Path:
    w = Path(p)
    if not (w / "state.json").exists():
        die(f"{w} has no state.json; run `prepare` first")
    return w


def audio_path(work: Path, state: dict) -> str:
    a = Path(state["audio"])
    return str(a if a.is_absolute() else work / a)


def load_transcripts(work: Path) -> list[dict]:
    return [load_json(p) for p in sorted((work / "transcripts").glob("*.json"))]


def load_campaign(path: str | None) -> dict:
    if not path:
        return {}
    c = json.loads(Path(path).read_text(encoding="utf-8"))
    c.pop("_comment", None)
    return c
