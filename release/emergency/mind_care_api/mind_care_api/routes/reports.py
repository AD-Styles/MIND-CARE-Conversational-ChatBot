"""/api/v1/elders/{elder_id}/daily-report — 하루치 정서/활동/이벤트 집계 (on-demand)."""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, and_

from ..db import DB, Event, Alert as AlertRow
from ..schemas import DailyEvent, DailyReport


def _day_bounds(date: str, tz: str = "Asia/Seoul") -> tuple[float, float]:
    """YYYY-MM-DD → (start_ts, end_ts) UTC seconds."""
    try:
        d = dt.datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "date must be YYYY-MM-DD")
    # 단순화 — UTC 기준 집계 (FE 는 tz 보정해서 표시).
    start = dt.datetime.combine(d, dt.time.min, tzinfo=dt.timezone.utc)
    end   = start + dt.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def get_router(db: DB, auth_dep) -> APIRouter:
    router = APIRouter(prefix="/api/v1/elders", tags=["reports"])

    @router.get("/{elder_id}/daily-report", response_model=DailyReport,
                dependencies=[Depends(auth_dep)])
    async def daily_report(
        elder_id: str,
        date: str = Query(..., description="YYYY-MM-DD"),
    ) -> DailyReport:
        start, end = _day_bounds(date)

        with db.session() as s:
            evs = list(s.execute(
                select(Event).where(and_(
                    Event.elder_id == elder_id,
                    Event.ts >= start, Event.ts < end,
                )).order_by(Event.ts)
            ).scalars())
            alerts = list(s.execute(
                select(AlertRow).where(and_(
                    AlertRow.elder_id == elder_id,
                    AlertRow.ts >= start, AlertRow.ts < end,
                ))
            ).scalars())

        # presence_ratio — events 중 vision_state 이벤트의 presence true 비율
        # (현재 ros_bridge 는 status 만 캐시하고 events 에 vision_state 안 누적함 —
        #  운영 시 이 부분 ros_bridge 에서 이벤트도 누적하면 정확도 ↑)
        presence_ratio = 0.0   # placeholder — 향후 events 로 계산

        emo_counter: Counter = Counter()
        out_events: List[DailyEvent] = []
        for e in evs:
            try:
                payload = json.loads(e.payload_json)
            except json.JSONDecodeError:
                payload = {}
            if e.type == "emotion_sample":
                lbl = payload.get("label")
                if lbl:
                    emo_counter[lbl] += 1
            out_events.append(DailyEvent(
                ts=e.ts, type=e.type,
                severity=payload.get("severity"),
                payload=payload,
            ))

        total_emo = sum(emo_counter.values()) or 1
        emo_dist = {k: v / total_emo for k, v in emo_counter.items()}

        sev_count: Counter = Counter()
        for a in alerts:
            sev_count[a.severity] += 1

        critical_n = sev_count.get("critical", 0)
        warning_n  = sev_count.get("warning", 0)
        info_n     = sev_count.get("info", 0)
        summary = (
            f"{date} 하루 — 응급 알림 {critical_n}건, "
            f"경고 {warning_n}건, false alarm {info_n}건."
        )

        return DailyReport(
            elder_id=elder_id, date=date,
            presence_ratio=presence_ratio,
            emotion_distribution=emo_dist,
            events=out_events,
            alerts_count={"critical": critical_n,
                           "warning": warning_n, "info": info_n},
            summary_text=summary,
        )

    return router
