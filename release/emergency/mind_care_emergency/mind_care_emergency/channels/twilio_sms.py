"""twilio_sms.py — Twilio SMS.

자격증명: env `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_PHONE`.
"""

from __future__ import annotations

import logging
import os
from typing import List

from .base import Channel, ChannelResult

log = logging.getLogger("mind_care_emergency.channels.twilio")


class TwilioSMSChannel(Channel):
    name = "sms"

    def __init__(self) -> None:
        self.sid    = os.environ.get("TWILIO_ACCOUNT_SID")
        self.token  = os.environ.get("TWILIO_AUTH_TOKEN")
        self.from_  = os.environ.get("TWILIO_FROM_PHONE")
        self._client = None
        if self.sid and self.token and self.from_:
            try:
                from twilio.rest import Client  # type: ignore
                self._client = Client(self.sid, self.token)
            except ImportError:
                log.warning("twilio 미설치 — SMS 채널 비활성")

    def available(self) -> bool:
        return self._client is not None

    def send(self, alert: dict, guardians: List[dict]) -> ChannelResult:
        if not self.available():
            return ChannelResult(ok=False, detail={}, error="Twilio 자격증명 없음")

        body = _format_sms(alert)
        targets = [g["phone"] for g in guardians if g.get("phone")]
        if not targets:
            return ChannelResult(ok=False, detail={}, error="phone 없음")

        ok_n, fail_n, errors = 0, 0, []
        for phone in targets:
            try:
                self._client.messages.create(to=phone, from_=self.from_, body=body)
                ok_n += 1
            except Exception as exc:                # pragma: no cover
                fail_n += 1
                errors.append(f"{phone}: {exc}")
        return ChannelResult(
            ok=ok_n > 0,
            detail={"success": ok_n, "failure": fail_n, "errors": errors},
            error=None if ok_n else "전체 SMS 실패",
        )


def _format_sms(alert: dict) -> str:
    t = alert.get("type", "fall")
    return {
        "fall":       "[마음돌봄] 어르신 낙상 후 응답 없음. 즉시 확인 부탁드립니다.",
        "panic_word": "[마음돌봄] 어르신이 도움을 요청하셨습니다.",
        "long_idle":  "[마음돌봄] 오래 활동이 감지되지 않습니다.",
    }.get(t, "[마음돌봄] 알림이 도착했습니다.")
