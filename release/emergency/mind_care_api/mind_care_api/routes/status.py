"""GET /api/v1/elders/{elder_id}/status — 캐시된 최신 상태."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..ros_bridge import ROSBridge
from ..schemas import Status

router = APIRouter(prefix="/api/v1/elders", tags=["status"])


def get_router(bridge: ROSBridge, auth_dep) -> APIRouter:

    @router.get("/{elder_id}/status", response_model=Status,
                dependencies=[Depends(auth_dep)])
    async def get_status(elder_id: str) -> Status:
        st = bridge.node.get_last_status()
        if st.elder_id != elder_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown elder_id")
        return st

    return router
