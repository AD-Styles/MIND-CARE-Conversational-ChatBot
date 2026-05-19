"""vision_deepstream_node.py — Phase 2 실 비전 노드.

DeepStream 8.0 파이프라인 (YOLOv8n-face + Emotion SGIE) 을 구동하고,
주기적으로 `/vision/state` (std_msgs/String JSON) 토픽을 발행한다.
스키마는 Phase 1 에뮬레이터와 동일.

파라미터
  source_mode      : "test" | "v4l2" | "file" | "ros"
  image_topic      : sensor_msgs/Image 입력 (source_mode=ros)
  v4l2_device      : "/dev/video0"            (source_mode=v4l2)
  file_uri         : "file:///path/to.mp4"    (source_mode=file)
  width / height / fps
  pgie_config_file : 절대경로 (없으면 패키지 share 내 기본값)
  sgie_config_file : 〃
  tracker_config_file : 〃 (선택)
  publish_topic    : "/vision/state"
  publish_period_s : 0.5
  registered_name  : "철수 어르신"   (Phase 3 이전, track_id != 0 이면 표시)

사용
  ros2 run mind_care_perception vision_deepstream_node \
      --ros-args -p source_mode:=test
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from sensor_msgs.msg import Image

# DeepStream / GStreamer / pyds 의 import 는 rclpy.Node 가 만들어진 뒤에 한다.
# (`gi` 와 `pyds` 의 module-level 초기화가 ROS DDS 의 시그널 마스크와 충돌해
#  rclpy.Node.__init__ 안에서 SIGSEGV 를 일으키는 사례가 있음.)
# 실제 import 는 VisionDeepStreamNode.__init__ 안에서 수행.


def _default_share_path(filename: str) -> str:
    """ament 설치 share 디렉터리에서 config 파일 경로를 찾는다."""
    try:
        from ament_index_python.packages import get_package_share_directory
        share = get_package_share_directory("mind_care_perception")
        return str(Path(share) / "config" / filename)
    except Exception:
        # 개발 트리 fallback
        here = Path(__file__).resolve().parent.parent
        return str(here / "config" / filename)


class VisionDeepStreamNode(Node):
    def __init__(self) -> None:
        super().__init__("vision_deepstream_node")

        # rclpy.Node 초기화가 끝난 뒤에 GStreamer/pyds import (위 주석 참조)
        from .ds_metadata import (  # noqa: WPS433  (import-not-at-top intentional)
            VisionAggregator, VisionSnapshot, make_buffer_probe,
        )
        from .ds_pipeline import DEEPSTREAM_AVAILABLE, DeepStreamPipeline
        self._VisionSnapshot = VisionSnapshot

        # ----------------- 파라미터 선언 -----------------
        self.declare_parameter("source_mode", "test")
        self.declare_parameter("image_topic", "/camera/image_raw")
        self.declare_parameter("v4l2_device", "/dev/video0")
        self.declare_parameter("file_uri", "")
        self.declare_parameter("width", 1280)
        self.declare_parameter("height", 720)
        self.declare_parameter("fps", 30)
        self.declare_parameter("pgie_config_file", "")
        self.declare_parameter("sgie_config_file", "")
        self.declare_parameter("tracker_config_file", "")
        self.declare_parameter("publish_topic", "/vision/state")
        self.declare_parameter("publish_period_s", 0.5)
        self.declare_parameter("registered_name", "철수 어르신")
        self.declare_parameter("min_emotion_conf", 0.3)
        self.declare_parameter("stale_window_s", 1.0)

        # ----------------- 값 읽기 -----------------
        gp = lambda k: self.get_parameter(k).value
        source_mode = str(gp("source_mode"))
        width  = int(gp("width"))
        height = int(gp("height"))
        fps    = int(gp("fps"))
        pgie = str(gp("pgie_config_file")) or _default_share_path("pgie_yolov8n_face.txt")
        sgie = str(gp("sgie_config_file")) or _default_share_path("sgie_emotion.txt")
        tracker = str(gp("tracker_config_file")) or _default_share_path("tracker_NvDCF.yml")
        if not os.path.isfile(tracker):
            tracker = ""  # 트래커 config 없으면 라이브러리 기본값 사용

        self._registered_name = str(gp("registered_name"))
        self._publish_period_s = float(gp("publish_period_s"))
        self._publish_topic = str(gp("publish_topic"))

        # ----------------- 어그리게이터 + probe -----------------
        self._agg = VisionAggregator(
            ema_alpha=0.3,
            stale_window_s=float(gp("stale_window_s")),
            min_emotion_conf=float(gp("min_emotion_conf")),
        )
        # 임시 face_id resolver — track_id 기반 단순 매핑 (Phase 3 에서 ArcFace 로 대체)
        self._agg.set_face_id_resolver(self._resolve_face_id)

        probe = make_buffer_probe(self._agg, pgie_id=1, sgie_id=2)

        # ----------------- 파이프라인 -----------------
        self._pipeline: Optional[DeepStreamPipeline] = None
        self._cv_bridge = None  # ros 모드에서만 사용

        if not DEEPSTREAM_AVAILABLE:
            self.get_logger().error(
                "DeepStream/GStreamer 가 import 되지 않았습니다. "
                "노드는 살아 있지만 파이프라인은 시작하지 않습니다."
            )
        else:
            try:
                self._pipeline = DeepStreamPipeline(
                    source_mode=source_mode,
                    pgie_config=pgie,
                    sgie_config=sgie,
                    tracker_config=tracker or None,
                    on_buffer_probe=probe,
                    width=width, height=height, fps=fps,
                    v4l2_device=str(gp("v4l2_device")),
                    file_uri=str(gp("file_uri")),
                )
                self._pipeline.start()
            except Exception as exc:
                self.get_logger().error(f"파이프라인 시작 실패: {exc}")
                self._pipeline = None

        # ----------------- ROS pub/sub -----------------
        self._pub = self.create_publisher(String, self._publish_topic, 10)

        if source_mode == "ros":
            try:
                from cv_bridge import CvBridge
                self._cv_bridge = CvBridge()
                self.create_subscription(
                    Image, str(gp("image_topic")), self._on_image, 10
                )
                self.get_logger().info(
                    f"ros 입력 모드 — {gp('image_topic')} 구독 시작"
                )
            except Exception as exc:
                self.get_logger().error(f"cv_bridge 로드 실패: {exc}")

        # 발행 타이머
        self.create_timer(self._publish_period_s, self._on_publish_tick)

        self.get_logger().info(
            f"VisionDeepStreamNode ready — mode={source_mode}, "
            f"publish={self._publish_topic} @ {self._publish_period_s}s"
        )

    # ------------------------------------------------------------------
    # ROS 이미지 콜백 (source_mode=ros 일 때만)
    # ------------------------------------------------------------------
    def _on_image(self, msg: Image) -> None:
        if self._pipeline is None or self._cv_bridge is None:
            return
        try:
            frame = self._cv_bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as exc:
            self.get_logger().warn(f"cv_bridge 변환 실패: {exc}")
            return
        self._pipeline.push_frame(frame)

    # ------------------------------------------------------------------
    # 주기 발행
    # ------------------------------------------------------------------
    def _on_publish_tick(self) -> None:
        snap = self._agg.snapshot()
        state = self._snapshot_to_dict(snap)
        msg = String()
        msg.data = json.dumps(state, ensure_ascii=False)
        self._pub.publish(msg)

    def _snapshot_to_dict(self, snap) -> dict:
        return {
            "ts": time.time(),
            "presence": bool(snap.presence),
            "face_id": snap.face_id,
            "face_name": snap.face_name or "",
            "emotion": snap.emotion,
            "emotion_conf": float(snap.emotion_conf),
            "emotion_scores": snap.emotion_scores,
            "track_count": int(snap.track_count),
            "fall_detected": bool(snap.fall_detected),
        }

    # ------------------------------------------------------------------
    # face_id resolver (Phase 3 진입 전 임시 — 최초 트랙은 등록자로 가정)
    # ------------------------------------------------------------------
    def _resolve_face_id(self, track_id: int):
        if track_id <= 0:
            return None, ""
        # Phase 3 ArcFace 통합 시 여기를 임베딩 ↔ DB 매칭으로 대체
        return f"track_{track_id}", self._registered_name

    # ------------------------------------------------------------------
    # 종료
    # ------------------------------------------------------------------
    def destroy_node(self):  # noqa: D401
        try:
            if self._pipeline is not None:
                self._pipeline.stop()
        finally:
            return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = VisionDeepStreamNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
