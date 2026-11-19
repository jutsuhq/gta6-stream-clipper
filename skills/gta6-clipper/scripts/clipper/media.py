"""ffmpeg, ffprobe and yt-dlp wrappers; video is read remotely, only the needed seconds."""
from __future__ import annotations

import json
import re
import subprocess

import numpy as np

from .common import die

VIDEO_FORMAT = ("bestvideo[height<=1080][vcodec^=avc1]+bestaudio/bestvideo[height<=1080]+bestaudio/"
                "best[height<=1080]/best")


def is_url(s: str) -> bool:
    return bool(re.match(r"https?://", s))


def ydl(opts: dict):
    import yt_dlp
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True, **opts})


def ffprobe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def video_stream(probe: dict) -> dict:
    return next((s for s in probe["streams"] if s["codec_type"] == "video"), {})


def measure_lufs(path) -> float | None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=framelog=quiet",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    return float(m[-1]) if m else None


def loudness_per_second(audio: str, sr: int = 8000) -> np.ndarray:
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", audio, "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], stdout=subprocess.PIPE)
    out, buf, step = [], b"", sr * 2
    while chunk := p.stdout.read(step * 120):
        buf += chunk
        n = len(buf) // step
        if n:
            a = np.frombuffer(buf[: n * step], dtype="<i2").astype(np.float32).reshape(n, sr) / 32768.0
            out.append(10 * np.log10(np.mean(a * a, axis=1) + 1e-10))
            buf = buf[n * step:]
    if p.wait():
        die(f"ffmpeg could not read audio from {audio}")
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, np.float32)


def read_pcm(audio: str, start: float, dur: float, sr: int = 16000) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur), "-i", audio,
                        "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.float32)


def stream_inputs(state: dict):
    if not state.get("url"):
        src = state["path"]
        v = video_stream(ffprobe(src))
        return [(src, {})], 0, 0, (int(v["width"]), int(v["height"]))
    info = ydl({"format": VIDEO_FORMAT}).extract_info(state["url"], download=False)
    fmts = info.get("requested_formats") or [info]
    vfmt = next((f for f in fmts if f.get("vcodec") not in (None, "none")), fmts[0])
    afmt = next((f for f in fmts if f.get("acodec") not in (None, "none")), fmts[-1])
    inputs = [(vfmt["url"], vfmt.get("http_headers") or {})]
    if afmt is not vfmt:
        inputs.append((afmt["url"], afmt.get("http_headers") or {}))
    return inputs, 0, len(inputs) - 1, (int(vfmt.get("width") or 1920), int(vfmt.get("height") or 1080))


def input_args(src: str, headers: dict, start: float | None = None, dur: float | None = None) -> list[str]:
    a = []
    if headers:
        a += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    if start is not None:
        a += ["-ss", f"{start:.3f}"]
    if dur is not None:
        a += ["-t", f"{dur:.3f}"]
    return a + ["-i", src]


def grab_frame(src: str, headers: dict, t: float):
    import cv2
    p = subprocess.run(["ffmpeg", "-v", "error", *input_args(src, headers, t), "-frames:v", "1",
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    if p.returncode or not p.stdout:
        return None
    return cv2.imdecode(np.frombuffer(p.stdout, np.uint8), cv2.IMREAD_COLOR)
