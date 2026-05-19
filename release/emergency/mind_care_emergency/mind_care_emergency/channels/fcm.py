"""fcm.py — Firebase Cloud Messaging.

자격증명 (`FIREBASE_CRED_PATH`) 가 없으면 `available()` 이 False 를 반환해
dispatcher 가 자동으로 mock 채널로 fallback.
"""

from __future__ import annotations

import logging
import os
from typing import List

from .base import Channel, ChannelResult

log = logging.getLogger("mind_care_emergency.channels.fcm")


class FCMChannel(Channel):
    name = "fcm"

    def __init__(self, credentials_path: str | None = None,
                 default_lang: str = "ko"):
        self.credentials_path = credentials_path or os.environ.get(
            "FIREBASE_CRED_PATH")
        self.default_lang = default_lang
        self._app = None
        self._import_error: Exception | None = None
        self._init_app()

    def _init_app(self) -> None:
        if not self.credentials_path or not os.path.isfile(self.credentials_path):
            return
        try:
            import firebase_admin                       # type: ignore
            from firebase_admin import credentials       # type: ignore
        except ImportError as exc:
            self._import_error = exc
            log.warning("firebase-admin 미설치 — FCM 채널 비활성")
            return
        if not firebase_admin._apps:
            self._app = firebase_admin.initialize_app(
                credentials.Certificate(self.credentials_path))
        else:
            self._app = firebase_admin.get_app()

    def available(self) -> bool:
        return self._app is not None and self._import_error is None

    def send(self, alert: dict, guardians: List[dict]) -> ChannelResult:
        if not self.available():
            return ChannelResult(ok=False, detail={},
                                  error="FCM 자격증명 없음")
        from firebase_admin import messaging  # type: ignore

        tokens = [g["fcm_token"] for g in guardians if g.get("fcm_token")]
        if not tokens:
            return ChannelResult(ok=False, detail={},
                                  error="등록된 fcm_token 없음")

        title, body = _format_message(alert)
        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body),
            data={
                "alert_id":  str(alert["alert_id"]),
                "elder_id":  str(alert["elder_id"]),
                "type":      str(alert["type"]),
                "severity":  str(alert["severity"]),
                "ts":        str(alert["ts"]),
                "deeplink":  f"mindcare://alerts/{alert['alert_id']}",
            },
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10"},
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", badge=1)),
            ),
        )
        try:
            resp = messaging.send_each_for_multicast(message)
            ok = resp.success_count > 0
            return ChannelResult(
                ok=ok,
                detail={
                    "success": resp.success_count,
                    "failure": resp.failure_count,
                    "tokens":  len(tokens),
                },
                error=None if ok else "전체 토큰 실패",
            )
        except Exception as exc:
            log.warning("FCM 전송 실패: %s", exc)
            return ChannelResult(ok=False, detail={}, error=str(exc))


def _format_message(alert: dict) -> tuple[str, str]:
    titles = {
        "fall":         "🚨 마음돌봄 응급 알림",
        "panic_word":   "🚨 마음돌봄 응급 알림",
        "long_idle":    "⚠️ 마음돌봄 활동 알림",
        "false_alarm":  "✅ 마음돌봄 false alarm",
    }
    bodies = {
        "fall":         "어르신께서 낙상 후 응답이 없습니다.",
        "panic_word":   "어르신이 도움을 요청하셨습니다.",
        "long_idle":    "오랫동안 활동이 감지되지 않았습니다.",
        "false_alarm":  "낙상 의심 후 본인 응답으로 정상 복귀.",
    }
    t = alert.get("type", "fall")
    return titles.get(t, "마음돌봄 알림"), bodies.get(t, "확인이 필요합니다.")
