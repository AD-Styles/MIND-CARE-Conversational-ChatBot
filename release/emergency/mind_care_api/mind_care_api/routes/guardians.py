"""/api/v1/guardians — 등록/조회/삭제."""

from __future__ import annotations

import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, delete

from ..auth import issue_api_key
from ..db import DB, Guardian as GuardianRow
from ..schemas import Guardian, GuardianCreate


def get_router(db: DB, auth_dep) -> APIRouter:
    router = APIRouter(prefix="/api/v1/guardians", tags=["guardians"])

    @router.get("", response_model=List[Guardian],
                dependencies=[Depends(auth_dep)])
    async def list_guardians(
        elder_id: str = Query(...),
    ) -> List[Guardian]:
        with db.session() as s:
            rows = list(s.execute(
                select(GuardianRow).where(GuardianRow.elder_id == elder_id)
            ).scalars())
        return [Guardian(
            id=r.id, elder_id=r.elder_id, name=r.name, phone=r.phone,
            fcm_token=r.fcm_token, role=r.role, lang=r.lang,
            created_at=r.created_at, api_key=None,
        ) for r in rows]

    @router.post("", response_model=Guardian, status_code=status.HTTP_201_CREATED)
    async def create_guardian(body: GuardianCreate) -> Guardian:
        """등록은 인증 없음 (FE 가 사용자 회원가입 흐름에서 호출).
        평문 api_key 는 응답에 1회만 노출 — 클라이언트가 안전 보관해야 함.
        """
        plain, h = issue_api_key()
        with db.session() as s:
            row = GuardianRow(
                elder_id=body.elder_id, name=body.name, phone=body.phone,
                fcm_token=body.fcm_token, role=body.role, lang=body.lang,
                api_key_hash=h, created_at=time.time(),
            )
            s.add(row)
            s.flush()
            return Guardian(
                id=row.id, elder_id=row.elder_id, name=row.name, phone=row.phone,
                fcm_token=row.fcm_token, role=row.role, lang=row.lang,
                created_at=row.created_at, api_key=plain,
            )

    @router.delete("/{guardian_id}", status_code=status.HTTP_204_NO_CONTENT,
                   dependencies=[Depends(auth_dep)])
    async def delete_guardian(guardian_id: int):
        with db.session() as s:
            res = s.execute(delete(GuardianRow).where(GuardianRow.id == guardian_id))
            if res.rowcount == 0:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "guardian not found")
        return

    return router
