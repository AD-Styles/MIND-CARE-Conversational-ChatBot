"""alerts_db.py — Dispatcher 의 SQLite 재시도 큐.

Decider 가 발행한 alert 가 들어오면 row 가 만들어지고, 채널별로 status 가 갱신됨.
재시도 백오프는 dispatcher 가 결정하지만, 큐 자체는 DB 만 노출.

스키마는 mind_care_api 의 sqlalchemy 모델과 컬럼이 일치하도록 맞춰 둠 — 같은
DB 파일을 공유하는 게 운영상 이점이 큼 (대시보드가 같은 row 를 본다).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

# 컬럼 — mind_care_api 와 일치 (alembic 안 쓰고 같은 schema 두 곳에서 보장)
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id      TEXT PRIMARY KEY,
    elder_id      TEXT NOT NULL,
    ts            REAL NOT NULL,
    type          TEXT NOT NULL,
    severity      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'raised',
    context_json  TEXT NOT NULL,
    acked_by      INTEGER,
    acked_at      REAL
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id      TEXT NOT NULL,
    channel       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    retry         INTEGER NOT NULL DEFAULT 0,
    last_attempt  REAL,
    next_attempt  REAL,
    response_json TEXT,
    FOREIGN KEY (alert_id) REFERENCES alerts(alert_id)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_pending
    ON alert_deliveries(status, next_attempt);

CREATE TABLE IF NOT EXISTS guardians (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id      TEXT NOT NULL,
    name          TEXT NOT NULL,
    phone         TEXT,
    fcm_token     TEXT,
    role          TEXT NOT NULL DEFAULT 'primary',
    lang          TEXT NOT NULL DEFAULT 'ko',
    api_key_hash  TEXT,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    elder_id      TEXT NOT NULL,
    ts            REAL NOT NULL,
    type          TEXT NOT NULL,
    payload_json  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_elder_ts ON events(elder_id, ts);
"""


class AlertsDB:
    """thread-safe 한 SQLite wrapper. dispatcher 와 ros_bridge 에서 공유.

    - SQLite 의 동시성 — `check_same_thread=False` + 명시적 Lock.
    - 매 호출 commit (작은 부하라 OK).
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False,
                                     isolation_level=None)   # autocommit
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA_SQL)

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    # ------------------------------------------------------------------
    # alert
    # ------------------------------------------------------------------
    def insert_alert(self, alert: dict, channels: List[str]) -> None:
        """alert + 각 채널 pending row 를 atomic 으로 생성."""
        with self.cursor() as cur:
            cur.execute("BEGIN")
            try:
                cur.execute(
                    "INSERT OR REPLACE INTO alerts "
                    "(alert_id, elder_id, ts, type, severity, status, context_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (alert["alert_id"], alert["elder_id"], alert["ts"],
                     alert["type"], alert["severity"], alert["status"],
                     json.dumps(alert.get("context", {}), ensure_ascii=False)),
                )
                now = time.time()
                for ch in channels:
                    cur.execute(
                        "INSERT INTO alert_deliveries "
                        "(alert_id, channel, status, retry, next_attempt) "
                        "VALUES (?, ?, 'pending', 0, ?)",
                        (alert["alert_id"], ch, now),
                    )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def update_alert_status(self, alert_id: str, status: str,
                             acked_by: Optional[int] = None,
                             acked_at: Optional[float] = None) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE alerts SET status=?, acked_by=?, acked_at=? "
                "WHERE alert_id=?",
                (status, acked_by, acked_at, alert_id),
            )

    # ------------------------------------------------------------------
    # delivery
    # ------------------------------------------------------------------
    def claim_pending(self, max_rows: int = 16) -> List[dict]:
        """다음 시도가 가능한(now ≥ next_attempt) pending row 를 가져옴.

        클레임만 하고 status 변경은 안 함 — 시도 결과로 update.
        """
        now = time.time()
        with self.cursor() as cur:
            cur.execute(
                "SELECT id, alert_id, channel, retry "
                "FROM alert_deliveries "
                "WHERE status='pending' AND (next_attempt IS NULL OR next_attempt <= ?) "
                "ORDER BY id ASC LIMIT ?",
                (now, max_rows),
            )
            rows = cur.fetchall()
        return [{"id": r[0], "alert_id": r[1], "channel": r[2], "retry": r[3]}
                for r in rows]

    def mark_delivery(self, delivery_id: int, status: str, *,
                      retry: int, response: Optional[dict],
                      next_attempt: Optional[float]) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE alert_deliveries SET status=?, retry=?, last_attempt=?, "
                "next_attempt=?, response_json=? WHERE id=?",
                (status, retry, time.time(), next_attempt,
                 json.dumps(response or {}, ensure_ascii=False), delivery_id),
            )

    def fetch_alert_payload(self, alert_id: str) -> Optional[dict]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT alert_id, elder_id, ts, type, severity, status, context_json "
                "FROM alerts WHERE alert_id=?", (alert_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "alert_id": row[0], "elder_id": row[1], "ts": row[2],
            "type": row[3], "severity": row[4], "status": row[5],
            "context": json.loads(row[6]),
        }

    # ------------------------------------------------------------------
    # event log (ros_bridge 가 모든 ROS 이벤트를 누적)
    # ------------------------------------------------------------------
    def append_event(self, elder_id: str, type_: str,
                      payload: dict, ts: Optional[float] = None) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO events (elder_id, ts, type, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (elder_id, ts or time.time(), type_,
                 json.dumps(payload, ensure_ascii=False)),
            )
