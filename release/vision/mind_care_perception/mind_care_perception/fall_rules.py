"""fall_rules.py — 낙상 감지 룰 + 시간 윈도우 상태 머신.

설계 (PHASE4 §접근 후보 B):

  Frame-level rule (4가지, AND 결합)
    1) 자세 각도   : 어깨 중점-엉덩이 중점 선이 수직축에서 ≥ 60° 기울어짐
    2) 수직 압축   : 머리 y 와 엉덩이 y 의 차이 < bbox 높이 × 0.30
    3) 박스 모양   : bbox 가로/세로 비율 > 1.4
    4) 수직 가속   : 머리 y 가 0.3s 내 frame 높이의 25% 이상 떨어짐

  시간 윈도우 confirm (2단)
    "fall_event"     : 1.0s 슬라이딩 윈도우 안에서 frame-level fallen 비율 ≥ 50%
    "fall_confirmed" : fall_event 후 추가 5.0s 동안 IoU ≥ 0.85 유지 (부동)

COCO keypoint index (yolov8-pose)
    0  nose                  | 5  left_shoulder  | 6  right_shoulder
    1  left_eye  | 2 right   | 7  left_elbow     | 8  right_elbow
    3  left_ear  | 4 right   | 9  left_wrist     | 10 right_wrist
                             | 11 left_hip       | 12 right_hip
                             | 13 left_knee      | 14 right_knee
                             | 15 left_ankle     | 16 right_ankle
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple


# COCO keypoint 인덱스 상수
KP_NOSE = 0
KP_L_SHOULDER, KP_R_SHOULDER = 5, 6
KP_L_HIP, KP_R_HIP = 11, 12
KP_VIS_THR = 0.30   # keypoint visibility 임계


@dataclass
class Keypoints:
    """단일 사람의 keypoint + bbox 한 프레임 분."""
    ts: float
    bbox: Tuple[float, float, float, float]   # x1, y1, x2, y2 (네트워크 입력 좌표 = 640×640)
    kpts: List[Tuple[float, float, float]]    # (x, y, visibility) × 17
    track_id: int = 0


# ----------------------------------------------------------------------
# Frame-level rule helpers
# ----------------------------------------------------------------------
def _midpoint(p1: Tuple[float, float, float],
              p2: Tuple[float, float, float]) -> Optional[Tuple[float, float]]:
    """두 keypoint 의 중점. 둘 다 visible 이어야 valid."""
    if p1[2] < KP_VIS_THR or p2[2] < KP_VIS_THR:
        return None
    return ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)


def torso_tilt_deg(kp: Keypoints) -> Optional[float]:
    """어깨 중점 → 엉덩이 중점 선이 수직축(아래쪽)에서 얼마나 기울어졌나(0~90°).

    선이 수직(서있음) 이면 0°, 수평(누움) 이면 90°.
    어느 한 쪽이라도 keypoint 가 invisible 이면 None.
    """
    sh = _midpoint(kp.kpts[KP_L_SHOULDER], kp.kpts[KP_R_SHOULDER])
    hp = _midpoint(kp.kpts[KP_L_HIP],      kp.kpts[KP_R_HIP])
    if sh is None or hp is None:
        return None
    dx = hp[0] - sh[0]
    dy = hp[1] - sh[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return None
    # 수직축은 (0, 1) 방향 — atan2(|dx|, dy) 가 수직축 기준 각도
    angle_rad = math.atan2(abs(dx), abs(dy))
    return math.degrees(angle_rad)


def head_hip_y_compression(kp: Keypoints) -> Optional[float]:
    """(엉덩이 y - 머리 y) / bbox 높이.

    서있음: 1.0 가까이. 완전히 누움: 0 가까이.
    """
    nose = kp.kpts[KP_NOSE]
    if nose[2] < KP_VIS_THR:
        return None
    hp = _midpoint(kp.kpts[KP_L_HIP], kp.kpts[KP_R_HIP])
    if hp is None:
        return None
    bbox_h = max(1.0, kp.bbox[3] - kp.bbox[1])
    return (hp[1] - nose[1]) / bbox_h


def bbox_aspect(kp: Keypoints) -> float:
    """bbox 가로/세로. 누우면 > 1, 서있으면 < 1."""
    w = max(1.0, kp.bbox[2] - kp.bbox[0])
    h = max(1.0, kp.bbox[3] - kp.bbox[1])
    return w / h


def is_frame_fallen(
    kp: Keypoints,
    *,
    aspect_thr: float = 1.4,
    tilt_deg_thr: float = 60.0,
    compression_thr: float = 0.30,
    rules_required: int = 1,
    use_tilt_rule: bool = False,
    use_compression_rule: bool = False,
) -> bool:
    """단일 프레임 fallen 판정.

    URFDD (천장 카메라) 도메인 분석 결과 (PHASE4_DESIGN.md §룰 튜닝):
      - **aspect (bbox w/h)** 가 fall vs ADL 을 가장 안정적으로 가른다
        (fall mean 1.6+, ADL mean 0.4~0.8 — 3배 차이, 영상별 일관)
      - **tilt** 는 카메라 각도에 따라 0~85° 천차만별 → 단일 임계 X → default off
      - **head-hip y compression** 은 keypoint 가림·음수 빈번 → default off

    그래서 default 는 **aspect 단독** (rules_required=1, use_*=False).
    정면 카메라 환경 (Le2i 등) 에서는 use_tilt_rule=True 로 켜는 식으로 활성화.

    transient false positive (앉기·굽히기 순간) 는 frame-level 임계로 잡지 않고
    `FallStateMachine` 의 1 s × 50% 윈도우 + 5 s 부동 confirm 으로 흡수.
    """
    score = 0
    if use_tilt_rule:
        tilt = torso_tilt_deg(kp)
        if tilt is not None and tilt >= tilt_deg_thr:
            score += 1
    if use_compression_rule:
        comp = head_hip_y_compression(kp)
        if comp is not None and comp < compression_thr:
            score += 1
    if bbox_aspect(kp) > aspect_thr:
        score += 1
    return score >= rules_required


# ----------------------------------------------------------------------
# 시간 윈도우 상태 머신
# ----------------------------------------------------------------------
@dataclass
class FallState:
    fall_detected: bool = False
    fall_confirmed: bool = False
    last_fallen_ts: float = 0.0
    last_event_ts: float = 0.0


@dataclass
class FallStateMachine:
    """frame-level fallen 시퀀스 → fall_event → fall_confirmed.

    parameters
        window_s         : frame-level fallen 비율을 계산할 슬라이딩 윈도우(초)
        ratio_thr        : 윈도우 내 fallen 프레임 비율 임계
        confirm_idle_s   : fall_event 후 부동 확인 시간(초)
        idle_iou_thr     : 부동 판정 IoU 임계 (이전 bbox 와 비교)
        head_drop_thr    : 짧은 시간 머리 y 가속 임계 (frame 높이 비율)
        head_drop_window_s : 머리 y 가속 측정 윈도우(초)
    """
    # URFDD v4 — 더 짧은 윈도우 (0.2 s = 6 frame) + 33% (2/6 frame) 로 매우 빠른 fall 도 잡음.
    # ADL transient 차단은 5 s 부동 confirm 단계에 의존.
    window_s: float = 0.2
    ratio_thr: float = 0.33
    # 시연/운영 환경 default — 5 s 부동까지 통과해야 confirmed.
    # ADL 의 transient 정지 (앉기·굽히기 1~2 s) 는 통과 못 함 → false positive 차단.
    # URFDD 같은 5~14 s 짧은 평가 영상에선 이 시간이 영상 길이를 초과해 confirmed 가
    # 거의 안 발행되지만, 시연·운영에선 ADL FP 차단의 핵심 안전장치.
    confirm_idle_s: float = 5.0
    idle_iou_thr: float = 0.85
    head_drop_thr: float = 0.25
    head_drop_window_s: float = 0.3
    frame_height: float = 640.0   # 네트워크 입력 좌표계 기준

    # 내부 상태
    _hist: Deque[Tuple[float, bool, Tuple[float, float, float, float]]] = \
        field(default_factory=lambda: deque(maxlen=120))
    _head_y_hist: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=60))
    _state: FallState = field(default_factory=FallState)
    _event_bbox: Optional[Tuple[float, float, float, float]] = None
    _event_started_at: float = 0.0

    def update(self, kp: Optional[Keypoints]) -> FallState:
        """매 프레임 호출. kp=None 이면 사람 미검출 — 부재."""
        now = time.time()

        if kp is None:
            # 사람이 안 보이면 fall 상태 유지하지 않음 (단, fall_event 가 진행 중이면 idle 기록)
            self._gc(now)
            return self._state

        # 1) frame-level fallen
        fallen_now = is_frame_fallen(kp)

        # 2) 머리 y 가속 → 추가 강력 신호
        nose = kp.kpts[KP_NOSE]
        if nose[2] >= KP_VIS_THR:
            self._head_y_hist.append((kp.ts, nose[1]))
            old = [(t, y) for (t, y) in self._head_y_hist
                   if now - t <= self.head_drop_window_s]
            if len(old) >= 3:
                y_now = old[-1][1]
                y_then = old[0][1]
                drop = (y_now - y_then) / max(1.0, self.frame_height)
                if drop >= self.head_drop_thr:
                    fallen_now = True   # 강력한 단일 신호로 즉시 fallen 트리거

        self._hist.append((now, fallen_now, tuple(kp.bbox)))
        self._gc(now)

        # 3) 윈도우 내 fallen 비율
        recent = [(t, f, b) for (t, f, b) in self._hist if now - t <= self.window_s]
        if recent:
            ratio = sum(1 for (_, f, _) in recent if f) / len(recent)
        else:
            ratio = 0.0

        # 4) fall_event 판정
        if not self._state.fall_detected and ratio >= self.ratio_thr and len(recent) >= 5:
            self._state.fall_detected = True
            self._state.last_event_ts = now
            self._event_bbox = tuple(kp.bbox)
            self._event_started_at = now

        # 5) 사람이 일어남 → reset. 임계 0.05 — 한 번 trigger 된 fall 은 noise 로 reset 안 됨.
        if (self._state.fall_detected and not self._state.fall_confirmed
                and ratio < 0.05):
            self._state = FallState()
            self._event_bbox = None

        # 6) fall_confirmed: 부동 윈도우 확인
        if self._state.fall_detected and not self._state.fall_confirmed:
            if (now - self._event_started_at) >= self.confirm_idle_s:
                # 마지막 N 초 동안 bbox IoU 가 계속 높았는지
                idle_window = [(t, b) for (t, _, b) in self._hist
                               if now - t <= self.confirm_idle_s and b is not None]
                if self._event_bbox is not None and len(idle_window) >= 3:
                    iou_min = min(_iou(b, self._event_bbox) for (_, b) in idle_window)
                    if iou_min >= self.idle_iou_thr:
                        self._state.fall_confirmed = True

        if fallen_now:
            self._state.last_fallen_ts = now

        return self._state

    def _gc(self, now: float) -> None:
        # window_s + confirm_idle_s 보다 오래된 것은 제거
        keep_s = max(self.window_s, self.confirm_idle_s) + 1.0
        while self._hist and (now - self._hist[0][0]) > keep_s:
            self._hist.popleft()
        while self._head_y_hist and (now - self._head_y_hist[0][0]) > self.head_drop_window_s + 0.5:
            self._head_y_hist.popleft()


def _iou(a: Tuple[float, float, float, float],
         b: Tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    aa = max(0.0, (ax2 - ax1)) * max(0.0, (ay2 - ay1))
    bb = max(0.0, (bx2 - bx1)) * max(0.0, (by2 - by1))
    union = aa + bb - inter
    return inter / union if union > 0 else 0.0
