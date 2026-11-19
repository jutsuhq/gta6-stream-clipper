"""Find the streamer's facecam with YuNet and turn it into a crop box for the top of the frame."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .common import CACHE, CAM_H, W, load_json, log, save_json
from .media import grab_frame

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")


def cluster_faces(dets: list[list[tuple]], fw: int, min_share: float = 0.5):
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
    x, y, w, h = face
    cw = min(fw, int(w * scale))
    ch = min(fh, int(cw / aspect))
    cw = int(ch * aspect)
    cx, cy = x + w / 2, y + h / 2
    bx = int(min(max(0, cx - cw / 2), fw - cw))
    by = int(min(max(0, cy - ch * 0.45), fh - ch))
    return bx - bx % 2, by - by % 2, cw - cw % 2, ch - ch % 2


def yunet_model() -> Path:
    path = CACHE / "face_detection_yunet_2023mar.onnx"
    if not path.exists():
        import urllib.request
        CACHE.mkdir(parents=True, exist_ok=True)
        log(f"downloading face model -> {path}")
        urllib.request.urlretrieve(YUNET_URL, path)
    return path


def detect_faces(src: str, headers: dict, times: list[float], fw: int):
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
        dets.append([(*map(float, f[:4]), float(f[-1])) for f in (faces if faces is not None else [])
                     if f[2] < w * 0.25])
    return cluster_faces(dets, fw) if dets else None


def resolve_cam(arg: str, work: Path, picks: list[dict], src: str, headers: dict, size: tuple[int, int]):
    if arg == "none":
        return None
    if arg != "auto":
        return tuple(int(v) for v in arg.split(","))
    cache = work / "facecam.json"
    if cache.exists():
        return load_json(cache)["box"]
    fw, fh = size
    times = [(p["start"] + p["end"]) / 2 for p in picks[:8]] + [p["start"] + 2 for p in picks[:8]]
    face = detect_faces(src, headers, times, fw)
    cam = cam_box(face, fw, fh) if face else None
    save_json(cache, {"box": cam, "face": face, "source_size": [fw, fh]})
    return cam
