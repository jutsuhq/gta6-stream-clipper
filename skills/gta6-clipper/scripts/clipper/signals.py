"""Score every second of the VOD from chat, loudness and the replay heatmap, then pick candidate windows."""
from __future__ import annotations

import numpy as np

from .chat import chat_series, top_messages
from .common import fmt_ts, load_json, print_json, save_json, work_dir


def moving_mean(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(x) == 0:
        return x.astype(np.float32)
    return np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same").astype(np.float32)


def rolling_median(x: np.ndarray, k: int, stride: int = 10) -> np.ndarray:
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


def combine(parts: dict[str, tuple[np.ndarray | None, float]]) -> np.ndarray:
    have = {k: v for k, v in parts.items() if v[0] is not None and len(v[0]) and np.ptp(v[0]) > 0}
    if not have:
        raise ValueError("no usable signal (no chat, flat audio, no heatmap)")
    total = sum(w for _, w in have.values())
    n = min(len(a) for a, _ in have.values())
    return sum(robust_z(a[:n]) * (w / total) for a, w in have.values())


def pick_peaks(score: np.ndarray, n: int, min_gap: int, skip_start: int = 0, skip_end: int = 0,
               min_score: float = 0.0) -> list[int]:
    hi = len(score) - skip_end
    picked: list[int] = []
    for i in map(int, np.argsort(-score, kind="stable")):
        if score[i] <= min_score or len(picked) >= n:
            break
        if skip_start <= i < hi and all(abs(i - p) >= min_gap for p in picked):
            picked.append(i)
    return sorted(picked)


def heat_series(heatmap, n: int) -> np.ndarray | None:
    if not heatmap:
        return None
    xs = [(h["start_time"] + h["end_time"]) / 2 for h in heatmap]
    return np.interp(np.arange(n), xs, [h["value"] for h in heatmap]).astype(np.float32)


def loudness_excess(loud: np.ndarray, n: int) -> np.ndarray:
    fill = float(np.median(loud)) if len(loud) else -60
    smooth = moving_mean(np.pad(loud, (0, n - len(loud)), constant_values=fill), 3)
    return smooth - rolling_median(smooth, 300)


def shifted_chat(msgs, n: int, lag: int) -> np.ndarray | None:
    if not msgs:
        return None
    c = moving_mean(chat_series(msgs, n), 10)
    return np.concatenate([c[lag:], np.zeros(lag, np.float32)])


def cmd_signals(args):
    work = work_dir(args.work)
    state = load_json(work / "state.json")
    loud = np.load(work / "loudness.npy")
    n = int(max(len(loud), state["duration"]))
    msgs = [tuple(m) for m in load_json(work / "chat.json", [])]
    series = {"chat": shifted_chat(msgs, n, args.chat_lag), "loud": loudness_excess(loud, n),
              "heat": heat_series(state.get("heatmap"), n)}
    weights = {"chat": args.w_chat, "loud": args.w_loud, "heat": args.w_heat}
    score = combine({k: (v, weights[k]) for k, v in series.items()})
    z = {k: robust_z(v) for k, v in series.items() if v is not None}
    cands = []
    for i, p in enumerate(pick_peaks(score, args.top, args.gap, args.skip_start, 20), 1):
        cands.append({"id": f"c{i:02d}", "peak": p, "start": max(0, p - args.pre), "end": min(n, p + args.post),
                      "score": round(float(score[p]), 2),
                      "signals": {k: round(float(v[p]), 2) for k, v in z.items()},
                      "chat_top": top_messages(msgs, p - 5, p + args.chat_lag + 15) if msgs else []})
    save_json(work / "candidates.json", cands)
    print_json({"signals": sorted(z),
                "candidates": [{"id": c["id"], "at": fmt_ts(c["peak"]), "score": c["score"]} for c in cands]})
