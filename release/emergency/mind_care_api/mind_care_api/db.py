"""db.py — SQLAlchemy 2.x 동기 세션 + 같은 SQLite 파일 (mind_care_emergency 와 공유).

스키마 정의는 ``mind_care_emergency.alerts_db`` 가 이미 만들어 둔 테이블을 그대로 read.
별도 ORM 모델로 다시 선언하지만 컬럼 이름은 정확히 일치.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import (Column, Float, ForeignKey, Index, Integer, String,
                        create_engine)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Alert(Base):
    __tablename__ = "alerts"
    alert_id     = Column(String, primary_key=True)
    elder_id     = Column(String, nullable=False, index=True)
    ts           = Column(Float, nullable=False)
    type         = Column(String, nullable=False)
    severity     = Column(String, nullable=False)
    status       = Column(String, nullable=False, default="raised")
    context_json = Column(String, nullable=False, default="{}")
    acked_by     = Column(Integer)
    acked_at     = Column(Float)


class Delivery(Base):
    __tablename__ = "alert_deliveries"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    alert_id      = Column(String, ForeignKey("alerts.alert_id"), nullable=False, index=True)
    channel       = Column(String, nullable=False)
    status        = Column(String, nullable=False, default="pending")
    retry         = Column(Integer, nullable=False, default=0)
    last_attempt  = Column(Float)
    next_attempt  = Column(Float)
    response_json = Column(String, default="{}")


class Guardian(Base):
    __tablename__ = "guardians"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    elder_id     = Column(String, nullable=False, index=True)
    name         = Column(String, nullable=False)
    phone        = Column(String)
    fcm_token    = Column(String)
    role         = Column(String, nullable=False, default="primary")
    lang         = Column(String, nullable=False, default="ko")
    api_key_hash = Column(String)
    created_at   = Column(Float, nullable=False)


class Event(Base):
    __tablename__ = "events"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    elder_id     = Column(String, nullable=False)
    ts           = Column(Float, nullable=False)
    type         = Column(String, nullable=False)
    payload_json = Column(String, nullable=False)


Index("idx_events_elder_ts", Event.elder_id, Event.ts)


# ----------------------------------------------------------------------
# 엔진 + 세션
# ----------------------------------------------------------------------
class DB:
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path}",
            future=True, pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )
        # 테이블은 mind_care_emergency 가 만들지만 단독 기동도 가능하도록 보장
        Base.metadata.create_all(self.engine)
        self._maker = sessionmaker(bind=self.engine, expire_on_commit=False,
                                    autoflush=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._maker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
