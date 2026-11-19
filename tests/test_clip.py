import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "gta6-clipper" / "scripts"))
from clipper import captions, chat, checks, common, facecam, signals  # noqa: E402


def test_parse_ts():
    assert common.parse_ts("1:02:03.5") == 3723.5
    assert common.parse_ts("62:03") == 3723
    assert common.parse_ts(12) == 12.0
    with pytest.raises(ValueError):
        common.parse_ts("1:2:3:4")


def test_msg_weight_counts_hype_and_caps():
    assert chat.msg_weight("hello there") == 1.0
    assert chat.msg_weight("KEKW") == 3.0
    assert chat.msg_weight("clip it KEKW LMAO OMEGALUL") == 4.0  # capped at +3


def test_parse_chat_formats(tmp_path):
    ytdlp = tmp_path / "a.live_chat.json"
    line = {"replayChatItemAction": {"videoOffsetTimeMsec": "61500", "actions": [{"addChatItemAction": {"item": {
        "liveChatTextMessageRenderer": {"message": {"runs": [{"text": "no way "},
                                                             {"emoji": {"shortcuts": [":skull:"]}}]}}}}}]}}
    ytdlp.write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert chat.parse_chat(ytdlp) == [(61.5, "no way :skull:")]

    lst = tmp_path / "b.json"
    lst.write_text(json.dumps([{"t": "1:00", "text": "W"}, [5, "first"]]), encoding="utf-8")
    assert chat.parse_chat(lst) == [(5.0, "first"), (60.0, "W")]

    txt = tmp_path / "c.txt"
    txt.write_text("[0:01:05] KEKW\n12\tclip it\nnot a line\n", encoding="utf-8")
    assert chat.parse_chat(txt) == [(12.0, "clip it"), (65.0, "KEKW")]


def test_combine_renormalises_missing_series():
    a = np.array([0, 0, 0, 10, 0, 0], np.float32)
    s = signals.combine({"chat": (a, 0.5), "loud": (None, 0.3), "heat": (np.zeros(6), 0.2)})
    assert int(np.argmax(s)) == 3
    with pytest.raises(ValueError):
        signals.combine({"chat": (None, 1.0)})


def test_pick_peaks_respects_gap_and_edges():
    score = np.zeros(1000, np.float32)
    score[[50, 300, 320, 700, 990]] = [9, 8, 7, 6, 10]
    assert signals.pick_peaks(score, 5, 90, skip_start=100, skip_end=20) == [300, 700]


def test_snap_moves_to_word_edges_and_fits_limits():
    words = [{"w": "go", "s": 10.4, "e": 10.9}, {"w": "now", "s": 30.0, "e": 30.6}]
    assert captions.snap(10.0, 31.0, words, 5, 60) == (10.25, 30.85)
    s, e = captions.snap(0, 200, [], 5, 60)
    assert (s, e) == (0.0, 60.0)
    s, e = captions.snap(10, 12, [], 15, 60)
    assert e - s == 15


def test_chunk_words_breaks_on_size_gap_and_punctuation():
    ws = [{"w": w, "s": i, "e": i + 0.5} for i, w in enumerate(["oh", "no,", "he", "is", "so", "gone"])]
    ws[5]["s"], ws[5]["e"] = 10, 10.5
    chunks = [[w["w"] for w in c] for c in captions.chunk_words(ws)]
    assert chunks == [["oh", "no,"], ["he", "is", "so"], ["gone"]]


def test_caption_timeline_covers_clip_without_overlap():
    ws = [{"w": "a", "s": 1.0, "e": 1.3}, {"w": "b", "s": 1.4, "e": 1.8}, {"w": "c", "s": 5.0, "e": 5.5}]
    tl = captions.caption_timeline(captions.chunk_words(ws, max_words=2), 8.0)
    assert tl[0][0] == 0 and tl[-1][1] == 8.0
    for (a0, a1, *_), (b0, *_x) in zip(tl, tl[1:]):
        assert a1 == pytest.approx(b0) and a1 > a0


def test_cluster_faces_finds_steady_facecam():
    cam = (1700, 900, 80, 90, 0.9)
    frames = [[cam, (900 + i * 50, 400, 60, 60, 0.9)] for i in range(6)]
    assert facecam.cluster_faces(frames, 1920) == (1700, 900, 80, 90)
    assert facecam.cluster_faces([[(300 * i, 100, 50, 50, 0.9)] for i in range(6)], 1920) is None


def test_cam_box_keeps_aspect_and_frame():
    x, y, w, h = facecam.cam_box((1800, 1000, 80, 90), 1920, 1080)
    assert x + w <= 1920 and y + h <= 1080 and x >= 0 and y >= 0
    assert abs(w / h - common.W / common.CAM_H) < 0.02


def test_run_checks_flags_rockstar_and_campaign_rules():
    meta = {"width": 1080, "height": 1920, "has_audio": True, "duration": 30, "lufs": -14.2,
            "source_url": "https://example.com/v"}
    camp = {"min_seconds": 15, "max_seconds": 60, "paid": True, "required_hashtags": ["#gta6"],
            "banned_words": ["leak"], "allow_spoilers": False}
    pick = {"title": "Big chase", "kind": "cutscene", "spoiler": True}
    res = {r[1]: r[0] for r in checks.run_checks(meta, pick, "#gta6 https://example.com/v", "a leak here", camp, "x")}
    assert res["rockstar: cutscene"] == "FAIL"
    assert res["spoiler"] == "FAIL"
    assert res["rockstar: commercial use"] == "WARN"
    assert res["banned words"] == "FAIL"
    assert res["required tags"] == "PASS"
    assert res["format"] == res["length"] == res["credit"] == "PASS"

    ok = checks.run_checks(meta, {"title": "t", "kind": "chase"}, "https://example.com/v", "", {}, "x")
    assert not [r for r in ok if r[0] != "PASS"]
