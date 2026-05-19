#!/usr/bin/env python3
"""eval_fall.py — Phase 4 낙상 감지기 정량 평가.

각 영상에 대해 fall_detection_node 를 file 모드로 띄우고 /vision/fall_state
토픽을 모니터링해 ground truth 와 비교한다.

ground truth CSV 형식:
    video_id,start_ts,end_ts,is_fall
    fall-01.mp4,3.2,5.0,1
    adl-01.mp4,,,0

판정 규칙 (τ = 3 s default):
    is_fall=1 인 영상에서 GT fall 구간 ±τ 안에 fall_detected=true 가 발생 → TP
    is_fall=1 인데 영상 끝까지 발생 안 함                                → FN
    is_fall=0 인데 발생                                                  → FP
    is_fall=0 이고 발생 안 함                                            → TN

지표
    Recall    = TP / (TP + FN)
    Precision = TP / (TP + FP)
    F1        = 2·P·R / (P+R)
    E2E latency = (첫 fall_detected=true 의 recv_ts) - (영상 시작 + GT.start_ts)

사용
    # 기본 평가 (default 임계)
    python eval_fall.py --videos-dir ~/eval/urfdd \
                       --gt        ~/eval/urfdd/gt.csv \
                       --output-json eval_results.json

    # 임계 1개만 변화시켜 sweep — coordinate descent 1-pass
    python eval_fall.py --videos-dir ... --gt ... \
        --sweep tilt_deg_thr:50,55,60,65,70 \
        --output-json sweep_tilt.json

전제
    - mind_care_perception 패키지가 colcon build 되어 있어 `ros2 launch
      mind_care_perception fall_detection.launch.py` 가 동작.
    - venv-ros 활성화 + /opt/ros/jazzy/setup.bash + ros2_ws/install/setup.bash
      sourced.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# rclpy 는 ROS 환경 source 후에만 import 가능 — lazy
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


# ----------------------------------------------------------------------
# Ground Truth & Result
# ----------------------------------------------------------------------
@dataclass
class GTRow:
    video_id: str
    start_ts: Optional[float]   # GT fall 시작 (초). is_fall=0 이면 None
    end_ts:   Optional[float]
    is_fall:  bool


@dataclass
class EvalRow:
    video_id: str
    is_fall: bool
    detected: bool                          # fall_detected=true 가 한 번이라도 발생
    detect_ts: Optional[float] = None       # 첫 detect 의 video-relative time
    confirmed: bool = False                 # fall_confirmed=true 도 발생했나
    classification: str = ""                # TP | FN | FP | TN
    latency_s: Optional[float] = None       # GT.start_ts → detect_ts (TP 만)
    n_messages: int = 0                     # recorder 가 받은 토픽 메시지 수
    error: Optional[str] = None


# ----------------------------------------------------------------------
# CSV 파서
# ----------------------------------------------------------------------
def load_gt(csv_path: Path) -> List[GTRow]:
    out: List[GTRow] = []
    with csv_path.open() as f:
        for r in csv.DictReader(f):
            is_fall = bool(int(r["is_fall"]))
            start = float(r["start_ts"]) if r.get("start_ts") else None
            end   = float(r["end_ts"])   if r.get("end_ts")   else None
            out.append(GTRow(video_id=r["video_id"],
                              start_ts=start, end_ts=end, is_fall=is_fall))
    return out


# ----------------------------------------------------------------------
# 토픽 recorder — rclpy Node, 한 번만 만들어 재사용
# ----------------------------------------------------------------------
class FallStateRecorder(Node):
    """`/vision/fall_state` 를 구독해 메시지 + 수신 시각을 메모리 buffer 에 누적."""

    def __init__(self) -> None:
        super().__init__("eval_fall_recorder")
        self._lock = threading.Lock()
        self._messages: List[Dict] = []
        self.create_subscription(String, "/vision/fall_state",
                                 self._on_msg, 10)

    def _on_msg(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        d["_recv_ts"] = time.time()
        with self._lock:
            self._messages.append(d)

    def reset(self) -> None:
        with self._lock:
            self._messages.clear()

    def snapshot(self) -> List[Dict]:
        with self._lock:
            return list(self._messages)


# ----------------------------------------------------------------------
# 영상 길이 추정 (ffprobe 가 있으면 정확, 없으면 사용자 인자)
# ----------------------------------------------------------------------
def video_duration_s(path: Path, fallback: float) -> float:
    if not path.is_file():
        return fallback
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode().strip()
        return float(out)
    except Exception:
        return fallback


# ----------------------------------------------------------------------
# 한 영상 평가
# ----------------------------------------------------------------------
def run_one_video(video: Path, gt: GTRow, recorder: FallStateRecorder,
                   *, tau: float, default_dur: float,
                   launch_extra: List[str], engine_warmup_s: float) -> EvalRow:
    row = EvalRow(video_id=gt.video_id, is_fall=gt.is_fall, detected=False)

    if not video.is_file():
        row.error = f"video file not found: {video}"
        row.classification = _classify(False, gt.is_fall)
        return row

    dur = video_duration_s(video, fallback=default_dur)

    recorder.reset()
    t_launch = time.time()

    # ros2 launch 로 fall_detection_node 띄우기
    cmd = [
        "ros2", "launch", "mind_care_perception", "fall_detection.launch.py",
        "source_mode:=file",
        f"file_uri:=file://{video.resolve()}",
    ] + launch_extra
    proc = subprocess.Popen(cmd,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             preexec_fn=os.setsid)

    try:
        # engine 로드 + 영상 길이 + 살짝 여유
        wait_s = engine_warmup_s + dur + 5.0
        end_at = time.time() + wait_s
        # spin 은 main loop 가 별도 thread 에서 돌고 있다고 가정 (run_evaluation 측)
        while time.time() < end_at and proc.poll() is None:
            time.sleep(0.1)
    finally:
        # SIGINT → 5초 대기 → SIGKILL → 추가 sleep (publisher TTL 만료)
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=2.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        time.sleep(2.0)

    msgs = recorder.snapshot()
    row.n_messages = len(msgs)

    # 영상 시작은 launch 직후 engine_warmup_s 지난 시점으로 근사
    video_start_clock = t_launch + engine_warmup_s

    # 첫 fall_detected=true 의 video-relative time
    first_true: Optional[Dict] = next(
        (m for m in msgs if m.get("fall_detected")), None)
    if first_true is not None:
        row.detected = True
        row.detect_ts = max(0.0, first_true["_recv_ts"] - video_start_clock)
    if any(m.get("fall_confirmed") for m in msgs):
        row.confirmed = True

    # 분류
    row.classification = _classify_with_window(row, gt, tau=tau)
    if row.classification == "TP" and gt.start_ts is not None and row.detect_ts is not None:
        row.latency_s = row.detect_ts - gt.start_ts
    return row


def _classify(detected: bool, is_fall: bool) -> str:
    if is_fall and detected:     return "TP"
    if is_fall and not detected: return "FN"
    if not is_fall and detected: return "FP"
    return "TN"


def _classify_with_window(row: EvalRow, gt: GTRow, *, tau: float) -> str:
    """GT fall 구간 ±τ 안에 trigger 한 경우만 TP. 구간 밖이면 FP 로."""
    if not row.detected:
        return "FN" if gt.is_fall else "TN"
    if not gt.is_fall:
        return "FP"
    # gt.is_fall=True
    if gt.start_ts is None or row.detect_ts is None:
        return "TP"   # 시간 정보 없으면 단순 detection 만 보고 TP
    lo = gt.start_ts - tau
    hi = (gt.end_ts if gt.end_ts is not None else gt.start_ts) + tau
    return "TP" if lo <= row.detect_ts <= hi else "FP"


# ----------------------------------------------------------------------
# 전체 평가
# ----------------------------------------------------------------------
def run_evaluation(args: argparse.Namespace) -> Dict:
    gt_rows = load_gt(Path(args.gt))
    print(f"[eval] {len(gt_rows)} 영상 — fall={sum(1 for g in gt_rows if g.is_fall)} "
          f"adl={sum(1 for g in gt_rows if not g.is_fall)}", flush=True)

    rclpy.init()
    recorder = FallStateRecorder()

    spin_stop = threading.Event()
    def _spin():
        while rclpy.ok() and not spin_stop.is_set():
            rclpy.spin_once(recorder, timeout_sec=0.1)
    spin_thread = threading.Thread(target=_spin, name="recorder-spin", daemon=True)
    spin_thread.start()

    launch_extra: List[str] = list(args.launch_extra or [])
    if args.pgie_config:
        launch_extra.append(f"pgie_config_file:={args.pgie_config}")
    if args.tracker_config:
        launch_extra.append(f"tracker_config_file:={args.tracker_config}")
    for kv in args.param or []:
        launch_extra.append(kv)   # e.g. "tilt_deg_thr:=55.0"

    results: List[EvalRow] = []
    try:
        for i, gt in enumerate(gt_rows, 1):
            video = Path(args.videos_dir) / gt.video_id
            print(f"[{i}/{len(gt_rows)}] {gt.video_id}  is_fall={gt.is_fall}",
                  flush=True)
            r = run_one_video(
                video, gt, recorder,
                tau=args.tau, default_dur=args.default_dur,
                launch_extra=launch_extra,
                engine_warmup_s=args.engine_warmup_s,
            )
            print(f"    → {r.classification}  detect={r.detected}  "
                  f"detect_ts={r.detect_ts}  conf={r.confirmed}  msgs={r.n_messages}",
                  flush=True)
            results.append(r)
    finally:
        spin_stop.set()
        spin_thread.join(timeout=2.0)
        recorder.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    return summarize(results)


# ----------------------------------------------------------------------
# 요약 (Recall/Precision/F1 + latency)
# ----------------------------------------------------------------------
def summarize(rows: List[EvalRow]) -> Dict:
    cm = {"TP": 0, "FN": 0, "FP": 0, "TN": 0}
    for r in rows:
        cm[r.classification] = cm.get(r.classification, 0) + 1

    tp, fn, fp, tn = cm["TP"], cm["FN"], cm["FP"], cm["TN"]
    recall    = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)

    latencies = [r.latency_s for r in rows
                 if r.classification == "TP" and r.latency_s is not None]
    lat_p50 = statistics.median(latencies) if latencies else None
    lat_p95 = (statistics.quantiles(latencies, n=20)[18]   # 95th percentile
               if len(latencies) >= 20 else
               (max(latencies) if latencies else None))

    summary = {
        "n_videos": len(rows),
        "confusion_matrix": cm,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "f1": round(f1, 4),
        "latency_p50_s": lat_p50,
        "latency_p95_s": lat_p95,
        "rows": [asdict(r) for r in rows],
    }

    # 사람이 읽을 한 줄 요약
    print()
    print("=" * 60)
    print(f"  TP={tp}  FN={fn}  FP={fp}  TN={tn}")
    print(f"  Recall    = {recall:.4f}    (KPI ≥ 0.95 — "
          f"{'PASS ✅' if recall >= 0.95 else 'FAIL'})")
    print(f"  Precision = {precision:.4f}    (KPI ≥ 0.85 — "
          f"{'PASS ✅' if precision >= 0.85 else 'FAIL'})")
    print(f"  F1        = {f1:.4f}")
    if lat_p50 is not None:
        print(f"  Latency   p50={lat_p50:.2f}s  p95={lat_p95:.2f}s")
    print("=" * 60)
    return summary


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Phase 4 낙상 감지 정량 평가")
    p.add_argument("--videos-dir", required=True, type=Path,
                    help="영상 파일들이 있는 디렉터리 (CSV.video_id 가 상대경로)")
    p.add_argument("--gt", required=True, type=Path,
                    help="ground truth CSV (video_id,start_ts,end_ts,is_fall)")
    p.add_argument("--output-json", type=Path,
                    help="결과 JSON 저장 경로")
    p.add_argument("--tau", type=float, default=3.0,
                    help="GT fall 시간 ±τ 안에서만 TP (default 3.0s)")
    p.add_argument("--default-dur", type=float, default=10.0,
                    help="ffprobe 실패 시 사용할 영상 길이 (s)")
    p.add_argument("--engine-warmup-s", type=float, default=4.0,
                    help="launch ~ 영상 시작 사이 엔진 로드 추정 시간")
    p.add_argument("--pgie-config",    type=str, default="",
                    help="pgie config 절대경로 override (필요 시)")
    p.add_argument("--tracker-config", type=str, default="",
                    help="tracker config 절대경로 override")
    p.add_argument("--param", action="append", metavar="KEY:=VALUE",
                    help="launch 인자 추가 (반복 가능). "
                         "예: --param tilt_deg_thr:=55.0")
    p.add_argument("--launch-extra", action="append", metavar="KEY:=VALUE",
                    help="(고급) launch 에 그대로 전달할 추가 인자")
    args = p.parse_args()

    summary = run_evaluation(args)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with args.output_json.open("w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"[eval] saved → {args.output_json}")


if __name__ == "__main__":
    main()
