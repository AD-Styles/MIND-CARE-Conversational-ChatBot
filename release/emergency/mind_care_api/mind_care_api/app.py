"""app.py — FastAPI 앱 팩토리. uvicorn 이 import 한다.

    uvicorn mind_care_api.app:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .auth import APIKeyAuth, _digest
from .db import DB, Guardian as GuardianRow
from .ros_bridge import ROSBridge
from .routes import alerts, guardians, reports, status
from .schemas import Health
from .ws import Broadcaster

log = logging.getLogger("mind_care_api.app")


def create_app(
    db_path: str | None = None,
    elder_id: str = "elder_01",
    dev_open: bool | None = None,
) -> FastAPI:
    db_path = db_path or os.environ.get(
        "MIND_CARE_DB",
        str(Path.home() / "마음돌봄" / "release" / "emergency"
            / "state" / "mindcare.db"),
    )
    if dev_open is None:
        dev_open = os.environ.get("MIND_CARE_DEV_OPEN", "0") == "1"

    db = DB(db_path)
    bc = Broadcaster()
    bridge = ROSBridge(db, bc, elder_id=elder_id)
    auth = APIKeyAuth(db, dev_open=dev_open)

    app = FastAPI(
        title="마음돌봄 API",
        version="0.1.0",
        description=(
            "독거노인용 멀티모달 HRI 헬스케어 챗봇 — 보호자 모바일 앱 게이트웨이.\n"
            "WebSocket 스트림: GET /api/v1/stream"
        ),
    )

    # CORS — 모바일 앱 / 웹 대시보드 대응
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("MIND_CARE_CORS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 라우터
    app.include_router(status.get_router(bridge, auth))
    app.include_router(alerts.get_router(db, bridge, auth))
    app.include_router(guardians.get_router(db, auth))
    app.include_router(reports.get_router(db, auth))

    # 시연용 HTML WebSocket 클라이언트 (디렉토리 존재 시에만 마운트)
    demo_dir = Path(__file__).resolve().parent.parent / "demo_client"
    if demo_dir.is_dir():
        app.mount("/demo", StaticFiles(directory=str(demo_dir), html=True), name="demo")

    # ------------------------------------------------------------------
    # 헬스체크
    # ------------------------------------------------------------------
    @app.get("/api/v1/health", response_model=Health)
    async def health() -> Health:
        ros_alive = bridge._node is not None
        try:
            with db.session() as s:
                s.execute(text("SELECT 1"))
            db_alive = True
        except Exception:
            db_alive = False
        with db.session() as s:
            pending = s.execute(
                text("SELECT COUNT(*) FROM alert_deliveries WHERE status='pending'")
            ).scalar_one()
        return Health(status=("ok" if ros_alive and db_alive else "degraded"),
                      ros_alive=ros_alive, db_alive=db_alive,
                      queue_pending=int(pending))

    # ------------------------------------------------------------------
    # WebSocket /api/v1/stream
    # ------------------------------------------------------------------
    @app.websocket("/api/v1/stream")
    async def stream(ws: WebSocket,
                     elder_id_q: str = Query(default=elder_id, alias="elder_id"),
                     token: str | None = Query(default=None)):
        # query 토큰 인증 (모바일 WS 표준 — 헤더 못 쓰는 경우 대비)
        if not dev_open:
            if not token:
                await ws.close(code=4401)
                return
            with db.session() as s:
                row = s.execute(
                    text("SELECT 1 FROM guardians WHERE api_key_hash=:h LIMIT 1"),
                    {"h": _digest(token)},
                ).first()
            if not row:
                await ws.close(code=4401)
                return

        await bc.connect(ws)
        try:
            # 연결 직후 마지막 status 1회 푸시 (FE 가 즉시 그릴 수 있게)
            await ws.send_json({"type": "status",
                                "data": bridge.node.get_last_status().model_dump(mode="json")})
            while True:
                # 클라이언트 메시지는 무시 (단방향 스트림). keep-alive 만.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await bc.disconnect(ws)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @app.on_event("startup")
    async def _startup() -> None:
        bc.attach_loop(asyncio.get_event_loop())
        bridge.start()
        log.info("API up — db=%s elder=%s dev_open=%s", db_path, elder_id, dev_open)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        bridge.stop()

    return app
