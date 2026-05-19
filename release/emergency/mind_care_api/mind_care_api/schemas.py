"""schemas.py — FE 와의 JSON 계약 (Pydantic v2).

OpenAPI 자동 생성으로 FE 가 TypeScript 타입 codegen 가능.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# 공통
# ----------------------------------------------------------------------
class Health(BaseModel):
    status: Literal["ok", "degraded", "down"]
    ros_alive: bool
    db_alive: bool
    queue_pending: int = 0


# ----------------------------------------------------------------------
# Status — 어르신 현재 상태 한 장
# ----------------------------------------------------------------------
class EmotionLabel(BaseModel):
    label: str
    confidence: float = Field(ge=0.0, le=1.0)


class Status(BaseModel):
    elder_id: str
    ts: float
    presence: bool
    track_count: int = 0
    fall_detected: bool = False
    fall_confirmed: bool = False
    emotion: Optional[EmotionLabel] = None
    last_speech_ts: Optional[float] = None
    session_state: Literal["NORMAL", "QUERY", "EMERGENCY", "ACKED"] = "NORMAL"


# ----------------------------------------------------------------------
# Alert
# ----------------------------------------------------------------------
class DeliveryItem(BaseModel):
    channel: Literal["fcm", "sms", "buzzer", "mock"]
    status:  Literal["pending", "ok", "failed", "retry", "dropped"]
    ts: Optional[float] = None
    retry: int = 0
    error: Optional[str] = None
    detail: Dict[str, Any] = Field(default_factory=dict)


class Alert(BaseModel):
    alert_id: str
    elder_id: str
    ts: float
    type: Literal["fall", "panic_word", "long_idle", "false_alarm"]
    severity: Literal["critical", "warning", "info"]
    status: Literal["raised", "delivered", "failed", "acked", "resolved"]
    context: Dict[str, Any] = Field(default_factory=dict)
    deliveries: List[DeliveryItem] = Field(default_factory=list)
    acked_by: Optional[int] = None
    acked_at: Optional[float] = None


class AckRequest(BaseModel):
    guardian_id: int
    note: Optional[str] = None


# ----------------------------------------------------------------------
# Guardian
# ----------------------------------------------------------------------
class GuardianCreate(BaseModel):
    elder_id: str
    name: str
    phone: Optional[str] = None
    fcm_token: Optional[str] = None
    role: Literal["primary", "backup"] = "primary"
    lang: str = "ko"


class Guardian(GuardianCreate):
    id: int
    created_at: float
    api_key: Optional[str] = None   # 등록 직후 1회만 평문 반환 (이후엔 hash 만 저장)


# ----------------------------------------------------------------------
# DailyReport — on-demand 집계
# ----------------------------------------------------------------------
class DailyEvent(BaseModel):
    ts: float
    type: str
    severity: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class DailyReport(BaseModel):
    elder_id: str
    date: str          # YYYY-MM-DD
    tz: str = "Asia/Seoul"
    presence_ratio: float = 0.0
    emotion_distribution: Dict[str, float] = Field(default_factory=dict)
    events: List[DailyEvent] = Field(default_factory=list)
    alerts_count: Dict[str, int] = Field(default_factory=dict)
    summary_text: str = ""


# ----------------------------------------------------------------------
# WebSocket envelope — 모든 stream 메시지의 공통 형식
# ----------------------------------------------------------------------
class WSMessage(BaseModel):
    type: Literal["status", "alert", "delivery", "alert_status", "ping"]
    data: Dict[str, Any] = Field(default_factory=dict)
