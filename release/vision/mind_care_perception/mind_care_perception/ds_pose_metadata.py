"""ds_pose_metadata.py — DS yolov8n-pose probe + Aggregator.

probe 가 매 프레임 (~30 fps) 호출되며 다음을 한다:

  1. obj_meta_list 순회 → 사람 박스 + tracker_id 수집
  2. tensor_output_meta (output-tensor-meta=1) → raw [1,300,57] 텐서 읽기
  3. bbox 와 raw 행을 IoU 매칭하여 keypoint 합성
  4. PoseAggregator.update(Keypoints)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    import pyds  # type: ignore
    PYDS_AVAILABLE = True
except Exception as _exc:  # pragma: no cover
    PYDS_AVAILABLE = False
    pyds = None  # type: ignore
    logger.warning("[ds_pose_metadata] pyds 사용 불가 (%s)", _exc)

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore


from .fall_rules import FallState, FallStateMachine, Keypoints

NUM_KP = 17
ROW_DIM = 57    # 4 + 1 + 1 + 17×3
MAX_BOXES = 300


@dataclass
class PoseSnapshot:
    presence: bool
    track_count: int
    fall_detected: bool
    fall_confirmed: bool
    primary_track_id: int = 0
    last_update_ts: float = 0.0


class PoseAggregator:
    """probe 가 채우고 ROS 노드가 snapshot() 으로 읽는다.

    단일 어르신 가정 — 가장 큰 bbox (= 화면에 가장 가까이/크게 잡힌 사람)
    를 "주 대상" 으로 삼아 FallStateMachine 에 흘려 넣는다.
    """
    def __init__(self, machine: Optional[FallStateMachine] = None,
                 stale_window_s: float = 1.0):
        self._lock = threading.Lock()
        self._machine = machine if machine is not None else FallStateMachine()
        self._stale = stale_window_s
        self._last_seen_ts = 0.0
        self._last_state: FallState = FallState()
        self._last_track_count = 0
        self._last_primary = 0

    def update(self, observations: List[Keypoints]) -> None:
        """프레임당 1회 호출. observations 비어있으면 사람 미검출."""
        now = time.time()
        with self._lock:
            self._last_track_count = len(observations)
            primary = _largest(observations)
            if primary is not None:
                self._last_seen_ts = now
                self._last_primary = primary.track_id
                self._last_state = self._machine.update(primary)
            else:
                # 사람 미검출 — None 으로 호출해 부재 처리
                self._last_state = self._machine.update(None)

    def snapshot(self) -> PoseSnapshot:
        with self._lock:
            now = time.time()
            presence = (now - self._last_seen_ts) <= self._stale
            return PoseSnapshot(
                presence=presence,
                track_count=self._last_track_count,
                fall_detected=self._last_state.fall_detected,
                fall_confirmed=self._last_state.fall_confirmed,
                primary_track_id=self._last_primary,
                last_update_ts=self._last_seen_ts,
            )


def _largest(obs: List[Keypoints]) -> Optional[Keypoints]:
    if not obs:
        return None
    return max(obs, key=lambda k: (k.bbox[2]-k.bbox[0]) * (k.bbox[3]-k.bbox[1]))


# ----------------------------------------------------------------------
# pyds probe — DS pgie sgie src pad 에 등록
# ----------------------------------------------------------------------
def make_pose_probe(agg: PoseAggregator, pgie_id: int = 10):
    """nvinfer (pgie) src pad 에 등록할 buffer probe 를 만든다.

    동작
      - frame 별로 obj_meta_list 순회 → bbox + tracker_id 수집
      - user_meta_list 에서 NvDsInferTensorMeta (= pgie raw output) 추출
      - 매 bbox 마다 IoU 가 가장 높은 raw row 와 keypoint 결합
    """
    if not PYDS_AVAILABLE or np is None:
        def _noop(*a, **kw):  # pragma: no cover
            return 0
        return _noop

    from gi.repository import Gst   # type: ignore

    NVDS_INFER_TENSOR_META_API = pyds.NVDSINFER_TENSOR_OUTPUT_META

    def _probe(_pad, info, _u_data) -> int:
        gst_buf = info.get_buffer()
        if gst_buf is None:
            return Gst.PadProbeReturn.OK
        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buf))
        if batch_meta is None:
            return Gst.PadProbeReturn.OK

        l_frame = batch_meta.frame_meta_list
        while l_frame is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            except StopIteration:
                break

            # 1) raw tensor 추출
            raw_rows = _extract_tensor_rows(frame_meta, pgie_id)

            # 2) obj_meta 순회 + raw 매칭
            kp_list: List[Keypoints] = []
            l_obj = frame_meta.obj_meta_list
            while l_obj is not None:
                try:
                    obj = pyds.NvDsObjectMeta.cast(l_obj.data)
                except StopIteration:
                    break

                # tracker 가 켜져 있으면 detector_bbox 가 비고 tracker_bbox 만 채워질 수 있다.
                # 둘 중 valid (width>0) 한 쪽 사용 (Phase 2 face probe 와 동일 패턴).
                tb = obj.tracker_bbox_info.org_bbox_coords
                db = obj.detector_bbox_info.org_bbox_coords
                rect = tb if tb.width > 0 and tb.height > 0 else db
                if rect.width <= 0 or rect.height <= 0:
                    try:
                        l_obj = l_obj.next
                        continue
                    except StopIteration:
                        break
                box = (
                    float(rect.left),
                    float(rect.top),
                    float(rect.left + rect.width),
                    float(rect.top  + rect.height),
                )

                # raw row 가 있으면 bbox 도 모델 좌표로 통일 (keypoint 와 일관)
                row = _best_match_row(raw_rows, box) if raw_rows is not None else None
                if row is not None:
                    box = (float(row[0]), float(row[1]),
                           float(row[2]), float(row[3]))
                    kpts = [
                        (float(row[6 + i*3]),
                         float(row[6 + i*3 + 1]),
                         float(row[6 + i*3 + 2]))
                        for i in range(NUM_KP)
                    ]
                else:
                    kpts = [(0.0, 0.0, 0.0)] * NUM_KP   # 모두 invisible

                kp_list.append(Keypoints(
                    ts=time.time(),
                    bbox=box,
                    kpts=kpts,
                    track_id=int(obj.object_id) if obj.object_id is not None else 0,
                ))
                try:
                    l_obj = l_obj.next
                except StopIteration:
                    break

            agg.update(kp_list)

            try:
                l_frame = l_frame.next
            except StopIteration:
                break

        return Gst.PadProbeReturn.OK

    return _probe


def _extract_tensor_rows(frame_meta, pgie_id: int):
    """frame_meta.frame_user_meta_list 에서 PGIE 의 raw [300,57] 텐서를 numpy 로.

    pyds 빌드 차이를 회피하기 위해 여러 메모리 access 경로를 시도:
        1) pyds.get_ptr(layer.buffer)        ← 새 pyds
        2) int(layer.buffer)                  ← 일부 빌드
        3) ctypes 의 string_at                ← raw bytes copy
    """
    if not PYDS_AVAILABLE or np is None:
        return None
    import ctypes

    l_user = frame_meta.frame_user_meta_list
    while l_user is not None:
        try:
            user_meta = pyds.NvDsUserMeta.cast(l_user.data)
        except StopIteration:
            break

        meta_type = user_meta.base_meta.meta_type
        if meta_type == pyds.NVDSINFER_TENSOR_OUTPUT_META:
            try:
                tmeta = pyds.NvDsInferTensorMeta.cast(user_meta.user_meta_data)
            except Exception:
                tmeta = None
            if tmeta is not None and tmeta.num_output_layers > 0:
                # unique_id 가 pgie_id 와 일치할 때만 — 하지만 일부 pyds 빌드에서
                # 0 으로 나올 수 있어 0 도 허용 (PGIE 하나뿐인 우리 파이프라인에선 안전).
                if tmeta.unique_id not in (pgie_id, 0):
                    pass
                else:
                    arr = _read_layer_buffer(tmeta, ctypes)
                    if arr is not None:
                        return arr
        try:
            l_user = l_user.next
        except StopIteration:
            break
    return None


def _read_layer_buffer(tmeta, ctypes):
    """layer 0 의 buffer 를 numpy [300,57] float32 로 읽어 사본 반환."""
    nbytes = MAX_BOXES * ROW_DIM * 4
    try:
        layer = pyds.get_nvds_LayerInfo(tmeta, 0)
    except Exception as exc:
        return None

    # 주소 후보를 순서대로 시도
    addr = None
    for getter in (
        lambda: pyds.get_ptr(layer.buffer),       # pyds 1.1+ helper
        lambda: int(layer.buffer),                # 일부 빌드에서 직접 변환
        lambda: ctypes.cast(layer.buffer, ctypes.c_void_p).value,
    ):
        try:
            cand = getter()
            if cand:
                addr = int(cand)
                break
        except Exception:
            continue
    if not addr:
        return None

    try:
        buf = ctypes.string_at(addr, nbytes)
        return np.frombuffer(buf, dtype=np.float32).reshape(MAX_BOXES, ROW_DIM).copy()
    except Exception:
        return None


def _best_match_row(rows, bbox):
    """단일 어르신 가정 — raw [300,57] 중 conf 가장 높은 행 반환.

    obj_meta bbox 는 streammux 좌표(예: 1280×720), raw row 는 모델 좌표
    (640×640, letterbox padding 포함) 로 좌표계가 달라 IoU 매칭이 어렵다.
    PoseAggregator 가 가장 큰 bbox 1개만 primary 로 사용하므로, raw 에서도
    conf 최고 1개 = 같은 사람으로 간주하는 단순화가 정확도와 단순성 둘 다 잡는다.
    """
    if rows is None:
        return None
    best_idx = -1
    best_conf = 0.05   # 너무 낮은 conf 는 노이즈
    for i in range(rows.shape[0]):
        c = float(rows[i, 4])
        if c > best_conf:
            best_conf = c
            best_idx = i
    return rows[best_idx] if best_idx >= 0 else None


def _row_iou(a, b):
    ix1 = max(a[0], b[0]); iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2]); iy2 = min(a[3], b[3])
    iw = max(0.0, ix2 - ix1); ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    aa = max(0.0, a[2]-a[0]) * max(0.0, a[3]-a[1])
    bb = max(0.0, b[2]-b[0]) * max(0.0, b[3]-b[1])
    union = aa + bb - inter
    return inter / union if union > 0 else 0.0
