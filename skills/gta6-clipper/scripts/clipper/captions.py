"""Word timing and caption images: ffmpeg here has no libass, so captions are PNGs played by the concat demuxer."""
from __future__ import annotations

import re
from pathlib import Path

from .common import H, W

STYLES = {
    "bold": {"upper": True, "size": 92, "fill": "#FFFFFF", "hi": "#FFD400", "stroke": 9, "box": False},
    "box": {"upper": True, "size": 80, "fill": "#FFFFFF", "hi": "#7CFF4F", "stroke": 0, "box": True},
    "clean": {"upper": False, "size": 74, "fill": "#FFFFFF", "hi": "#FFFFFF", "stroke": 4, "box": False},
}


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
    starts = [w["s"] for w in words]
    ends = [w["e"] for w in words]
    if near := [s for s in starts if abs(s - start) <= reach]:
        start = min(near, key=lambda s: abs(s - start)) - 0.15
    if near := [e for e in ends if abs(e - end) <= reach]:
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
        if cur and _breaks(cur, text, w["s"] - cur[-1]["e"], max_words, max_chars, max_gap):
            chunks.append(cur)
            cur = []
        cur.append({**w, "w": text})
    if cur:
        chunks.append(cur)
    return chunks


def _breaks(cur: list[dict], text: str, gap: float, max_words: int, max_chars: int, max_gap: float) -> bool:
    chars = sum(len(x["w"].strip()) + 1 for x in cur) + len(text)
    return (len(cur) >= max_words or chars > max_chars or gap > max_gap
            or bool(re.search(r"[.!?,]$", cur[-1]["w"].strip())))


def caption_timeline(chunks, clip_len: float):
    tl, t = [], 0.0
    for ci, ch in enumerate(chunks):
        nxt = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else clip_len
        end = min(nxt, ch[-1]["e"] + 0.4, clip_len)
        for wi, w in enumerate(ch):
            s = max(w["s"], t)
            if s > t + 0.01:
                tl.append((t, s, None, None))
            e = max(ch[wi + 1]["s"] if wi + 1 < len(ch) else end, s + 0.05)
            tl.append((s, e, ci, wi))
            t = e
    if t < clip_len:
        tl.append((t, clip_len, None, None))
    return tl


def write_caption_track(rdir: Path, words: list[dict], dur: float, style: dict, font: Path, y: int) -> Path:
    chunks = chunk_words(words)
    highlight = style["hi"] != style["fill"]
    draw_words([], None, style, font, y, rdir / "blank.png")
    lines = ["ffconcat version 1.0"]
    for t0, t1, ci, wi in caption_timeline(chunks, dur):
        name = "blank.png" if ci is None else f"c{ci:03d}_{wi}.png"
        if not (rdir / name).exists():
            draw_words([w["w"] for w in chunks[ci]], wi if highlight else None, style, font, y, rdir / name)
        lines += [f"file '{name}'", f"duration {max(t1 - t0, 0.034):.3f}"]
    lines.append("file 'blank.png'")
    path = rdir / "captions.ffconcat"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _font(size: int, font_path: Path):
    from PIL import ImageFont
    return ImageFont.truetype(str(font_path), size)


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
    while True:
        font = _font(size, font_path)
        lines = _wrap(d, text, font, W - 160)
        if len(lines) <= 2 or size - 6 < 40:
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


def _wrap(d, text: str, font, width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=font) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]
