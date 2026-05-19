#!/usr/bin/env python3
"""visualize_keypoints.py — 한 영상의 frame 별 룰 metric 시계열을 추출.

DS pipeline 없이 ultralytics 로 직접 추론 (sanity check 와 동일한 경로).
도메인(URFDD) 에 맞는 룰 임계를 설계하기 위한 데이터 수집.

출력 CSV columns:
    t, n, conf, bbox_x1, bbox_y1, bbox_x2, bbox_y2,
    tilt_deg, head_hip_compression, bbox_aspect,
    head_x, head_y, head_v,
    sh_mid_x, sh_mid_y, hp_mid_x, hp_mid_y

사용
    python visualize_keypoints.py --video ~/eval/urfdd/videos/fall-01.mp4 \
                                  --gt-start 3.767 --gt-end 5.333 \
                                  --out /tmp/fall-01_kp.csv
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Optional, Tuple

import cv2
from ultralytics import YOLO


# COCO keypoint
KP_NOSE = 0
KP_L_SHOULDER, KP_R_SHOULDER = 5, 6
KP_L_HIP, KP_R_HIP = 11, 12
KP_VIS_THR = 0.30


def _midpoint(kxy, kv, idx_a: int, idx_b: int) -> Optional[Tuple[float, float]]:
    if kv[idx_a] < KP_VIS_THR or kv[idx_b] < KP_VIS_THR:
        return None
    return ((kxy[idx_a, 0] + kxy[idx_b, 0]) / 2.0,
            (kxy[idx_a, 1] + kxy[idx_b, 1]) / 2.0)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True, type=Path)
    p.add_argument("--onnx",  type=Path,
                    default=Path.home() / "마음돌봄" / "release" / "vision"
                            / "models" / "pose_estimator" / "yolov8n_pose.onnx")
    p.add_argument("--gt-start", type=float, default=None)
    p.add_argument("--gt-end",   type=float, default=None)
    p.add_argument("--conf",     type=float, default=0.10)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"[load] {args.video.name}  fps={fps:.1f}  frames={n_frames}  "
          f"duration={n_frames/fps:.2f}s")
    model = YOLO(str(args.onnx), task="pose")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    f = args.out.open("w", newline="")
    w = csv.writer(f)
    w.writerow([
        "t", "n", "conf", "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2",
        "tilt_deg", "head_hip_compression", "bbox_aspect",
        "head_x", "head_y", "head_v",
        "sh_mid_x", "sh_mid_y", "hp_mid_x", "hp_mid_y",
    ])

    frame_idx = 0
    fallen_summary = []   # GT 윈도우 내 row 들
    standing_summary = [] # GT 밖

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        t = frame_idx / fps
        frame_idx += 1

        res = model.predict(frame, imgsz=640, conf=args.conf, verbose=False)[0]
        boxes = res.boxes
        if boxes is None or len(boxes) == 0:
            w.writerow([round(t, 3), 0] + [""] * 15)
            continue

        # 가장 큰 bbox 1개
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        areas = (xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])
        i = int(areas.argmax())
        bbox = xyxy[i].tolist()
        cf = float(confs[i])

        kxy = res.keypoints.xy[i].cpu().numpy()        # (17, 2)
        if res.keypoints.conf is not None:
            kv = res.keypoints.conf[i].cpu().numpy()
        else:
            kv = [1.0] * 17

        sh = _midpoint(kxy, kv, KP_L_SHOULDER, KP_R_SHOULDER)
        hp = _midpoint(kxy, kv, KP_L_HIP,      KP_R_HIP)
        nose_v = float(kv[KP_NOSE])
        nose_x = float(kxy[KP_NOSE, 0])
        nose_y = float(kxy[KP_NOSE, 1])

        # tilt
        tilt = None
        if sh is not None and hp is not None:
            dx = hp[0] - sh[0]; dy = hp[1] - sh[1]
            if abs(dx) + abs(dy) > 1e-6:
                tilt = math.degrees(math.atan2(abs(dx), abs(dy)))

        # head_hip y compression
        comp = None
        if hp is not None and nose_v >= KP_VIS_THR:
            bbox_h = max(1.0, bbox[3] - bbox[1])
            comp = (hp[1] - nose_y) / bbox_h

        # bbox aspect
        w_ = max(1.0, bbox[2] - bbox[0])
        h_ = max(1.0, bbox[3] - bbox[1])
        aspect = w_ / h_

        row = [
            round(t, 3), 1, round(cf, 3),
            round(bbox[0], 1), round(bbox[1], 1),
            round(bbox[2], 1), round(bbox[3], 1),
            round(tilt, 1) if tilt is not None else "",
            round(comp, 3) if comp is not None else "",
            round(aspect, 3),
            round(nose_x, 1) if nose_v >= KP_VIS_THR else "",
            round(nose_y, 1) if nose_v >= KP_VIS_THR else "",
            round(nose_v, 2),
            round(sh[0], 1) if sh else "", round(sh[1], 1) if sh else "",
            round(hp[0], 1) if hp else "", round(hp[1], 1) if hp else "",
        ]
        w.writerow(row)

        # GT 윈도우 분류 — GT 인자 없으면 모든 frame 을 standing 으로
        in_gt = (args.gt_start is not None and args.gt_end is not None
                 and args.gt_start <= t <= args.gt_end)
        target = fallen_summary if in_gt else standing_summary
        target.append({
            "t": t, "tilt": tilt, "comp": comp, "aspect": aspect,
            "nose_y": nose_y if nose_v >= KP_VIS_THR else None,
        })

    f.close()
    cap.release()

    print(f"[saved] {args.out}")

    # 요약 — GT 윈도우 안 vs 밖
    def _summary(rows, name):
        if not rows: return
        tilts   = [r["tilt"]   for r in rows if r["tilt"]   is not None]
        comps   = [r["comp"]   for r in rows if r["comp"]   is not None]
        aspects = [r["aspect"] for r in rows]
        print(f"\n[{name}]  n={len(rows)}")
        if tilts:
            print(f"  tilt   min={min(tilts):5.1f}°  max={max(tilts):5.1f}°  "
                  f"mean={sum(tilts)/len(tilts):5.1f}°")
        if comps:
            print(f"  comp   min={min(comps):.3f}  max={max(comps):.3f}  "
                  f"mean={sum(comps)/len(comps):.3f}")
        print(f"  aspect min={min(aspects):.3f}  max={max(aspects):.3f}  "
              f"mean={sum(aspects)/len(aspects):.3f}")

    _summary(fallen_summary,   "GT FALL window")
    _summary(standing_summary, "outside GT (서있음/걷기)")


if __name__ == "__main__":
    main()
