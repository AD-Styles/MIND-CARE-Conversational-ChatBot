"""ds_metadata.py — pyds 메타데이터 probe + /vision/state 어그리게이터.

probe 가 매 SGIE 출력 버퍼마다 호출되며 (보통 25~30 fps),
프레임의 face/emotion 결과를 누적한다. 외부(ROS 노드)가
`snapshot()` 을 주기적으로(예: 0.5s) 읽어 /vision/state 로 발행한다.

집계 규칙
  - 프레임에서 가장 큰 bbox 의 face 를 "주 대상" 으로 선택 (단일 어르신 가정)
  - emotion: 최근 N 프레임 score 평균 (EMA, alpha=0.3)
  - presence: 최근 stale_window 내 검출이 1회 이상이면 True
  - track_id 가 가장 자주 등장한 ID 를 "현재 트랙" 으로
  - face_name / face_id 는 Phase 3 에서 ArcFace 로 채울 자리 (지금은 null/"")
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# pyds 임포트 (DS 미설치 환경에서도 import 자체는 실패하지 않게)
# ---------------------------------------------------------------------
try:
    import pyds  # type: ignore
    PYDS_AVAILABLE = True
except Exception as _exc:  # pragma: no cover
    PYDS_AVAILABLE = False
    pyds = None  # type: ignore
    logger.warning("[ds_metadata] pyds 사용 불가 — DS 8.0 미설치? (%s)", _exc)


EMOTION_LABELS: Tuple[str, ...] = (
    "angry", "disgust", "fear", "happy", "neutral", "sad", "surprise",
)
NUM_EMOTIONS = len(EMOTION_LABELS)


# ---------------------------------------------------------------------
# 결과 자료구조
# ---------------------------------------------------------------------
@dataclass
class FaceObservation:
    track_id: int
    bbox_area: float
    emotion_idx: int
    emotion_conf: float
    scores: List[float]
    ts: float


@dataclass
class VisionSnapshot:
    """`snapshot()` 결과 — /vision/state 와 직결."""
    presence: bool
    track_count: int
    face_id: Optional[str]
    face_name: str
    emotion: str
    emotion_conf: float
    emotion_scores: Optional[List[float]]
    fall_detected: bool = False  # Phase 4 에서 채움


# ---------------------------------------------------------------------
# 어그리게이터
# ---------------------------------------------------------------------
class VisionAggregator:
    """probe 콜백이 채우고, 외부가 `snapshot()` 으로 읽는 thread-safe 버퍼."""

    def __init__(
        self,
        ema_alpha: float = 0.3,
        stale_window_s: float = 1.0,
        history_len: int = 60,
        min_emotion_conf: float = 0.3,
    ) -> None:
        self.ema_alpha = float(ema_alpha)
        self.stale_window_s = float(stale_window_s)
        self.min_emotion_conf = float(min_emotion_conf)

        self._lock = threading.Lock()
        self._history: Deque[FaceObservation] = deque(maxlen=history_len)
        self._ema_scores: Optional[List[float]] = None
        self._last_seen_ts: float = 0.0

        # Phase 3 — 등록 얼굴 매핑은 외부에서 주입
        self._face_id_resolver = None  # type: Optional[callable]

    # -----------------------------------------------------------------
    # probe 가 호출하는 입력 인터페이스
    # -----------------------------------------------------------------
    def push_observation(self, obs: FaceObservation) -> None:
        with self._lock:
            self._history.append(obs)
            self._last_seen_ts = obs.ts

            # EMA 업데이트
            if self._ema_scores is None or len(self._ema_scores) != NUM_EMOTIONS:
                self._ema_scores = list(obs.scores)
            else:
                a = self.ema_alpha
                self._ema_scores = [
                    (1 - a) * old + a * new
                    for old, new in zip(self._ema_scores, obs.scores)
                ]

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._ema_scores = None
            self._last_seen_ts = 0.0

    # -----------------------------------------------------------------
    # 외부 읽기 인터페이스
    # -----------------------------------------------------------------
    def snapshot(self) -> VisionSnapshot:
        now = time.time()
        with self._lock:
            stale = (now - self._last_seen_ts) > self.stale_window_s
            recent = [o for o in self._history if (now - o.ts) <= self.stale_window_s]

            if stale or not recent:
                return VisionSnapshot(
                    presence=False,
                    track_count=0,
                    face_id=None,
                    face_name="",
                    emotion="unknown",
                    emotion_conf=0.0,
                    emotion_scores=None,
                )

            # presence True — 가장 자주 등장한 track_id, 최신 bbox 의 면적 평균
            tid_counts = Counter(o.track_id for o in recent)
            dom_tid, _ = tid_counts.most_common(1)[0]

            # 한 프레임 안의 동시 트래킹 수 추정: 시간 bin(0.1s) 별 max 트랙 수
            bins: Dict[int, set] = {}
            for o in recent:
                k = int(o.ts * 10)
                bins.setdefault(k, set()).add(o.track_id)
            track_count = max((len(s) for s in bins.values()), default=1)

            scores = list(self._ema_scores) if self._ema_scores else [0.0] * NUM_EMOTIONS
            best_idx = max(range(NUM_EMOTIONS), key=lambda i: scores[i])
            best_conf = scores[best_idx]

            if best_conf < self.min_emotion_conf:
                emotion_label = "unknown"
            else:
                emotion_label = EMOTION_LABELS[best_idx]

            # face_id 매핑 (Phase 3)
            face_id: Optional[str] = None
            face_name = ""
            if self._face_id_resolver is not None:
                try:
                    face_id, face_name = self._face_id_resolver(dom_tid)
                except Exception as exc:  # pragma: no cover
                    logger.debug("face_id_resolver 실패: %s", exc)

            return VisionSnapshot(
                presence=True,
                track_count=int(track_count),
                face_id=face_id,
                face_name=face_name or "",
                emotion=emotion_label,
                emotion_conf=float(best_conf),
                emotion_scores=[float(s) for s in scores],
            )

    # -----------------------------------------------------------------
    # Phase 3 hook
    # -----------------------------------------------------------------
    def set_face_id_resolver(self, fn) -> None:
        """fn(track_id:int) -> (face_id:str|None, face_name:str)."""
        self._face_id_resolver = fn


# ---------------------------------------------------------------------
# DeepStream pad probe
# ---------------------------------------------------------------------
def make_buffer_probe(
    aggregator: VisionAggregator,
    pgie_id: int = 1,
    sgie_id: int = 2,
):
    """SGIE src pad 용 buffer probe 함수를 반환한다."""
    if not PYDS_AVAILABLE:
        # pyds 없을 때 — probe 가 호출되어도 안전하게 무시
        def _noop_probe(_pad, _info, _u):
            return 1  # Gst.PadProbeReturn.OK
        return _noop_probe

    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # type: ignore

    def _probe(_pad, info, _u_data):
        gst_buffer = info.get_buffer()
        if not gst_buffer:
            return Gst.PadProbeReturn.OK

        try:
            batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        except Exception:
            return Gst.PadProbeReturn.OK
        if not batch_meta:
            return Gst.PadProbeReturn.OK

        now = time.time()

        # frame loop
        l_frame = batch_meta.frame_meta_list
        while l_frame is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            except StopIteration:
                break

            # object loop
            l_obj = frame_meta.obj_meta_list
            while l_obj is not None:
                try:
                    obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
                except StopIteration:
                    break

                if obj_meta.unique_component_id == pgie_id:
                    obs = _parse_object(obj_meta, sgie_id, now)
                    if obs is not None:
                        aggregator.push_observation(obs)

                try:
                    l_obj = l_obj.next
                except StopIteration:
                    break

            try:
                l_frame = l_frame.next
            except StopIteration:
                break

        return Gst.PadProbeReturn.OK

    return _probe


def _parse_object(obj_meta, sgie_id: int, ts: float) -> Optional[FaceObservation]:
    """NvDsObjectMeta → FaceObservation. SGIE 결과 없으면 None."""
    rect = obj_meta.tracker_bbox_info.org_bbox_coords
    if rect.width == 0 or rect.height == 0:
        rect = obj_meta.detector_bbox_info.org_bbox_coords
    area = float(rect.width) * float(rect.height)
    if area <= 0.0:
        return None

    # SGIE 결과 추출
    scores = [0.0] * NUM_EMOTIONS
    best_idx = -1
    best_conf = 0.0

    l_cls = obj_meta.classifier_meta_list
    while l_cls is not None:
        try:
            cls_meta = pyds.NvDsClassifierMeta.cast(l_cls.data)
        except StopIteration:
            break

        if cls_meta.unique_component_id != sgie_id:
            try:
                l_cls = l_cls.next
                continue
            except StopIteration:
                break

        l_info = cls_meta.label_info_list
        while l_info is not None:
            try:
                label_info = pyds.NvDsLabelInfo.cast(l_info.data)
            except StopIteration:
                break
            idx = int(label_info.result_class_id)
            conf = float(label_info.result_prob)
            if 0 <= idx < NUM_EMOTIONS:
                scores[idx] = conf
                if conf > best_conf:
                    best_conf = conf
                    best_idx = idx
            try:
                l_info = l_info.next
            except StopIteration:
                break

        # 첫 번째 SGIE classifier 만 사용
        break

    if best_idx < 0:
        return None  # 아직 SGIE 결과 도착 전

    return FaceObservation(
        track_id=int(obj_meta.object_id),
        bbox_area=area,
        emotion_idx=best_idx,
        emotion_conf=best_conf,
        scores=scores,
        ts=ts,
    )
