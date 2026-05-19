"""fall_detection_node.py — Phase 4 낙상 감지 ROS 노드.

DS yolov8n-pose 파이프라인 → 룰 기반 낙상 판정 → /vision/fall_state JSON 발행.

토픽 스키마 (`std_msgs/String` JSON):
  {
    "ts": 1714000000.0,
    "presence": true,
    "track_count": 1,
    "primary_track_id": 3,
    "fall_detected": false,
    "fall_confirmed": false
  }

이 토픽을 vision_deepstream_node 가 구독해 /vision/state.fall_detected 채울
수도 있고, dialogue/emergency 노드가 직접 구독할 수도 있다.

파라미터
  source_mode      : "test" | "v4l2" | "file" | "ros"
  image_topic      : sensor_msgs/Image 입력 (source_mode=ros)
  v4l2_device      : "/dev/video0"
  file_uri         : "file:///path/to.mp4"
  width / height / fps
  pgie_config_file : 절대경로 (없으면 패키지 share)
  tracker_config_file : 〃
  publish_topic    : "/vision/fall_state"
  publish_period_s : 0.2  (낙상은 0.5s 주기보다 짧게 권장)
  tilt_deg_thr / compression_thr / aspect_thr
  window_s / ratio_thr / confirm_idle_s
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from sensor_msgs.msg import Image


def _default_share_path(filename: str) -> str:
    try:
        from ament_index_python.packages import get_package_share_directory
        share = get_package_share_directory("mind_care_perception")
        return str(Path(share) / "config" / filename)
    except Exception:
        here = Path(__file__).resolve().parent.parent
        return str(here / "config" / filename)


class FallDetectionNode(Node):
    def __init__(self) -> None:
        super().__init__("fall_detection_node")

        # rclpy.Node 초기화 후에 GStreamer/pyds import (Phase 2 와 동일 패턴)
        from .fall_rules import FallStateMachine
        from .ds_pose_metadata import PoseAggregator, make_pose_probe
        from .ds_pose_pipeline import DEEPSTREAM_AVAILABLE, PosePipeline

        # 파라미터
        self.declare_parameter("source_mode", "test")
        self.declare_parameter("image_topic", "/camera/image_raw")
        self.declare_parameter("v4l2_device", "/dev/video0")
        self.declare_parameter("file_uri", "")
        self.declare_parameter("width", 1280)
        self.declare_parameter("height", 720)
        self.declare_parameter("fps", 30)
        self.declare_parameter("pgie_config_file", "")
        self.declare_parameter("tracker_config_file", "")
        self.declare_parameter("publish_topic", "/vision/fall_state")
        self.declare_parameter("publish_period_s", 0.2)
        # 룰 임계
        self.declare_parameter("tilt_deg_thr", 60.0)
        self.declare_parameter("compression_thr", 0.30)
        self.declare_parameter("aspect_thr", 1.4)
        # URFDD v4 — 짧은 fall 까지 잡기 위해 window 0.2 s + ratio 33%.
        self.declare_parameter("window_s", 0.2)
        self.declare_parameter("ratio_thr", 0.33)
        self.declare_parameter("confirm_idle_s", 5.0)

        gp = lambda k: self.get_parameter(k).value
        source_mode = str(gp("source_mode"))
        width  = int(gp("width"))
        height = int(gp("height"))
        fps    = int(gp("fps"))
        pgie = str(gp("pgie_config_file")) or _default_share_path("pgie_yolov8n_pose.txt")
        tracker = str(gp("tracker_config_file")) or _default_share_path("tracker_NvDCF.yml")
        if not os.path.isfile(tracker):
            tracker = ""

        self._publish_topic = str(gp("publish_topic"))
        self._publish_period_s = float(gp("publish_period_s"))

        # FallStateMachine — 파라미터로 임계 조정
        machine = FallStateMachine(
            window_s=float(gp("window_s")),
            ratio_thr=float(gp("ratio_thr")),
            confirm_idle_s=float(gp("confirm_idle_s")),
            frame_height=640.0,    # 모델 입력 좌표
        )
        # stale_window 가 너무 짧으면 frame-level 변동에 따라 presence flap.
        # publish_period(0.2) × 5 정도 = 1.0s 이론상 충분하지만 실 파이프라인에서
        # frame skip 발생 가능 — 보수적 3.0s.
        self._agg = PoseAggregator(machine=machine, stale_window_s=3.0)
        # PGIE gie-unique-id 와 일치 (config: gie-unique-id=10)
        probe = make_pose_probe(self._agg, pgie_id=10)

        self._pipeline = None
        self._cv_bridge = None
        if not DEEPSTREAM_AVAILABLE:
            self.get_logger().error("DeepStream/GStreamer import 실패 — 파이프라인 미시작.")
        else:
            try:
                self._pipeline = PosePipeline(
                    source_mode=source_mode,
                    pgie_config=pgie,
                    on_buffer_probe=probe,
                    tracker_config=tracker or None,
                    width=width, height=height, fps=fps,
                    v4l2_device=str(gp("v4l2_device")),
                    file_uri=str(gp("file_uri")),
                )
                self._pipeline.start()
            except Exception as exc:
                self.get_logger().error(f"파이프라인 시작 실패: {exc}")
                self._pipeline = None

        # ROS pub/sub
        self._pub = self.create_publisher(String, self._publish_topic, 10)

        if source_mode == "ros":
            try:
                from cv_bridge import CvBridge
                self._cv_bridge = CvBridge()
                self.create_subscription(
                    Image, str(gp("image_topic")), self._on_image, 10
                )
                self.get_logger().info(f"ros 입력 모드 — {gp('image_topic')} 구독")
            except Exception as exc:
                self.get_logger().error(f"cv_bridge 로드 실패: {exc}")

        self.create_timer(self._publish_period_s, self._on_publish_tick)
        self.get_logger().info(
            f"FallDetectionNode ready — mode={source_mode}, "
            f"publish={self._publish_topic} @ {self._publish_period_s}s"
        )

    def _on_image(self, msg: Image) -> None:
        if self._pipeline is None or self._cv_bridge is None:
            return
        try:
            frame = self._cv_bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as exc:
            self.get_logger().warn(f"cv_bridge 변환 실패: {exc}")
            return
        self._pipeline.push_frame(frame)

    def _on_publish_tick(self) -> None:
        snap = self._agg.snapshot()
        msg = String()
        msg.data = json.dumps({
            "ts": time.time(),
            "presence": bool(snap.presence),
            "track_count": int(snap.track_count),
            "primary_track_id": int(snap.primary_track_id),
            "fall_detected": bool(snap.fall_detected),
            "fall_confirmed": bool(snap.fall_confirmed),
        }, ensure_ascii=False)
        self._pub.publish(msg)

    def destroy_node(self):  # noqa: D401
        try:
            if self._pipeline is not None:
                self._pipeline.stop()
        finally:
            return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = FallDetectionNode()
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
