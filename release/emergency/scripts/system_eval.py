#!/usr/bin/env python3
"""system_eval.py — Phase 5 시스템 레벨 평가.

eval_fall.py 와 동일한 영상-loop 구조이지만, 판정 기준이 다음으로 바뀜:
    Vision 단독 fall_detected → ❌
    Decider 의 /emergency/alert 발행  → ✅

이게 진짜 KPI — "보호자 알림이 발생했는가". Vision Recall 77% 였더라도
시스템 레벨에서 Voice 융합 + 시간 confirm 으로 더 robust 한 신호를 만들 수 있는지 검증.

실행 흐름 (영상 1 개당):
    1. fall_detection_node       (mp4 → /vision/fall_state)
    2. emergency_decider_node    (/vision/fall_state → 상태 머신 → /emergency/alert)
    3. (선택) alert_dispatcher_node — mock 모드. 평가에는 영향 X. 부저 retry 만 발생
    4. /emergency/alert recorder (rclpy 직접 구독)

판정:
    fall 영상에 alert 발행됨        → TP
    fall 영상인데 alert 없음        → FN
    ADL 영상에 alert 발행됨         → FP
    ADL 영상이고 alert 없음         → TN
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


# ----------------------------------------------------------------------
@dataclass
class GTRow:
    video_id: str
    start_ts: Optional[float]
    end_ts:   Optional[float]
    is_fall:  bool


@dataclass
class EvalRow:
    video_id: str
    is_fall: bool
    alert_received: bool = False
    alert_ts: Optional[float] = None       # video-relative
    alert_type: Optional[str] = None
    alert_severity: Optional[str] = None
    classification: str = ""
    latency_s: Optional[float] = None
    n_alerts: int = 0


def load_gt(csv_path: Path) -> List[GTRow]:
    rows = []
    with csv_path.open() as f:
        for r in csv.DictReader(f):
            rows.append(GTRow(
                video_id=r["video_id"],
                start_ts=float(r["start_ts"]) if r.get("start_ts") else None,
                end_ts=float(r["end_ts"])     if r.get("end_ts")   else None,
                is_fall=bool(int(r["is_fall"])),
            ))
    return rows


# ----------------------------------------------------------------------
class AlertRecorder(Node):
    """`/emergency/alert` 구독 — 영상 사이 buffer reset."""
    def __init__(self) -> None:
        super().__init__("system_eval_recorder")
        self._lock = threading.Lock()
        self._alerts: List[Dict] = []
        self.create_subscription(String, "/emergency/alert", self._on, 10)

    def _on(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        d["_recv_ts"] = time.time()
        with self._lock:
            self._alerts.append(d)

    def reset(self) -> None:
        with self._lock:
            self._alerts.clear()

    def snapshot(self) -> List[Dict]:
        with self._lock:
            return list(self._alerts)


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


def _spawn(cmd: List[str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        cmd,
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )


def _kill(proc: subprocess.Popen, label: str) -> None:
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2.0)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


def run_one_video(
    video: Path, gt: GTRow, recorder: AlertRecorder, *,
    pgie_cfg: str, tracker_cfg: str,
    query_timeout_s: float, engine_warmup_s: float, default_dur: float,
    log_dir: Path,
) -> EvalRow:
    row = EvalRow(video_id=gt.video_id, is_fall=gt.is_fall)

    if not video.is_file():
        row.classification = _classify(False, gt.is_fall)
        return row

    dur = video_duration_s(video, fallback=default_dur)
    recorder.reset()
    t_launch = time.time()

    # 1) fall_detection
    fall_proc = _spawn([
        sys.executable, "-u", "-m", "mind_care_perception.fall_detection_node",
        "--ros-args",
        "-p", "source_mode:=file",
        "-p", f"file_uri:=file://{video.resolve()}",
        "-p", f"pgie_config_file:={pgie_cfg}",
        "-p", f"tracker_config_file:={tracker_cfg}",
    ], log_dir / f"{gt.video_id}.fall.log")

    # 2) emergency_decider — query_timeout 짧게 (평가 빠르게)
    decider_proc = _spawn([
        sys.executable, "-u", "-m", "mind_care_emergency.emergency_decider_node",
        "--ros-args",
        "-p", f"query_timeout_s:={query_timeout_s}",
    ], log_dir / f"{gt.video_id}.decider.log")

    try:
        wait_s = engine_warmup_s + dur + query_timeout_s + 3.0
        end_at = time.time() + wait_s
        while time.time() < end_at:
            if fall_proc.poll() is not None and decider_proc.poll() is not None:
                break
            time.sleep(0.1)
    finally:
        _kill(decider_proc, "decider")
        _kill(fall_proc, "fall")
        time.sleep(2.0)

    alerts = recorder.snapshot()
    row.n_alerts = len(alerts)
    if alerts:
        first = alerts[0]
        row.alert_received = True
        row.alert_type = first.get("type")
        row.alert_severity = first.get("severity")
        # video-relative ts
        video_start_clock = t_launch + engine_warmup_s
        row.alert_ts = max(0.0, first["_recv_ts"] - video_start_clock)

    row.classification = _classify(row.alert_received, gt.is_fall)
    if row.classification == "TP" and gt.start_ts is not None and row.alert_ts is not None:
        row.latency_s = row.alert_ts - gt.start_ts
    return row


def _classify(alert: bool, is_fall: bool) -> str:
    if is_fall and alert:     return "TP"
    if is_fall and not alert: return "FN"
    if not is_fall and alert: return "FP"
    return "TN"


# ----------------------------------------------------------------------
def run_evaluation(args: argparse.Namespace) -> Dict:
    gt_rows = load_gt(Path(args.gt))
    print(f"[sys-eval] {len(gt_rows)} 영상 — fall={sum(1 for g in gt_rows if g.is_fall)} "
          f"adl={sum(1 for g in gt_rows if not g.is_fall)}", flush=True)
    print(f"[sys-eval] query_timeout={args.query_timeout_s}s — alert 발행 기준 평가",
          flush=True)

    rclpy.init()
    recorder = AlertRecorder()

    spin_stop = threading.Event()
    def _spin():
        while rclpy.ok() and not spin_stop.is_set():
            rclpy.spin_once(recorder, timeout_sec=0.1)
    t = threading.Thread(target=_spin, daemon=True)
    t.start()

    log_dir = Path(args.log_dir or "/tmp/sys_eval")
    log_dir.mkdir(parents=True, exist_ok=True)

    results: List[EvalRow] = []
    try:
        for i, gt in enumerate(gt_rows, 1):
            video = Path(args.videos_dir) / gt.video_id
            print(f"[{i}/{len(gt_rows)}] {gt.video_id}  is_fall={gt.is_fall}", flush=True)
            r = run_one_video(
                video, gt, recorder,
                pgie_cfg=args.pgie_config, tracker_cfg=args.tracker_config,
                query_timeout_s=args.query_timeout_s,
                engine_warmup_s=args.engine_warmup_s,
                default_dur=args.default_dur,
                log_dir=log_dir,
            )
            print(f"    → {r.classification}  alert={r.alert_received}  "
                  f"ts={r.alert_ts}  type={r.alert_type}",
                  flush=True)
            results.append(r)
    finally:
        spin_stop.set(); t.join(timeout=2.0)
        recorder.destroy_node()
        if rclpy.ok(): rclpy.shutdown()

    return summarize(results)


def summarize(rows: List[EvalRow]) -> Dict:
    cm = {"TP": 0, "FN": 0, "FP": 0, "TN": 0}
    for r in rows: cm[r.classification] = cm.get(r.classification, 0) + 1
    tp, fn, fp, tn = cm["TP"], cm["FN"], cm["FP"], cm["TN"]
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    pre = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = 2*pre*rec/(pre+rec) if (pre+rec) else 0.0

    lats = [r.latency_s for r in rows if r.classification == "TP" and r.latency_s is not None]
    p50 = statistics.median(lats) if lats else None
    p95 = (statistics.quantiles(lats, n=20)[18] if len(lats) >= 20
           else (max(lats) if lats else None))

    summary = {
        "n_videos": len(rows),
        "confusion_matrix": cm,
        "recall":  round(rec, 4),
        "precision": round(pre, 4),
        "f1": round(f1, 4),
        "latency_p50_s": p50,
        "latency_p95_s": p95,
        "rows": [asdict(r) for r in rows],
    }

    print()
    print("=" * 60)
    print(f"  TP={tp}  FN={fn}  FP={fp}  TN={tn}")
    print(f"  Recall    = {rec:.4f}    (KPI ≥ 0.95 — "
          f"{'PASS ✅' if rec >= 0.95 else 'FAIL'})")
    print(f"  Precision = {pre:.4f}    (KPI ≥ 0.85 — "
          f"{'PASS ✅' if pre >= 0.85 else 'FAIL'})")
    print(f"  F1        = {f1:.4f}")
    if p50 is not None:
        print(f"  Latency   p50={p50:.2f}s  p95={p95:.2f}s")
    print("=" * 60)
    return summary


# ----------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--videos-dir", required=True, type=Path)
    p.add_argument("--gt",         required=True, type=Path)
    p.add_argument("--output-json", type=Path)
    p.add_argument("--query-timeout-s", type=float, default=5.0,
                    help="QUERY → EMERGENCY 자동 timeout (default 5s — 평가 빠르게)")
    p.add_argument("--engine-warmup-s", type=float, default=4.0)
    p.add_argument("--default-dur", type=float, default=10.0)
    p.add_argument("--pgie-config",
                    default=str(Path.home() / "마음돌봄" / "release" / "vision"
                                / "mind_care_perception" / "config"
                                / "pgie_yolov8n_pose.txt"))
    p.add_argument("--tracker-config",
                    default=str(Path.home() / "마음돌봄" / "release" / "vision"
                                / "mind_care_perception" / "config"
                                / "tracker_NvDCF.yml"))
    p.add_argument("--log-dir", default="/tmp/sys_eval")
    args = p.parse_args()

    summary = run_evaluation(args)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        with args.output_json.open("w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"[sys-eval] saved → {args.output_json}")


if __name__ == "__main__":
    main()
