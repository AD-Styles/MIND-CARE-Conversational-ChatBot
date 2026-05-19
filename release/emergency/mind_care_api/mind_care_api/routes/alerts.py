"""/api/v1/alerts — 이력 조회 + 상세 + ACK."""

from __future__ import annotations

import json
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select

from ..db import DB, Alert as AlertRow, Delivery
from ..ros_bridge import ROSBridge
from ..schemas import Alert, AckRequest, DeliveryItem


def get_router(db: DB, bridge: ROSBridge, auth_dep) -> APIRouter:
    router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

    def _row_to_alert(row: AlertRow, deliveries: List[Delivery]) -> Alert:
        return Alert(
            alert_id=row.alert_id,
            elder_id=row.elder_id,
            ts=row.ts,
            type=row.type,
            severity=row.severity,
            status=row.status,
            context=json.loads(row.context_json or "{}"),
            deliveries=[
                DeliveryItem(
                    channel=d.channel,
                    status=d.status,
                    ts=d.last_attempt,
                    retry=d.retry,
                    detail=json.loads(d.response_json or "{}"),
                ) for d in deliveries
            ],
            acked_by=row.acked_by,
            acked_at=row.acked_at,
        )

    @router.get("", response_model=List[Alert],
                dependencies=[Depends(auth_dep)])
    async def list_alerts(
        elder_id: Optional[str] = None,
        since:    Optional[float] = None,
        status_:  Optional[str] = Query(default=None, alias="status"),
        limit:    int = Query(default=50, ge=1, le=500),
    ) -> List[Alert]:
        with db.session() as s:
            stmt = select(AlertRow).order_by(desc(AlertRow.ts)).limit(limit)
            if elder_id:
                stmt = stmt.where(AlertRow.elder_id == elder_id)
            if since is not None:
                stmt = stmt.where(AlertRow.ts >= since)
            if status_:
                stmt = stmt.where(AlertRow.status == status_)
            rows = list(s.execute(stmt).scalars())

            results: List[Alert] = []
            for row in rows:
                ds = list(s.execute(
                    select(Delivery).where(Delivery.alert_id == row.alert_id)
                ).scalars())
                results.append(_row_to_alert(row, ds))
        return results

    @router.get("/{alert_id}", response_model=Alert,
                dependencies=[Depends(auth_dep)])
    async def get_alert(alert_id: str) -> Alert:
        with db.session() as s:
            row = s.get(AlertRow, alert_id)
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
            ds = list(s.execute(
                select(Delivery).where(Delivery.alert_id == alert_id)
            ).scalars())
            return _row_to_alert(row, ds)

    @router.post("/{alert_id}/ack", dependencies=[Depends(auth_dep)])
    async def ack_alert(alert_id: str, body: AckRequest):
        now = time.time()
        with db.session() as s:
            row = s.get(AlertRow, alert_id)
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
            row.status = "acked"
            row.acked_by = body.guardian_id
            row.acked_at = now
        # ROS 측에 ACK 이벤트 — Decider 의 EMERGENCY → ACKED 전이를 일으킴
        bridge.node.publish_ack(alert_id, body.guardian_id, body.note)
        return {"ok": True, "alert_id": alert_id, "acked_at": now}

    return router
