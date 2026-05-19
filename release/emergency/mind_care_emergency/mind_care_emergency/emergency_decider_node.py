"""emergency_decider_node.py — 상태 머신을 ROS 토픽 위에 얹는 wrapper.

구독:
  /vision/fall_state          (mind_care_perception)
  /vision/state               (mind_care_perception, presence/emotion)
  /audio/transcript           (mind_care_vision STT — std_msgs/String JSON)
  /emergency/ack              (api_gateway 가 보호자 ACK 시 발행)

발행:
  /emergency/alert            (alert JSON)
  /dialogue/proactive_speech  (능동 발화 텍스트)

순수 로직은 `decider_states.DeciderStateMachine` 가 보유. 이 노드는 ROS 콜백
을 Event 로 변환해서 `decide()` 에 흘려주고, Effect 를 다시 토픽으로 발행만 한다.
"""

from __future__ import annotations

import json
import time
from typing import Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .decider_states import (
    DeciderConfig, DeciderStateMachine, Event, EventType, State,
)


# 응급 키워드 — 질환명("심근경색" 등) 단순 매칭은 RAG 의료 상담 특성상
# 정보 질문에서도 등장해 오탐이 잦다. 따라서 본인이 위급함을 호소하는
# 증상 표현 위주로 구성한다.
PANIC_KEYWORDS = (
    # 직접 도움 요청
    "도와줘", "살려줘", "도와주세요", "119",
    # 통증 호소
    "아파요", "아파",
    # 가슴/심장 (심근경색 등 급성 흉부 증상)
    "가슴이 답답", "가슴이 조여", "가슴이 터질", "가슴이 너무",
    # 호흡 곤란
    "숨을 못 쉬", "숨이 막혀", "숨쉬기 힘들", "숨이 가빠", "숨이 안 쉬",
    # 의식/실신 (뇌졸중·실신)
    "쓰러질 것 같", "쓰러졌", "어지러워", "어지럽", "정신이 없",
    # 기타 급성 증상
    "토할 것 같", "식은땀", "말이 안 나와",
)
OK_KEYWORDS    = ("괜찮아요", "괜찮습니다", "괜찮", "문제없", "오케이", "네 괜찮")


class EmergencyDeciderNode(Node):
    def __init__(self) -> None:
        super().__init__("emergency_decider_node")

        # 파라미터
        self.declare_parameter("elder_id", "elder_01")
        self.declare_parameter("query_timeout_s", 30.0)
        self.declare_parameter("cooldown_s", 60.0)
        self.declare_parameter("emergency_auto_clear_s", 60.0)
        self.declare_parameter("long_idle_threshold_s", 6.0 * 3600.0)
        self.declare_parameter("query_speech", "괜찮으세요?")
        self.declare_parameter("long_idle_speech", "오래 조용하셨네요. 거기 계세요?")
        self.declare_parameter("tick_period_s", 0.5)

        gp = lambda k: self.get_parameter(k).value
        cfg = DeciderConfig(
            elder_id=str(gp("elder_id")),
            query_timeout_s=float(gp("query_timeout_s")),
            cooldown_s=float(gp("cooldown_s")),
            emergency_auto_clear_s=float(gp("emergency_auto_clear_s")),
            long_idle_threshold_s=float(gp("long_idle_threshold_s")),
            query_speech=str(gp("query_speech")),
            long_idle_speech=str(gp("long_idle_speech")),
        )
        self._sm = DeciderStateMachine(cfg=cfg)

        # 6 시간 부동 추적 — presence true 인데 fall_state 변화 0
        self._last_motion_ts: float = time.time()
        self._long_idle_fired: bool = False

        # 이미 발행한 alert dedupe
        self._published_alert_ids: set[str] = set()
        # fall_detected edge 감지
        self._prev_fall_detected: bool = False

        # pub
        self._pub_alert = self.create_publisher(String, "/emergency/alert", 10)
        self._pub_speech = self.create_publisher(String,
                                                  "/dialogue/proactive_speech", 10)
        # sub
        self.create_subscription(String, "/vision/fall_state",
                                 self._on_fall_state, 10)
        self.create_subscription(String, "/vision/state",
                                 self._on_vision_state, 10)
        # mind_care_vision audio_bridge_node 가 발행하는 토픽 (s 복수형)
        self.create_subscription(String, "/audio/transcripts",
                                 self._on_transcript, 10)
        self.create_subscription(String, "/emergency/ack",
                                 self._on_ack, 10)

        # 주기 tick — 타이머 평가
        self.create_timer(float(gp("tick_period_s")), self._on_tick)

        self.get_logger().info(
            f"EmergencyDeciderNode ready — elder_id={cfg.elder_id} "
            f"timeout={cfg.query_timeout_s}s cooldown={cfg.cooldown_s}s"
        )

    # ------------------------------------------------------------------
    # Subscribers
    # ------------------------------------------------------------------
    def _on_fall_state(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            return

        ts = float(d.get("ts", time.time()))
        fall_detected  = bool(d.get("fall_detected", False))
        fall_confirmed = bool(d.get("fall_confirmed", False))

        # 부동 감시 — fall 정보 자체의 변화 + presence
        if d.get("presence") and (fall_detected or fall_confirmed):
            self._last_motion_ts = ts

        # rising edge: false → true 인 시점에서만 트리거 (중복 방지)
        if fall_detected and not self._prev_fall_detected:
            self._dispatch(Event(
                type=EventType.FALL_DETECTED, ts=ts,
                payload={"fall_detected": True, "track_id": d.get("primary_track_id", 0)},
            ))
        self._prev_fall_detected = fall_detected

        if fall_confirmed:
            self._dispatch(Event(type=EventType.FALL_CONFIRMED, ts=ts,
                                 payload={"fall_confirmed": True}))

    def _on_vision_state(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        # presence 가 있고 emotion 이 변하면 motion 으로 간주
        if d.get("presence"):
            self._last_motion_ts = float(d.get("ts", time.time()))
            self._long_idle_fired = False

    def _on_transcript(self, msg: String) -> None:
        """audio_bridge_node 가 발행하는 STT JSON.
        포맷: {"text": "...", "timestamp_ns": ..., "duration_s": ..., "latency_ms": ...}
        (`ts` 또는 `speaker_match` 같은 추가 필드는 향후 호환을 위해 옵션)
        """
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        text = (d.get("text") or "").strip()
        if not text:
            return
        # timestamp_ns (audio_bridge_node) 또는 ts (legacy/test) — 둘 다 지원
        if "timestamp_ns" in d:
            ts = float(d["timestamp_ns"]) / 1e9
        else:
            ts = float(d.get("ts", time.time()))
        # 화자 검증 — speaker_match 필드가 있으면 false 인 경우 panic_word 무시.
        # 현재 audio_bridge_node 는 화자 검증 미구현 → 모든 음성을 등록자로 간주.
        spk_ok = d.get("speaker_match", True)

        self._last_motion_ts = ts
        self._long_idle_fired = False

        if any(k in text for k in PANIC_KEYWORDS) and spk_ok:
            self._dispatch(Event(type=EventType.PANIC_WORD, ts=ts,
                                  payload={"user_quote": text}))
            return
        if any(k in text for k in OK_KEYWORDS):
            self._dispatch(Event(type=EventType.USER_OK, ts=ts,
                                  payload={"user_quote": text}))

    def _on_ack(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        self._dispatch(Event(type=EventType.ACK_RECEIVED,
                             ts=float(d.get("ts", time.time())),
                             payload=d))

    # ------------------------------------------------------------------
    # Timer
    # ------------------------------------------------------------------
    def _on_tick(self) -> None:
        now = time.time()
        # long_idle 감지 — NORMAL 일 때만 트리거, 한번 fire 후엔 motion 까지 대기
        if (self._sm.state == State.NORMAL
                and not self._long_idle_fired
                and (now - self._last_motion_ts) >= self._sm.cfg.long_idle_threshold_s):
            self._long_idle_fired = True
            self._dispatch(Event(type=EventType.LONG_IDLE, ts=now,
                                  payload={"idle_s": now - self._last_motion_ts}))

        self._dispatch(Event(type=EventType.TICK, ts=now))

    # ------------------------------------------------------------------
    # Effect → 토픽
    # ------------------------------------------------------------------
    def _dispatch(self, ev: Event) -> None:
        for eff in self._sm.decide(ev):
            if eff.log:
                self.get_logger().info(eff.log)
            if eff.proactive_speech:
                m = String(); m.data = eff.proactive_speech
                self._pub_speech.publish(m)
            if eff.publish_alert is not None:
                aid = eff.publish_alert.get("alert_id")
                if aid in self._published_alert_ids:
                    continue
                self._published_alert_ids.add(aid)
                m = String()
                m.data = json.dumps(eff.publish_alert, ensure_ascii=False)
                self._pub_alert.publish(m)


def main() -> None:
    rclpy.init()
    node = EmergencyDeciderNode()
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
