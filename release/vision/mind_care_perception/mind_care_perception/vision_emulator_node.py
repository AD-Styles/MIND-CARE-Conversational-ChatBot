"""vision_emulator_node.py — 마음돌봄 Vision Phase 1 에뮬레이터.

DeepStream/카메라 없이 dialogue_node 와의 통합을 검증하기 위한 더미 노드.
실제 비전 파이프라인(Phase 2)이 만들어지면 동일한 토픽 스키마(`/vision/state`)
로 교체된다.

발행 토픽
  /vision/state  (std_msgs/String, JSON)

JSON 스키마
  {
    "ts":            float,    # epoch sec
    "presence":      bool,     # 누군가 카메라 앞에 있나?
    "face_id":       str|null, # 등록 얼굴 매칭 ID. 미인식 시 null
    "face_name":     str,      # 화면 표시용 이름. 미인식 시 ""
    "emotion":       str,      # angry|disgust|fear|happy|neutral|sad|surprise|unknown
    "emotion_conf":  float,    # 0.0 ~ 1.0
    "emotion_scores": [float] | null,
    "track_count":   int,
    "fall_detected": bool
  }

파라미터
  publish_period_s : 발행 주기(초). 기본 2.0
  scenario         : "rotating" (기본) | "absent" | "happy" | "sad" | "fall"
  registered_name  : 인식되었을 때 전달할 이름. 기본 "철수 어르신"

사용
  ros2 run mind_care_perception vision_emulator_node
  ros2 run mind_care_perception vision_emulator_node \
      --ros-args -p scenario:=happy
"""

from __future__ import annotations

import json
import random
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


EMOTIONS = [
    "angry", "disgust", "fear",
    "happy", "neutral", "sad", "surprise",
]


class VisionEmulatorNode(Node):
    def __init__(self) -> None:
        super().__init__("vision_emulator_node")

        self.declare_parameter("publish_period_s", 2.0)
        self.declare_parameter("scenario", "rotating")
        self.declare_parameter("registered_name", "철수 어르신")
        self.declare_parameter("topic", "/vision/state")

        self.period = float(self.get_parameter("publish_period_s").value)
        self.scenario = str(self.get_parameter("scenario").value)
        self.name = str(self.get_parameter("registered_name").value)
        topic = str(self.get_parameter("topic").value)

        self.pub = self.create_publisher(String, topic, 10)
        self.timer = self.create_timer(self.period, self._tick)
        self._tick_n = 0

        self.get_logger().info(
            f"VisionEmulator ready — topic={topic}, "
            f"scenario={self.scenario}, period={self.period}s"
        )

    # ------------------------------------------------------------------
    # 시나리오별 상태 생성
    # ------------------------------------------------------------------
    def _state_rotating(self, n: int) -> dict:
        """시간에 따라 다양한 상태로 순환 — 통합 테스트에 가장 유용."""
        phases = [
            # (presence, face_id, face_name, emotion, emotion_conf, fall)
            (False, None, "",            "unknown", 0.0,  False),
            (True,  None, "",            "neutral", 0.55, False),  # 미인식 입장
            (True,  "elder_01", self.name, "neutral", 0.72, False),
            (True,  "elder_01", self.name, "happy",   0.81, False),
            (True,  "elder_01", self.name, "sad",     0.68, False),
            (True,  "elder_01", self.name, "neutral", 0.74, False),
            (False, None, "",            "unknown", 0.0,  False),  # 자리 비움
        ]
        p = phases[n % len(phases)]
        return self._make(p[0], p[1], p[2], p[3], p[4], p[5])

    def _state_static(self, presence, face_id, face_name, emotion, conf, fall):
        return self._make(presence, face_id, face_name, emotion, conf, fall)

    def _make(self, presence, face_id, face_name, emotion, conf, fall) -> dict:
        # 7-class one-hot-ish score (감정 검증용)
        scores = [0.0] * len(EMOTIONS)
        if emotion in EMOTIONS:
            idx = EMOTIONS.index(emotion)
            scores[idx] = float(conf)
            # 잡음 분배
            remaining = max(0.0, 1.0 - float(conf))
            for i in range(len(scores)):
                if i != idx:
                    scores[i] = remaining / (len(scores) - 1)
        else:
            scores = None
        return {
            "ts": time.time(),
            "presence": bool(presence),
            "face_id": face_id,
            "face_name": face_name or "",
            "emotion": emotion,
            "emotion_conf": float(conf),
            "emotion_scores": scores,
            "track_count": 1 if presence else 0,
            "fall_detected": bool(fall),
        }

    # ------------------------------------------------------------------
    # 타이머 콜백
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        n = self._tick_n
        self._tick_n += 1

        if self.scenario == "rotating":
            state = self._state_rotating(n)
        elif self.scenario == "absent":
            state = self._state_static(False, None, "", "unknown", 0.0, False)
        elif self.scenario == "happy":
            state = self._state_static(True, "elder_01", self.name, "happy", 0.85, False)
        elif self.scenario == "sad":
            state = self._state_static(True, "elder_01", self.name, "sad", 0.74, False)
        elif self.scenario == "fall":
            state = self._state_static(True, "elder_01", self.name, "fear", 0.62, True)
        else:
            # fallback — 무작위
            emo = random.choice(EMOTIONS)
            state = self._state_static(True, "elder_01", self.name, emo, 0.6, False)

        msg = String()
        msg.data = json.dumps(state, ensure_ascii=False)
        self.pub.publish(msg)
        self.get_logger().debug(f"[vision/state] {msg.data}")


def main() -> None:
    rclpy.init()
    node = VisionEmulatorNode()
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
