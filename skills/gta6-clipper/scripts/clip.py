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
import collections
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

SKILL_DIR = Path(__file__).resolve().parent.parent
FONT = SKILL_DIR / "fonts" / "Anton-Regular.ttf"
CACHE = Path(os.environ.get("GTA6_CLIPPER_CACHE", Path.home() / ".cache" / "gta6-clipper"))
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
W, H, CAM_H, FPS = 1080, 1920, 700, 30

# Chat tokens that usually mean "something just happened". Weight is added to a base of 1 per message.
HYPE = {
    "kekw": 2, "lul": 1.5, "lol": 1, "lmao": 2, "lmfao": 2, "omegalul": 2.5, "icant": 2,
    "pog": 1.5, "poggers": 1.5, "pogchamp": 1.5, "pogu": 1.5, "w": 1, "dub": 1,
    "wtf": 2, "omg": 1.5, "holy": 1.5, "monkas": 1.5, "rip": 1.5, "f": 1, "l": 1,
    "wasted": 2, "busted": 2, "clip": 3, "clipped": 3, "ez": 0.5,
    "😂": 1.5, "🤣": 1.5, "💀": 1.5, "😭": 1.5, "🔥": 1, "😱": 1.5,
}
HYPE_PHRASES = {"clip it": 3, "clip that": 3, "no way": 2, "?????": 1.5, "!!!!": 1}
TOKEN_RE = re.compile(r"[\w']+|[^\w\s]", re.UNICODE)

KINDS = {"gameplay", "reaction", "chase", "fail", "funny", "skill", "cutscene", "in-game-tv", "other"}


# ---------------------------------------------------------------- small helpers

def log(*a):
    print(*a, file=sys.stderr, flush=True)


def die(msg: str, code: int = 2):
    log(f"error: {msg}")
    sys.exit(code)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def parse_ts(v) -> float:
    """'1:02:03.5', '62:03', '3723.5' or a number -> seconds."""
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


# ---------------------------------------------------------------- chat

def msg_weight(text: str) -> float:
    low = text.lower()
    extra = sum(w for t in TOKEN_RE.findall(low) if (w := HYPE.get(t)))
    extra += sum(w for p, w in HYPE_PHRASES.items() if p in low)
    return 1.0 + min(3.0, extra)


def _ytdlp_chat_line(obj) -> tuple[float, str] | None:
    act = obj.get("replayChatItemAction") or {}
    try:
        t = int(act.get("videoOffsetTimeMsec", obj.get("videoOffsetTimeMsec"))) / 1000
    except (TypeError, ValueError):
        return None
    for a in act.get("actions", []):
        item = (a.get("addChatItemAction") or {}).get("item") or {}
        r = item.get("liveChatTextMessageRenderer")
        if not r:
            continue
        out = []
        for run_ in r.get("message", {}).get("runs", []):
            if "text" in run_:
                out.append(run_["text"])
            elif "emoji" in run_:
                sc = run_["emoji"].get("shortcuts") or []
                out.append(sc[0] if sc else run_["emoji"].get("emojiId", ""))
        return t, "".join(out)
    return None


def parse_chat(path: Path) -> list[tuple[float, str]]:
    """Read chat from yt-dlp live_chat.json, a JSON list, or 'time<TAB|,>text' lines."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    msgs: list[tuple[float, str]] = []
    stripped = raw.lstrip()
    data = None
    if stripped.startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
    if data is not None:
        for m in data:
            if isinstance(m, dict):
                t = m.get("t", m.get("time", m.get("offset", m.get("time_in_seconds"))))
                text = m.get("text", m.get("message", ""))
            else:
                t, text = m[0], m[1]
            if t is not None:
                msgs.append((parse_ts(t), str(text)))
    elif stripped.startswith("{"):
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                got = _ytdlp_chat_line(json.loads(line))
            except json.JSONDecodeError:
                continue
            if got:
                msgs.append(got)
    else:
        for line in raw.splitlines():
            m = re.match(r"\s*\[?([\d:.]+)\]?[\t,; ]+(.*)", line)
            if m:
                try:
                    msgs.append((parse_ts(m.group(1)), m.group(2)))
                except ValueError:
                    pass
    msgs.sort(key=lambda x: x[0])
    return msgs


def chat_series(msgs, n: int) -> np.ndarray:
    s = np.zeros(n, dtype=np.float32)
    for t, text in msgs:
        i = int(t)
        if 0 <= i < n:
            s[i] += msg_weight(text)
    return s


def top_messages(msgs, a: float, b: float, k: int = 6) -> list[tuple[str, int]]:
    c = collections.Counter()
    for t, text in msgs:
        if a <= t <= b:
            norm = re.sub(r"(.)\1{2,}", r"\1\1", text.strip().lower())[:40]
            if norm:
                c[norm] += 1
    return c.most_common(k)


# ---------------------------------------------------------------- signal math

def moving_mean(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(x) == 0:
        return x.astype(np.float32)
    kern = np.ones(k, dtype=np.float32) / k
    return np.convolve(x, kern, mode="same").astype(np.float32)


def rolling_median(x: np.ndarray, k: int, stride: int = 10) -> np.ndarray:
    """Coarse rolling median (computed every `stride` samples) used as a moving baseline."""
    n = len(x)
    if n == 0:
        return x
    half = k // 2
    idx = np.arange(0, n, stride)
    med = np.array([np.median(x[max(0, i - half): i + half + 1]) for i in idx], dtype=np.float32)
    return np.interp(np.arange(n), idx, med).astype(np.float32)


def robust_z(x: np.ndarray) -> np.ndarray:
    if len(x) == 0:
        return x
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    if mad < 1e-6:
        mad = float(np.std(x)) or 1.0
    return np.clip((x - med) / mad, -3, 15).astype(np.float32)


def combine(parts: dict[str, tuple[np.ndarray, float]]) -> np.ndarray:
    """Weighted sum of z-scored series; weights are renormalised over the series present."""
    have = {k: v for k, v in parts.items() if v[0] is not None and len(v[0]) and np.ptp(v[0]) > 0}
    if not have:
        raise ValueError("no usable signal (no chat, flat audio, no heatmap)")
    total = sum(w for _, w in have.values())
    n = min(len(a) for a, _ in have.values())
    return sum(robust_z(a[:n]) * (w / total) for a, w in have.values())


def pick_peaks(score: np.ndarray, n: int, min_gap: int, skip_start: int = 0, skip_end: int = 0,
               min_score: float = 0.0) -> list[int]:
    """Greedy top-n peaks above min_score (0 = the stream's median moment), at least min_gap apart."""
    order = np.argsort(-score, kind="stable")
    hi = len(score) - skip_end
    picked: list[int] = []
    for i in order:
        i = int(i)
        if score[i] <= min_score:
            break
        if i < skip_start or i >= hi:
            continue
        if all(abs(i - p) >= min_gap for p in picked):
            picked.append(i)
            if len(picked) >= n:
                break
    return sorted(picked)


def heat_series(heatmap, n: int) -> np.ndarray | None:
    if not heatmap:
        return None
    xs = [(h["start_time"] + h["end_time"]) / 2 for h in heatmap]
    ys = [h["value"] for h in heatmap]
    return np.interp(np.arange(n), xs, ys).astype(np.float32)


# ---------------------------------------------------------------- words, captions, snapping

def words_in(transcripts: list[dict], a: float, b: float) -> list[dict]:
    seen, out = set(), []
    for tr in transcripts:
        for w in tr.get("words", []):
            key = (round(w["s"], 2), w["w"])
            if w["s"] >= a - 0.05 and w["e"] <= b + 0.05 and key not in seen:
                seen.add(key)
                out.append(w)
    return sorted(out, key=lambda w: w["s"])


def snap(start: float, end: float, words: list[dict], min_s: float, max_s: float, reach: float = 1.5):
    """Move cut points onto word edges so no word is cut in half, then fit the length limits."""
    starts = [w["s"] for w in words]
    ends = [w["e"] for w in words]
    near = [s for s in starts if abs(s - start) <= reach]
    if near:
        start = min(near, key=lambda s: abs(s - start)) - 0.15
    near = [e for e in ends if abs(e - end) <= reach]
    if near:
        end = min(near, key=lambda e: abs(e - end)) + 0.25
    start = max(0.0, start)
    if end - start > max_s:
        inside = [e + 0.25 for e in ends if start + min_s <= e + 0.25 <= start + max_s]
        end = max(inside) if inside else start + max_s
    if end - start < min_s:
        end = start + min_s
    return round(start, 3), round(end, 3)


def chunk_words(words: list[dict], max_words: int = 3, max_chars: int = 16, max_gap: float = 0.6):
    chunks, cur = [], []
    for w in words:
        text = w["w"].strip()
        if not text:
            continue
        if cur:
            gap = w["s"] - cur[-1]["e"]
            chars = sum(len(x["w"].strip()) + 1 for x in cur) + len(text)
            if len(cur) >= max_words or chars > max_chars or gap > max_gap or re.search(r"[.!?,]$", cur[-1]["w"].strip()):
                chunks.append(cur)
                cur = []
        cur.append({**w, "w": text})
    if cur:
        chunks.append(cur)
    return chunks


def caption_timeline(chunks, clip_len: float):
    """-> list of (t0, t1, chunk_index, word_index or None for blank)."""
    tl, t = [], 0.0
    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else clip_len
        end = min(nxt, ch[-1]["e"] + 0.4, clip_len)
        for wi, w in enumerate(ch):
            s = max(w["s"], t)
            if s > t + 0.01:
                tl.append((t, s, None, None))
            e = ch[wi + 1]["s"] if wi + 1 < len(ch) else end
            e = max(e, s + 0.05)
            tl.append((s, e, ci, wi))
            t = e
    if t < clip_len:
        tl.append((t, clip_len, None, None))
    return tl


# ---------------------------------------------------------------- facecam geometry

def cluster_faces(dets: list[list[tuple]], fw: int, min_share: float = 0.5):
    """dets: per-frame lists of (x, y, w, h, score). Return the median box of the face seen in most frames."""
    flat = [(fi, d) for fi, frame in enumerate(dets) for d in frame]
    if not flat:
        return None
    tol = 0.06 * fw
    best, best_frames = None, 0
    for _, d in flat:
        cx, cy = d[0] + d[2] / 2, d[1] + d[3] / 2
        members = [(fi, e) for fi, e in flat
                   if abs(e[0] + e[2] / 2 - cx) < tol and abs(e[1] + e[3] / 2 - cy) < tol]
        frames = len({fi for fi, _ in members})
        if frames > best_frames:
            best_frames, best = frames, [e for _, e in members]
    if best_frames < max(2, math.ceil(min_share * len(dets))):
        return None
    arr = np.array([b[:4] for b in best], dtype=np.float32)
    return tuple(int(v) for v in np.median(arr, axis=0))


def cam_box(face, fw: int, fh: int, aspect: float = W / CAM_H, scale: float = 3.2):
    """Crop box (x, y, w, h) around a facecam face, in source pixels, clamped to the frame."""
    x, y, w, h = face
    cw = min(fw, int(w * scale))
    ch = min(fh, int(cw / aspect))
    cw = int(ch * aspect)
    cx, cy = x + w / 2, y + h / 2
    bx = int(min(max(0, cx - cw / 2), fw - cw))
    by = int(min(max(0, cy - ch * 0.45), fh - ch))
    return bx - bx % 2, by - by % 2, cw - cw % 2, ch - ch % 2


# ---------------------------------------------------------------- checks

def run_checks(meta: dict, pick: dict, post: str, transcript: str, campaign: dict, uploader: str):
    """Return [(level, rule, detail)], level in PASS/WARN/FAIL."""
    out = []

    def add(ok, rule, detail, bad="FAIL"):
        out.append(("PASS" if ok else bad, rule, detail))

    add(meta.get("width") == W and meta.get("height") == H, "format",
        f"{meta.get('width')}x{meta.get('height')}, needs {W}x{H}")
    add(bool(meta.get("has_audio")), "audio", "audio track present" if meta.get("has_audio") else "no audio track")
    dur = float(meta.get("duration") or 0)
    lo, hi = campaign.get("min_seconds", 10), campaign.get("max_seconds", 90)
    add(lo <= dur <= hi, "length", f"{dur:.1f}s, allowed {lo}-{hi}s")
    lufs = meta.get("lufs")
    if lufs is not None:
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
    allowed = [c.lower() for c in campaign.get("allowed_creators", [])]
    if allowed:
        add(uploader.lower() in allowed, "allowed creator", f"source by {uploader!r}")
    add(bool(meta.get("source_url")) and meta["source_url"] in post, "credit", "source link in post text",
        bad="WARN")
    return out


# ---------------------------------------------------------------- media helpers

def need(tool: str):
    if not shutil.which(tool):
        die(f"{tool} not found on PATH. Install ffmpeg (brew install ffmpeg / winget install ffmpeg / apt install ffmpeg).")


def ffprobe(path: str, extra: list[str] | None = None) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", *(extra or []), "-print_format", "json",
                          "-show_streams", "-show_format", path], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def measure_lufs(path: Path) -> float | None:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=framelog=quiet",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    return float(m[-1]) if m else None


def loudness_per_second(audio: str, sr: int = 8000) -> np.ndarray:
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", audio, "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], stdout=subprocess.PIPE)
    out, buf, step = [], b"", sr * 2
    while True:
        chunk = p.stdout.read(step * 120)
        if not chunk:
            break
        buf += chunk
        n = len(buf) // step
        if n:
            a = np.frombuffer(buf[: n * step], dtype="<i2").astype(np.float32).reshape(n, sr) / 32768.0
            out.append(10 * np.log10(np.mean(a * a, axis=1) + 1e-10))
            buf = buf[n * step:]
    p.wait()
    if p.returncode:
        die(f"ffmpeg could not read audio from {audio}")
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, np.float32)


def ydl(opts: dict):
    import yt_dlp
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True, **opts})


def is_url(s: str) -> bool:
    return bool(re.match(r"https?://", s))


def stream_inputs(state: dict):
    """-> (list of (url_or_path, headers)), video_index, audio_index, (width, height)."""
    if not state.get("url"):
        src = state["path"]
        pr = ffprobe(src)
        v = next(s for s in pr["streams"] if s["codec_type"] == "video")
        return [(src, {})], 0, 0, (int(v["width"]), int(v["height"]))
    info = ydl({"format": "bestvideo[height<=1080][vcodec^=avc1]+bestaudio/bestvideo[height<=1080]+bestaudio/"
                          "best[height<=1080]/best"}).extract_info(state["url"], download=False)
    fmts = info.get("requested_formats") or [info]
    vfmt = next((f for f in fmts if f.get("vcodec") not in (None, "none")), fmts[0])
    afmt = next((f for f in fmts if f.get("acodec") not in (None, "none")), fmts[-1])
    ins = [(vfmt["url"], vfmt.get("http_headers") or {})]
    ai = 0
    if afmt is not vfmt:
        ins.append((afmt["url"], afmt.get("http_headers") or {}))
        ai = 1
    return ins, 0, ai, (int(vfmt.get("width") or 1920), int(vfmt.get("height") or 1080))


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


def yunet_model() -> Path:
    path = CACHE / "face_detection_yunet_2023mar.onnx"
    if not path.exists():
        import urllib.request
        CACHE.mkdir(parents=True, exist_ok=True)
        log(f"downloading face model -> {path}")
        urllib.request.urlretrieve(YUNET_URL, path)
    return path


def detect_cam(src: str, headers: dict, times: list[float], fw: int):
    import cv2
    det, dets = None, []
    for t in times:
        img = grab_frame(src, headers, t)
        if img is None:
            continue
        h, w = img.shape[:2]
        if det is None:
            det = cv2.FaceDetectorYN.create(str(yunet_model()), "", (w, h), 0.8, 0.3, 50)
        det.setInputSize((w, h))
        _, faces = det.detect(img)
        frame = []
        for f in (faces if faces is not None else []):
            x, y, fw_, fh_ = (float(v) for v in f[:4])
            # a streamer's facecam face is small and usually sits away from the frame centre
            if fw_ < w * 0.25:
                frame.append((x, y, fw_, fh_, float(f[-1])))
        dets.append(frame)
    return cluster_faces(dets, fw) if dets else None


# ---------------------------------------------------------------- caption images

def _font(size: int, font_path: Path):
    from PIL import ImageFont
    return ImageFont.truetype(str(font_path), size)


STYLES = {
    "bold": {"upper": True, "size": 92, "fill": "#FFFFFF", "hi": "#FFD400", "stroke": 9, "box": False},
    "box": {"upper": True, "size": 80, "fill": "#FFFFFF", "hi": "#7CFF4F", "stroke": 0, "box": True},
    "clean": {"upper": False, "size": 74, "fill": "#FFFFFF", "hi": "#FFFFFF", "stroke": 4, "box": False},
}


def draw_words(words: list[str], hi: int | None, style: dict, font_path: Path, y_center: int, path: Path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if words:
        d = ImageDraw.Draw(img)
        words = [w.upper() if style["upper"] else w for w in words]
        size = style["size"]
        while True:
            font = _font(size, font_path)
            space = d.textlength(" ", font=font)
            widths = [d.textlength(w, font=font) for w in words]
            total = sum(widths) + space * (len(words) - 1)
            if total <= W - 120 or size <= 40:
                break
            size -= 4
        asc, desc = font.getmetrics()
        x = (W - total) / 2
        y = y_center - (asc + desc) / 2
        if style["box"]:
            pad = 22
            d.rounded_rectangle([x - pad, y - pad / 2, x + total + pad, y + asc + desc + pad / 2],
                                radius=18, fill=(0, 0, 0, 190))
        for i, (w, wd) in enumerate(zip(words, widths)):
            col = style["hi"] if i == hi else style["fill"]
            d.text((x, y), w, font=font, fill=col, stroke_width=style["stroke"], stroke_fill="#000000")
            x += wd + space
    img.save(path)


def draw_hook(text: str, font_path: Path, y_center: int, path: Path):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    text = text.upper()
    size = 70
    lines = [text]
    while size >= 40:
        font = _font(size, font_path)
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if d.textlength(trial, font=font) <= W - 160:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
        if len(lines) <= 2:
            break
        size -= 6
    asc, desc = font.getmetrics()
    lh = asc + desc + 8
    top = y_center - lh * len(lines) / 2
    wmax = max(d.textlength(line, font=font) for line in lines)
    d.rounded_rectangle([(W - wmax) / 2 - 30, top - 18, (W + wmax) / 2 + 30, top + lh * len(lines) + 10],
                        radius=20, fill=(255, 255, 255, 240))
    for i, line in enumerate(lines):
        d.text(((W - d.textlength(line, font=font)) / 2, top + i * lh), line, font=font, fill="#000000")
    img.save(path)


# ---------------------------------------------------------------- commands

def work_dir(p: str) -> Path:
    w = Path(p)
    if not (w / "state.json").exists():
        die(f"{w} has no state.json; run `prepare` first")
    return w


def cmd_doctor(_args):
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        log(f"{'ok ' if found else 'MISSING'} {tool} {found or ''}")
        ok &= bool(found)
    import importlib.util
    for mod in ("yt_dlp", "numpy", "PIL", "cv2", "faster_whisper"):
        found = importlib.util.find_spec(mod) is not None
        log(f"{'ok ' if found else 'MISSING'} {mod}")
        ok &= found
    log(f"{'ok ' if FONT.exists() else 'MISSING'} font {FONT}")
    sys.exit(0 if ok else 1)


def cmd_prepare(args):
    need("ffmpeg")
    src = args.source
    if is_url(src):
        log("reading video metadata ...")
        info = ydl({}).extract_info(src, download=False)
        vid = re.sub(r"[^\w-]", "_", str(info["id"]))
        work = Path(args.work or Path("clips") / vid)
        work.mkdir(parents=True, exist_ok=True)
        state = {"url": info.get("webpage_url") or src, "id": vid, "title": info.get("title", ""),
                 "uploader": info.get("uploader") or info.get("channel") or "",
                 "duration": float(info.get("duration") or 0), "heatmap": info.get("heatmap") or [],
                 "chapters": info.get("chapters") or []}
        if not list(work.glob("audio.*")):
            log("downloading audio only ...")
            ydl({"format": "bestaudio[ext=m4a]/bestaudio/worstvideo+bestaudio/best",
                 "outtmpl": str(work / "audio.%(ext)s")}).download([src])
        audio = next(p for p in work.glob("audio.*") if p.suffix != ".part")
        state["audio"] = audio.name
        if not args.chat and not args.no_chat and "live_chat" in (info.get("subtitles") or {}):
            if not (work / "chat.live_chat.json").exists():
                log("downloading chat replay (long streams take a few minutes) ...")
                ydl({"skip_download": True, "writesubtitles": True, "subtitleslangs": ["live_chat"],
                     "outtmpl": str(work / "chat")}).download([src])
            args.chat = str(work / "chat.live_chat.json")
    else:
        path = Path(src).resolve()
        if not path.exists():
            die(f"{path} not found")
        work = Path(args.work or Path("clips") / re.sub(r"[^\w-]", "_", path.stem))
        work.mkdir(parents=True, exist_ok=True)
        pr = ffprobe(str(path))
        state = {"path": str(path), "id": path.stem, "title": path.stem, "uploader": args.uploader or "",
                 "duration": float(pr["format"].get("duration") or 0), "heatmap": [], "chapters": [],
                 "audio": str(path)}
    if args.uploader:
        state["uploader"] = args.uploader
    audio_path = state["audio"] if Path(state["audio"]).is_absolute() else str(work / state["audio"])
    log("measuring loudness per second ...")
    loud = loudness_per_second(audio_path)
    np.save(work / "loudness.npy", loud)
    if not state["duration"]:
        state["duration"] = float(len(loud))
    nchat = 0
    if args.chat and Path(args.chat).exists():
        msgs = parse_chat(Path(args.chat))
        save_json(work / "chat.json", msgs)
        nchat = len(msgs)
    save_json(work / "state.json", state)
    print(json.dumps({"work": str(work), "title": state["title"], "uploader": state["uploader"],
                      "duration": fmt_ts(state["duration"]), "chat_messages": nchat,
                      "heatmap": bool(state["heatmap"]), "loudness_seconds": len(loud)}, indent=1))


def cmd_signals(args):
    work = work_dir(args.work)
    state = load_json(work / "state.json")
    loud = np.load(work / "loudness.npy")
    n = int(max(len(loud), state["duration"]))
    loud = np.pad(loud, (0, n - len(loud)), constant_values=float(np.median(loud)) if len(loud) else -60)
    loud_s = moving_mean(loud, 3)
    loud_x = loud_s - rolling_median(loud_s, 300)
    msgs = [tuple(m) for m in load_json(work / "chat.json", [])]
    chat = None
    if msgs:
        c = moving_mean(chat_series(msgs, n), 10)
        lag = args.chat_lag
        chat = np.concatenate([c[lag:], np.zeros(lag, np.float32)])  # chat reacts ~lag s after the moment
    heat = heat_series(state.get("heatmap"), n)
    score = combine({"chat": (chat, args.w_chat), "loud": (loud_x, args.w_loud), "heat": (heat, args.w_heat)})
    peaks = pick_peaks(score, args.top, args.gap, args.skip_start, 20)
    z = {k: robust_z(v) for k, v in (("chat", chat), ("loud", loud_x), ("heat", heat)) if v is not None}
    cands = []
    for i, p in enumerate(peaks, 1):
        a, b = max(0, p - args.pre), min(n, p + args.post)
        cands.append({"id": f"c{i:02d}", "peak": p, "start": a, "end": b, "score": round(float(score[p]), 2),
                      "signals": {k: round(float(v[p]), 2) for k, v in z.items()},
                      "chat_top": top_messages(msgs, p - 5, p + args.chat_lag + 15) if msgs else []})
    save_json(work / "candidates.json", cands)
    print(json.dumps({"signals": sorted(z), "candidates": [
        {"id": c["id"], "at": fmt_ts(c["peak"]), "score": c["score"]} for c in cands]}, indent=1))


def cmd_transcribe(args):
    need("ffmpeg")
    work = work_dir(args.work)
    state = load_json(work / "state.json")
    cands = load_json(work / "candidates.json") or die("run `signals` first")
    audio = state["audio"] if Path(state["audio"]).is_absolute() else str(work / state["audio"])
    tdir = work / "transcripts"
    tdir.mkdir(exist_ok=True)
    model = None
    for c in cands:
        out = tdir / f"{c['id']}.json"
        if out.exists():
            continue
        if model is None:
            from faster_whisper import WhisperModel
            log(f"loading whisper model {args.model} ...")
            model = WhisperModel(args.model, device="auto", compute_type="int8", cpu_threads=os.cpu_count() or 4)
        # decode with ffmpeg ourselves: faster-whisper's own decoder breaks on some PyAV versions
        pcm = run(["ffmpeg", "-v", "error", "-ss", str(c["start"]), "-t", str(c["end"] - c["start"]), "-i", audio,
                   "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
        segs, _ = model.transcribe(np.frombuffer(pcm, np.float32), word_timestamps=True, vad_filter=True,
                                   language=args.lang)
        segments, words = [], []
        for s in segs:
            segments.append({"s": round(c["start"] + s.start, 2), "e": round(c["start"] + s.end, 2),
                             "text": s.text.strip()})
            for w in s.words or []:
                words.append({"w": w.word.strip(), "s": round(c["start"] + w.start, 2),
                              "e": round(c["start"] + w.end, 2)})
        save_json(out, {"id": c["id"], "start": c["start"], "end": c["end"], "segments": segments, "words": words})
        log(f"{c['id']} {fmt_ts(c['start'])} transcribed ({len(words)} words)")
    write_candidates_md(work, state, cands)
    print(str(work / "candidates.md"))


def write_candidates_md(work: Path, state: dict, cands: list[dict]):
    lines = [f"# Candidates: {state['title']}", "",
             f"Source: {state.get('url') or state.get('path')}  ",
             f"Streamer: {state.get('uploader') or 'unknown'} · length {fmt_ts(state['duration'])}", "",
             "Times are absolute seconds in the VOD. Pick with references/rubric.md and write picks.json.", ""]
    for c in cands:
        tr = load_json(work / "transcripts" / f"{c['id']}.json", {"segments": []})
        sig = ", ".join(f"{k} {v:+.1f}" for k, v in c["signals"].items())
        lines += [f"## {c['id']} · {fmt_ts(c['start'])}-{fmt_ts(c['end'])} ({c['start']}-{c['end']}s) "
                  f"· peak {fmt_ts(c['peak'])} · score {c['score']} ({sig})", ""]
        if c.get("chat_top"):
            lines.append("Chat: " + ", ".join(f"\"{m}\" x{k}" for m, k in c["chat_top"]))
            lines.append("")
        for s in tr["segments"]:
            lines.append(f"- [{s['s']:.1f}] {s['text']}")
        lines.append("")
    (work / "candidates.md").write_text("\n".join(lines), encoding="utf-8")


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


def load_campaign(path: str | None) -> dict:
    if not path:
        return {}
    c = json.loads(Path(path).read_text(encoding="utf-8"))
    c.pop("_comment", None)
    return c


def post_text(pick: dict, state: dict, campaign: dict) -> str:
    tags = list(dict.fromkeys(campaign.get("required_hashtags", []) + pick.get("hashtags", ["#gta6"])))
    credit = f"Streamer: {state.get('uploader') or 'unknown'} · {state.get('url') or state.get('path')}"
    caption = pick.get("caption") or pick["hook"]
    return "\n".join([f"# {pick['title']}", "", caption, "", " ".join(tags + campaign.get("required_mentions", [])),
                      "", credit, ""])


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
    transcripts = [load_json(p) for p in sorted((work / "transcripts").glob("*.json"))] \
        if (work / "transcripts").exists() else []
    ins, vi, ai, (fw, fh) = stream_inputs(state)
    vsrc, vhdr = ins[vi]

    cam = None
    if args.layout in ("auto", "cam-top"):
        if args.cam == "none":
            cam = None
        elif args.cam != "auto":
            cam = tuple(int(v) for v in args.cam.split(","))
        else:
            cache = work / "facecam.json"
            if cache.exists():
                cam = load_json(cache)["box"]
            else:
                times = [(p["start"] + p["end"]) / 2 for p in picks[:8]]
                times += [p["start"] + 2 for p in picks[:8]]
                face = detect_cam(vsrc, vhdr, times, fw)
                cam = cam_box(face, fw, fh) if face else None
                save_json(cache, {"box": cam, "face": face, "source_size": [fw, fh]})
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
        cap_y = {"cam-top": 1330, "blur": 1480, "center": 1400}[layout]
        hook_y = CAM_H if layout == "cam-top" else 300

        cap_list = None
        rel = [{"w": w["w"], "s": w["s"] - s, "e": w["e"] - s} for w in words if s <= w["s"] < e]
        if rel and not args.no_captions:
            chunks = chunk_words(rel)
            tl = caption_timeline(chunks, dur)
            blank = rdir / "blank.png"
            draw_words([], None, style, font, cap_y, blank)
            lines = ["ffconcat version 1.0"]
            for k, (t0, t1, ci, wi) in enumerate(tl):
                if ci is None:
                    name = "blank.png"
                else:
                    name = f"c{ci:03d}_{wi}.png"
                    if not (rdir / name).exists():
                        draw_words([w["w"] for w in chunks[ci]], wi if style["hi"] != style["fill"] else None,
                                   style, font, cap_y, rdir / name)
                lines += [f"file '{name}'", f"duration {max(t1 - t0, 0.034):.3f}"]
            lines.append("file 'blank.png'")
            cap_list = rdir / "captions.ffconcat"
            cap_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
        hook_png = rdir / "hook.png"
        draw_hook(p["hook"], font, hook_y, hook_png)

        cmd = ["ffmpeg", "-v", "error", "-y"]
        for src, hdr in ins:
            cmd += input_args(src, hdr, s, dur + 0.5)
        idx = len(ins)
        if layout == "cam-top":
            cx, cy, cw, chh = cam
            gw = int(fh * W / (H - CAM_H)) // 2 * 2
            gx = (fw - gw) // 2
            fg = (f"[{vi}:v]split=2[a][b];[a]crop={cw}:{chh}:{cx}:{cy},scale={W}:{CAM_H},setsar=1[cam];"
                  f"[b]crop={min(gw, fw)}:{fh}:{gx}:0,scale={W}:{H - CAM_H},setsar=1[game];"
                  f"[cam][game]vstack,fps={FPS}[base]")
        elif layout == "blur":
            fg = (f"[{vi}:v]split=2[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"boxblur=24:2,eq=brightness=-0.08[bg];[b]scale={W}:-2[fgv];"
                  f"[bg][fgv]overlay=0:(H-h)/2,setsar=1,fps={FPS}[base]")
        else:
            fg = f"[{vi}:v]scale=-2:{H},crop={W}:{H},setsar=1,fps={FPS}[base]"
        last = "base"
        if cap_list:
            cmd += ["-f", "concat", "-safe", "0", "-i", str(cap_list)]
            fg += f";[{idx}:v]format=rgba[cap];[{last}][cap]overlay=0:0:eof_action=repeat[v1]"
            last, idx = "v1", idx + 1
        cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(hook_png)]
        fg += f";[{idx}:v]format=rgba[hk];[{last}][hk]overlay=0:0:enable='lt(t,{args.hook_seconds})'[v]"
        fg += f";[{ai}:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]"
        out = out_dir / f"{p['id']}.mp4"
        cmd += ["-filter_complex", fg, "-map", "[v]", "-map", "[aout]", "-t", f"{dur:.3f}",
                "-c:v", "libx264", "-preset", args.preset, "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
        log(f"rendering {p['id']} {fmt_ts(s)}-{fmt_ts(e)} ({dur:.1f}s, {layout}) ...")
        run(cmd)
        sheet = out_dir / f"{p['id']}_sheet.jpg"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-vf", f"fps=12/{dur:.3f},scale=270:-2,tile=4x3",
             "-frames:v", "1", str(sheet)])
        (out_dir / f"{p['id']}.md").write_text(post_text(p, state, campaign), encoding="utf-8")
        save_json(out_dir / f"{p['id']}.json", {**p, "cut_start": s, "cut_end": e, "layout": layout})
        print(json.dumps({"id": p["id"], "video": str(out), "sheet": str(sheet), "seconds": round(dur, 1)}))


def cmd_check(args):
    need("ffprobe")
    work = work_dir(args.work)
    state = load_json(work / "state.json")
    campaign = load_campaign(args.campaign)
    transcripts = [load_json(p) for p in sorted((work / "transcripts").glob("*.json"))] \
        if (work / "transcripts").exists() else []
    rows, fails = [], 0
    for meta_path in sorted((work / "out").glob("*.json")):
        pick = load_json(meta_path)
        video = meta_path.with_suffix(".mp4")
        if not video.exists():
            continue
        pr = ffprobe(str(video))
        v = next((st for st in pr["streams"] if st["codec_type"] == "video"), {})
        meta = {"width": v.get("width"), "height": v.get("height"),
                "has_audio": any(st["codec_type"] == "audio" for st in pr["streams"]),
                "duration": float(pr["format"].get("duration") or 0), "lufs": measure_lufs(video),
                "source_url": state.get("url") or state.get("path")}
        post = meta_path.with_suffix(".md").read_text(encoding="utf-8") if meta_path.with_suffix(".md").exists() else ""
        said = " ".join(w["w"] for w in words_in(transcripts, pick["cut_start"], pick["cut_end"]))
        res = run_checks(meta, pick, post, said, campaign, state.get("uploader", ""))
        fails += sum(1 for r in res if r[0] == "FAIL")
        rows.append((pick["id"], pick["title"], res))
    if not rows:
        die("no rendered clips in out/; run `render` first")
    lines = ["# Clip check", "", f"Campaign: {campaign.get('name', 'none (generic limits)')}", ""]
    for cid, title, res in rows:
        worst = "FAIL" if any(r[0] == "FAIL" for r in res) else "WARN" if any(r[0] == "WARN" for r in res) else "PASS"
        lines += [f"## {cid} · {worst} · {title}", "", "| result | rule | detail |", "|---|---|---|"]
        lines += [f"| {lvl} | {rule} | {detail} |" for lvl, rule, detail in res]
        lines.append("")
    (work / "check.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    sys.exit(1 if fails else 0)


def main(argv=None):
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
    p.add_argument("--chat-lag", type=int, default=8)
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

    args = ap.parse_args(argv)
    {"doctor": cmd_doctor, "prepare": cmd_prepare, "signals": cmd_signals, "transcribe": cmd_transcribe,
     "render": cmd_render, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    main()
