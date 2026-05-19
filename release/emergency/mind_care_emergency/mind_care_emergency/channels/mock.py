"""mock.py — 시연·개발 시 콘솔에 alert 를 찍기만 함. 항상 success."""

from __future__ import annotations

import json
import logging
from typing import List

from .base import Channel, ChannelResult

log = logging.getLogger("mind_care_emergency.channels.mock")


class MockChannel(Channel):
    name = "mock"

    def send(self, alert: dict, guardians: List[dict]) -> ChannelResult:
        log.info("[MOCK ALERT] %s", json.dumps(alert, ensure_ascii=False))
        for g in guardians:
            log.info("  → guardian %s (%s)", g.get("name"), g.get("phone"))
        return ChannelResult(ok=True, detail={"guardians": len(guardians)})
