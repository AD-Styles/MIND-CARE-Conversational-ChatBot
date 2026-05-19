"""ros_bridge.py — ROS 토픽 ↔ DB / WS 브리지.

별도 thread 에서 rclpy.spin 을 돌리고, 콜백마다:
  1. SQLite events/alerts/deliveries 테이블에 누적
  2. Broadcaster 에 cross-thread publish
  3. /emergency/ack 같은 outbound 토픽 발행 helper 제공
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .db import DB
from .ws import Broadcaster
from .schemas import Status

log = logging.getLogger("mind_care_api.ros_bridge")


class _BridgeNode(Node):
    def __init__(self, db: DB, broadcaster: Broadcaster, elder_id: str):
        super().__init__("api_gateway_bridge")
        self.db = db
        self.bc = broadcaster
        self.elder_id = elder_id

        # 캐시 — 최근 status (REST GET /elders/{id}/status 응답)
        self._last_status: Status = Status(elder_id=elder_id, ts=time.time(),
                                            presence=False)
        self._status_lock = threading.Lock()

        # outbound: ack
        self._pub_ack = self.create_publisher(String, "/emergency/ack", 10)

        # subscriptions
        self.create_subscription(String, "/vision/state",
                                 self._on_vision_state, 10)
        self.create_subscription(String, "/vision/fall_state",
                                 self._on_fall_state, 10)
        self.create_subscription(String, "/emergency/alert",
                                 self._on_alert, 10)
        self.create_subscription(String, "/emergency/delivery",
                                 self._on_delivery, 10)

    # ------------------------------------------------------------------
    # public API (REST 레이어가 호출)
    # ------------------------------------------------------------------
    def get_last_status(self) -> Status:
        with self._status_lock:
            return self._last_status.model_copy()

    def publish_ack(self, alert_id: str, guardian_id: int,
                    note: Optional[str] = None) -> None:
        msg = String()
        msg.data = json.dumps({
            "alert_id": alert_id,
            "guardian_id": guardian_id,
            "note": note,
            "ts": time.time(),
        }, ensure_ascii=False)
        self._pub_ack.publish(msg)

    # ------------------------------------------------------------------
    # subscribers
    # ------------------------------------------------------------------
    def _on_vision_state(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        emo = None
        if d.get("emotion") and d.get("emotion") != "unknown":
            emo = {"label": d["emotion"], "confidence": float(d.get("emotion_conf", 0.0))}
        with self._status_lock:
            self._last_status = Status(
                elder_id=self.elder_id,
                ts=float(d.get("ts", time.time())),
                presence=bool(d.get("presence", False)),
                track_count=int(d.get("track_count", 0)),
                fall_detected=self._last_status.fall_detected,
                fall_confirmed=self._last_status.fall_confirmed,
                emotion=emo if emo else None,
                last_speech_ts=self._last_status.last_speech_ts,
                session_state=self._last_status.session_state,
            )
        self.db.session  # 가벼운 events 만 별도 thread 에서 안 쓰면 부하 큼 → drop
        self.bc.publish_threadsafe("status",
                                    self._last_status.model_dump(mode="json"))

    def _on_fall_state(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        with self._status_lock:
            self._last_status = self._last_status.model_copy(update={
                "ts": float(d.get("ts", time.time())),
                "fall_detected": bool(d.get("fall_detected", False)),
                "fall_confirmed": bool(d.get("fall_confirmed", False)),
                "track_count":  int(d.get("track_count", 0)),
                "presence":     bool(d.get("presence", self._last_status.presence)),
            })
        self.bc.publish_threadsafe("status",
                                    self._last_status.model_dump(mode="json"))

    def _on_alert(self, msg: String) -> None:
        try:
            alert = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        # alerts 테이블엔 dispatcher 가 이미 row 를 만들었지만, 안전하게 upsert.
        # 여기서는 events 로그 + WS 브로드캐스트만.
        from .db import Event
        with self.db.session() as s:
            s.add(Event(
                elder_id=alert.get("elder_id", self.elder_id),
                ts=float(alert.get("ts", time.time())),
                type="alert_raised",
                payload_json=json.dumps(alert, ensure_ascii=False),
            ))
        self.bc.publish_threadsafe("alert", alert)

    def _on_delivery(self, msg: String) -> None:
        try:
            d = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        self.bc.publish_threadsafe("delivery", d)


class ROSBridge:
    """노드 + spin thread 묶음. FastAPI 시작 시 .start(), 종료 시 .stop()."""

    def __init__(self, db: DB, broadcaster: Broadcaster, elder_id: str):
        self.db = db
        self.bc = broadcaster
        self.elder_id = elder_id
        self._node: Optional[_BridgeNode] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not rclpy.ok():
            rclpy.init()
        self._node = _BridgeNode(self.db, self.bc, self.elder_id)

        def _spin():
            try:
                while rclpy.ok() and not self._stop.is_set():
                    rclpy.spin_once(self._node, timeout_sec=0.1)
            finally:
                if self._node is not None:
                    self._node.destroy_node()

        self._thread = threading.Thread(target=_spin, name="ros-bridge-spin",
                                         daemon=True)
        self._thread.start()
        log.info("ROSBridge started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if rclpy.ok():
            rclpy.shutdown()
        log.info("ROSBridge stopped")

    @property
    def node(self) -> _BridgeNode:
        assert self._node is not None, "ROSBridge.start() 가 먼저 호출되어야 함"
        return self._node
