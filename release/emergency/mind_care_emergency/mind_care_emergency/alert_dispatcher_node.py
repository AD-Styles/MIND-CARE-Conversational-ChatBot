"""alert_dispatcher_node.py — /emergency/alert 구독 → 3중 채널 발송 + 큐.

흐름:
  1. /emergency/alert 수신 → DB 에 alert 저장 + 채널별 pending row 생성
  2. ticker (default 1 Hz) 가 pending 클레임 → 채널 send → 결과로 status 갱신
  3. 실패한 row 는 backoff 시간을 next_attempt 에 기록 (다음 tick 에 재시도)
  4. 모든 채널 결과는 /emergency/delivery 로 발행 (api_gateway 가 WS push)

채널 우선순위:
  buzzer 는 무조건 등록 (오프라인 보장)
  fcm/sms 는 자격증명 있을 때만 — 없으면 mock 으로 fallback
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .alerts_db import AlertsDB
from .channels import (BuzzerChannel, Channel, ChannelResult, FCMChannel,
                        LocalBuzzerChannel, MockChannel, TwilioSMSChannel)


BACKOFF_S = [1.0, 5.0, 30.0, 300.0]   # 1s, 5s, 30s, 5min
MAX_RETRY = len(BACKOFF_S)


class AlertDispatcherNode(Node):
    def __init__(self) -> None:
        super().__init__("alert_dispatcher_node")

        # 파라미터
        default_db = str(Path.home() / "마음돌봄" / "release" / "emergency"
                         / "state" / "mindcare.db")
        self.declare_parameter("db_path", default_db)
        self.declare_parameter("dispatch_period_s", 1.0)
        self.declare_parameter("dispatch_mode", "auto")   # auto | mock
        self.declare_parameter("fcm_credentials_path", "")
        # Jetson GPIO 부저 — BOARD pin 7 (BCM 4). gpio 그룹 또는 sudo 필요.
        self.declare_parameter("buzzer_gpio_pin", 7)
        self.declare_parameter("buzzer_gpio_pattern", "siren")  # siren | beep | solid
        self.declare_parameter("buzzer_gpio_duration_s", 3.0)

        gp = lambda k: self.get_parameter(k).value
        db_path = str(gp("db_path"))
        self._db = AlertsDB(db_path)
        self.get_logger().info(f"AlertsDB at {db_path}")

        # 채널 구성
        self._channels: Dict[str, Channel] = {}
        self._build_channels(str(gp("dispatch_mode")), str(gp("fcm_credentials_path")))
        self._channel_names = list(self._channels.keys())
        self.get_logger().info(f"채널: {self._channel_names}")

        # 발행자 — delivery 결과
        self._pub_delivery = self.create_publisher(String, "/emergency/delivery", 16)

        # 구독 — alert
        self.create_subscription(String, "/emergency/alert",
                                 self._on_alert, 10)

        # 워커 timer
        self.create_timer(float(gp("dispatch_period_s")), self._dispatch_tick)
        self._tick_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 초기화 보조
    # ------------------------------------------------------------------
    def _build_channels(self, mode: str, fcm_cred: str) -> None:
        gp = lambda k: self.get_parameter(k).value
        # GPIO 부저 (Jetson AGX Xavier) — Jetson.GPIO + gpio 그룹 권한일 때만 활성
        gpio = BuzzerChannel(
            pin=int(gp("buzzer_gpio_pin")),
            default_pattern=str(gp("buzzer_gpio_pattern")),
            default_duration_s=float(gp("buzzer_gpio_duration_s")),
        )
        if gpio.available():
            self._channels[gpio.name] = gpio   # "buzzer_gpio"
            self.get_logger().info(
                f"GPIO 부저 활성 pin={gpio.pin} pattern={gpio.default_pattern}"
            )

        # ALSA 부저 — 항상 시도 (GPIO 와 병행, 또는 GPIO 미가용 시 단독)
        buzzer = LocalBuzzerChannel()
        if buzzer.available():
            self._channels[buzzer.name] = buzzer

        if mode == "mock":
            self._channels["mock"] = MockChannel()
            return

        # auto: 자격증명이 있으면 실제, 없으면 mock fallback
        fcm = FCMChannel(credentials_path=(fcm_cred or None))
        if fcm.available():
            self._channels[fcm.name] = fcm
        else:
            self.get_logger().warn("FCM 자격증명 없음 → mock channel 등록")
            self._channels["fcm"] = MockChannel()   # 같은 자리에 mock

        sms = TwilioSMSChannel()
        if sms.available():
            self._channels[sms.name] = sms
        else:
            self.get_logger().warn("Twilio 자격증명 없음 → SMS 채널 비활성")

    # ------------------------------------------------------------------
    # 알림 수신
    # ------------------------------------------------------------------
    def _on_alert(self, msg: String) -> None:
        try:
            alert = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warn("alert JSON 파싱 실패")
            return
        self._db.insert_alert(alert, channels=self._channel_names)
        self.get_logger().info(
            f"alert 큐잉됨: {alert['alert_id']} type={alert['type']} "
            f"channels={self._channel_names}"
        )

    # ------------------------------------------------------------------
    # 디스패처 워커 (timer)
    # ------------------------------------------------------------------
    def _dispatch_tick(self) -> None:
        # 동시에 여러 tick 안 돌게
        if not self._tick_lock.acquire(blocking=False):
            return
        try:
            pendings = self._db.claim_pending(max_rows=16)
            for row in pendings:
                self._try_deliver(row)
        finally:
            self._tick_lock.release()

    def _try_deliver(self, row: dict) -> None:
        ch = self._channels.get(row["channel"])
        if ch is None:
            self._db.mark_delivery(row["id"], "dropped",
                                    retry=row["retry"],
                                    response={"error": "no such channel"},
                                    next_attempt=None)
            return

        alert = self._db.fetch_alert_payload(row["alert_id"])
        if alert is None:
            self._db.mark_delivery(row["id"], "dropped",
                                    retry=row["retry"],
                                    response={"error": "alert vanished"},
                                    next_attempt=None)
            return

        # 보호자 목록 조회 (DB) — 단순화: 같은 elder_id 의 모든 보호자
        guardians = self._fetch_guardians(alert["elder_id"])

        result: ChannelResult = ch.send(alert, guardians)
        next_retry = row["retry"] + 1
        if result.ok:
            self._db.mark_delivery(row["id"], "ok",
                                    retry=row["retry"],
                                    response=result.detail,
                                    next_attempt=None)
        elif next_retry > MAX_RETRY:
            self._db.mark_delivery(row["id"], "failed",
                                    retry=next_retry,
                                    response={"detail": result.detail,
                                              "error": result.error},
                                    next_attempt=None)
        else:
            backoff = BACKOFF_S[min(row["retry"], MAX_RETRY - 1)]
            self._db.mark_delivery(row["id"], "pending",
                                    retry=next_retry,
                                    response={"detail": result.detail,
                                              "error": result.error},
                                    next_attempt=time.time() + backoff)

        # delivery 결과 토픽 발행 (노드 종료 race 보호)
        msg = String()
        msg.data = json.dumps({
            "alert_id": row["alert_id"],
            "channel": row["channel"],
            "status": "ok" if result.ok else
                       ("failed" if next_retry > MAX_RETRY else "retry"),
            "retry": next_retry if not result.ok else row["retry"],
            "ts": time.time(),
            "detail": result.detail,
            "error": result.error,
        }, ensure_ascii=False)
        try:
            self._pub_delivery.publish(msg)
        except Exception as exc:
            # 노드 종료 시점 race — context invalid 등. 무시 후 진행.
            self.get_logger().debug(f"delivery publish skipped: {exc}")

        # 모든 채널 ok 또는 dropped 면 alert 자체 status delivered 로 갱신
        self._maybe_finalize_alert(row["alert_id"])

    def _fetch_guardians(self, elder_id: str) -> List[dict]:
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT id, name, phone, fcm_token, role, lang FROM guardians "
                "WHERE elder_id=?", (elder_id,))
            rows = cur.fetchall()
        return [{"id": r[0], "name": r[1], "phone": r[2],
                 "fcm_token": r[3], "role": r[4], "lang": r[5]} for r in rows]

    def _maybe_finalize_alert(self, alert_id: str) -> None:
        """모든 delivery 가 ok|failed|dropped 면 alert.status 를 'delivered' 로."""
        with self._db.cursor() as cur:
            cur.execute(
                "SELECT status FROM alert_deliveries WHERE alert_id=?",
                (alert_id,))
            statuses = [r[0] for r in cur.fetchall()]
        if any(s == "pending" for s in statuses):
            return
        any_ok = any(s == "ok" for s in statuses)
        self._db.update_alert_status(alert_id,
                                      "delivered" if any_ok else "failed")


def main() -> None:
    rclpy.init()
    node = AlertDispatcherNode()
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
