"""채널 공통 추상 — 동기 send 1번만 담당. 재시도/백오프는 dispatcher 가."""

from __future__ import annotations

import dataclasses as dc
from abc import ABC, abstractmethod
from typing import List, Optional


@dc.dataclass
class ChannelResult:
    ok: bool
    detail: dict       # 보호자별 성공/실패, error 메시지 등
    error: Optional[str] = None


class Channel(ABC):
    """모든 채널은 send() 한 번 동기 호출 + 결과 반환.

    `available()` 가 False 면 dispatcher 가 채널을 큐에 안 넣음.
    """
    name: str = "base"

    def available(self) -> bool:
        return True

    @abstractmethod
    def send(self, alert: dict, guardians: List[dict]) -> ChannelResult: ...
