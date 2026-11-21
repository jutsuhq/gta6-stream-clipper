"""Step 3: transcribe only the candidate windows and write candidates.md for Claude to pick from."""
from __future__ import annotations

import os
from pathlib import Path

from .common import audio_path, die, fmt_ts, load_json, log, need, save_json, work_dir
from .media import read_pcm


def load_model(name: str):
    from faster_whisper import WhisperModel
    log(f"loading whisper model {name} ...")
    return WhisperModel(name, device="auto", compute_type="int8", cpu_threads=os.cpu_count() or 4)


def transcribe_window(model, audio: str, c: dict, lang: str | None) -> dict:
    t0 = c["start"]
    segs, _ = model.transcribe(read_pcm(audio, t0, c["end"] - t0), word_timestamps=True, vad_filter=True,
                               language=lang)
    segments, words = [], []
    for s in segs:
        segments.append({"s": round(t0 + s.start, 2), "e": round(t0 + s.end, 2), "text": s.text.strip()})
        words += [{"w": w.word.strip(), "s": round(t0 + w.start, 2), "e": round(t0 + w.end, 2)}
                  for w in s.words or []]
    return {"id": c["id"], "start": t0, "end": c["end"], "segments": segments, "words": words}


def cmd_transcribe(args):
    need("ffmpeg")
    work = work_dir(args.work)
    state = load_json(work / "state.json")
    cands = load_json(work / "candidates.json") or die("run `signals` first")
    audio = audio_path(work, state)
    tdir = work / "transcripts"
    tdir.mkdir(exist_ok=True)
    model = None
    for c in cands:
        out = tdir / f"{c['id']}.json"
        if out.exists():
            continue
        model = model or load_model(args.model)
        tr = transcribe_window(model, audio, c, args.lang)
        save_json(out, tr)
        log(f"{c['id']} {fmt_ts(c['start'])} transcribed ({len(tr['words'])} words)")
    write_candidates_md(work, state, cands)
    print(str(work / "candidates.md"))


def write_candidates_md(work: Path, state: dict, cands: list[dict]):
    lines = [f"# Candidates: {state['title']}", "",
             f"Source: {state.get('url') or state.get('path')}  ",
             f"Streamer: {state.get('uploader') or 'unknown'} · length {fmt_ts(state['duration'])}", "",
             "Times are absolute seconds in the VOD. Pick with references/rubric.md and write picks.json.", ""]
    if not load_json(work / "chat.json", []):
        lines += ["No chat replay: peaks come from loudness and heatmap only, so check each one by ear.", ""]
    for c in cands:
        tr = load_json(work / "transcripts" / f"{c['id']}.json", {"segments": []})
        sig = ", ".join(f"{k} {v:+.1f}" for k, v in c["signals"].items())
        lines += [f"## {c['id']} · {fmt_ts(c['start'])}-{fmt_ts(c['end'])} ({c['start']}-{c['end']}s) "
                  f"· peak {fmt_ts(c['peak'])} · score {c['score']} ({sig})", ""]
        if c.get("chat_top"):
            lines += ["Chat: " + ", ".join(f"\"{m}\" x{k}" for m, k in c["chat_top"]), ""]
        lines += [f"- [{s['s']:.1f}] {s['text']}" for s in tr["segments"]]
        lines.append("")
    (work / "candidates.md").write_text("\n".join(lines), encoding="utf-8")
