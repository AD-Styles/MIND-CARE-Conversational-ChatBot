"""auth.py — X-Api-Key 미들웨어.

보호자 등록 시 1회용 평문 키를 발급(`schemas.Guardian.api_key`), 이후 호출은
헤더 `X-Api-Key: <key>` 로 인증. DB 에는 SHA256 hash 만 저장.

`SECRET_KEY` 환경변수가 있으면 HMAC, 없으면 단순 SHA256.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException, status
from sqlalchemy import select

from .db import DB, Guardian


def _digest(key: str) -> str:
    secret = os.environ.get("MIND_CARE_SECRET_KEY", "")
    if secret:
        return hmac.new(secret.encode(), key.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(key.encode()).hexdigest()


def issue_api_key() -> tuple[str, str]:
    """평문 + hash 둘 다 반환 — 평문은 클라이언트에 1회만, hash 는 DB 에."""
    plain = secrets.token_urlsafe(32)
    return plain, _digest(plain)


class APIKeyAuth:
    """FastAPI dependency. dev_open=True 면 인증 우회."""
    def __init__(self, db: DB, dev_open: bool = False):
        self.db = db
        self.dev_open = dev_open

    async def __call__(
        self,
        x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
    ) -> Guardian:
        if self.dev_open:
            # 시연 시 first guardian 으로 가정 — 실서비스에서는 절대 사용 X
            with self.db.session() as s:
                row = s.execute(select(Guardian)).scalars().first()
                if row is not None:
                    return row
                raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                     "no guardian registered")
        if not x_api_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                 "X-Api-Key required")
        h = _digest(x_api_key)
        with self.db.session() as s:
            row = s.execute(
                select(Guardian).where(Guardian.api_key_hash == h)
            ).scalars().first()
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                 "invalid api key")
        return row
