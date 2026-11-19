"""Chat replay parsing and per-second hype weighting."""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import numpy as np

from .common import parse_ts

HYPE = {
    "kekw": 2, "lul": 1.5, "lol": 1, "lmao": 2, "lmfao": 2, "omegalul": 2.5, "icant": 2,
    "pog": 1.5, "poggers": 1.5, "pogchamp": 1.5, "pogu": 1.5, "w": 1, "dub": 1,
    "wtf": 2, "omg": 1.5, "holy": 1.5, "monkas": 1.5, "rip": 1.5, "f": 1, "l": 1,
    "wasted": 2, "busted": 2, "clip": 3, "clipped": 3, "ez": 0.5,
    "😂": 1.5, "🤣": 1.5, "💀": 1.5, "😭": 1.5, "🔥": 1, "😱": 1.5,
}
HYPE_PHRASES = {"clip it": 3, "clip that": 3, "no way": 2, "?????": 1.5, "!!!!": 1}
TOKEN_RE = re.compile(r"[\w']+|[^\w\s]", re.UNICODE)
LINE_RE = re.compile(r"\s*\[?([\d:.]+)\]?[\t,; ]+(.*)")

Message = tuple[float, str]


def msg_weight(text: str) -> float:
    low = text.lower()
    extra = sum(w for t in TOKEN_RE.findall(low) if (w := HYPE.get(t)))
    extra += sum(w for p, w in HYPE_PHRASES.items() if p in low)
    return 1.0 + min(3.0, extra)


def parse_chat(path: Path) -> list[Message]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    head = raw.lstrip()[:1]
    data = _try_json(raw) if head == "[" else None
    if data is not None:
        msgs = _from_json_list(data)
    elif head == "{":
        msgs = _from_ytdlp_jsonl(raw)
    else:
        msgs = _from_text_lines(raw)
    return sorted(msgs, key=lambda m: m[0])


def chat_series(msgs: list[Message], n: int) -> np.ndarray:
    s = np.zeros(n, dtype=np.float32)
    for t, text in msgs:
        if 0 <= int(t) < n:
            s[int(t)] += msg_weight(text)
    return s


def top_messages(msgs: list[Message], a: float, b: float, k: int = 6) -> list[tuple[str, int]]:
    c = collections.Counter()
    for t, text in msgs:
        if a <= t <= b:
            norm = re.sub(r"(.)\1{2,}", r"\1\1", text.strip().lower())[:40]
            if norm:
                c[norm] += 1
    return c.most_common(k)


def _try_json(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _from_json_list(data: list) -> list[Message]:
    msgs = []
    for m in data:
        if isinstance(m, dict):
            t = m.get("t", m.get("time", m.get("offset", m.get("time_in_seconds"))))
            text = m.get("text", m.get("message", ""))
        else:
            t, text = m[0], m[1]
        if t is not None:
            msgs.append((parse_ts(t), str(text)))
    return msgs


def _from_ytdlp_jsonl(raw: str) -> list[Message]:
    msgs = []
    for line in filter(None, map(str.strip, raw.splitlines())):
        obj = _try_json(line)
        if obj and (got := _ytdlp_message(obj)):
            msgs.append(got)
    return msgs


def _from_text_lines(raw: str) -> list[Message]:
    msgs = []
    for line in raw.splitlines():
        if m := LINE_RE.match(line):
            try:
                msgs.append((parse_ts(m.group(1)), m.group(2)))
            except ValueError:
                pass
    return msgs


def _ytdlp_message(obj: dict) -> Message | None:
    act = obj.get("replayChatItemAction") or {}
    try:
        t = int(act.get("videoOffsetTimeMsec", obj.get("videoOffsetTimeMsec"))) / 1000
    except (TypeError, ValueError):
        return None
    for a in act.get("actions", []):
        item = (a.get("addChatItemAction") or {}).get("item") or {}
        if r := item.get("liveChatTextMessageRenderer"):
            return t, "".join(_run_text(x) for x in r.get("message", {}).get("runs", []))
    return None


def _run_text(run: dict) -> str:
    if "text" in run:
        return run["text"]
    if "emoji" in run:
        shortcuts = run["emoji"].get("shortcuts") or []
        return shortcuts[0] if shortcuts else run["emoji"].get("emojiId", "")
    return ""
