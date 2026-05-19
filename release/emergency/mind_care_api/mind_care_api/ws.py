"""ws.py — WebSocket /api/v1/stream 브로드캐스터.

ROS 콜백 (background thread) 이 `Broadcaster.publish_threadsafe()` 호출 →
asyncio.Queue 로 전달 → 모든 connected client 로 push.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

from fastapi import WebSocket

log = logging.getLogger("mind_care_api.ws")


class Broadcaster:
    """asyncio loop 위에서 동작. ROS thread → loop 로 cross-thread 전달."""

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ------------------------------------------------------------------
    # 연결 관리
    # ------------------------------------------------------------------
    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    # ------------------------------------------------------------------
    # 브로드캐스트
    # ------------------------------------------------------------------
    async def _broadcast(self, message: Dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        dead = []
        for c in clients:
            try:
                await c.send_json(message)
            except Exception as exc:                   # pragma: no cover
                log.debug("ws send 실패 → drop: %s", exc)
                dead.append(c)
        if dead:
            async with self._lock:
                for c in dead:
                    self._clients.discard(c)

    async def publish(self, type_: str, data: Dict[str, Any]) -> None:
        await self._broadcast({"type": type_, "data": data})

    def publish_threadsafe(self, type_: str, data: Dict[str, Any]) -> None:
        """ROS 콜백 (다른 thread) 에서 안전하게 부르는 진입점."""
        if self._loop is None:
            log.warning("Broadcaster.loop 미연결 — 메시지 드롭")
            return
        asyncio.run_coroutine_threadsafe(self.publish(type_, data), self._loop)
