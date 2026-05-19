"""decider_states.py — Phase 5 응급 상태 머신 (순수 Python, ROS 비의존).

이 모듈만 따로 import 해서 pytest 로 단위 테스트 가능. ROS 콜백은
`emergency_decider_node` 가 wrapping 한다.

상태 전이:

  NORMAL ──fall_detected──> QUERY  (dialogue 능동 발화 트리거)
  NORMAL ──panic_word────> EMERGENCY (즉시)
  NORMAL ──long_idle─────> QUERY

  QUERY  ──"괜찮아요"────> NORMAL (false_alarm 로그)
  QUERY  ──"도와줘"──────> EMERGENCY
  QUERY  ──fall_confirmed> EMERGENCY
  QUERY  ──30s 타임아웃──> EMERGENCY

  EMERGENCY ──ack_received──────────> ACKED
  EMERGENCY ──auto_clear(ACK 없이 N초)> NORMAL
  ACKED     ──cooldown 60s ──────────> NORMAL

`decide()` 메서드는 외부 입력(이벤트) 을 받아 다음 상태와 발행할 효과를 반환.
효과(`Effect`) 는 ROS 노드가 토픽으로 발행할 페이로드.
"""

from __future__ import annotations

import dataclasses as dc
import enum
import time
import uuid
from typing import List, Optional


# ----------------------------------------------------------------------
# 상태 / 이벤트 / 효과
# ----------------------------------------------------------------------
class State(str, enum.Enum):
    NORMAL    = "NORMAL"
    QUERY     = "QUERY"
    EMERGENCY = "EMERGENCY"
    ACKED     = "ACKED"


class EventType(str, enum.Enum):
    FALL_DETECTED   = "fall_detected"
    FALL_CONFIRMED  = "fall_confirmed"
    USER_OK         = "user_ok"          # "괜찮아요"
    PANIC_WORD      = "panic_word"       # "도와줘", "아파"
    LONG_IDLE       = "long_idle"        # 6h 부동
    QUERY_TIMEOUT   = "query_timeout"
    ACK_RECEIVED    = "ack_received"     # 보호자 ACK
    COOLDOWN_DONE   = "cooldown_done"
    TICK            = "tick"             # 매 0.5s — 타이머 평가용


class AlertType(str, enum.Enum):
    FALL        = "fall"
    PANIC_WORD  = "panic_word"
    LONG_IDLE   = "long_idle"
    FALSE_ALARM = "false_alarm"


@dc.dataclass(frozen=True)
class Event:
    """외부 입력. ts 는 server clock (time.time())."""
    type: EventType
    ts: float
    payload: dict = dc.field(default_factory=dict)


@dc.dataclass
class Effect:
    """상태 머신이 ROS 노드에 시키는 일."""
    publish_alert: Optional[dict] = None   # /emergency/alert 페이로드 (or None)
    proactive_speech: Optional[str] = None # /dialogue/proactive_speech 텍스트
    log: Optional[str] = None              # 사람이 읽을 로그 한 줄


# ----------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------
@dc.dataclass
class DeciderConfig:
    elder_id: str = "elder_01"
    query_timeout_s: float = 30.0
    cooldown_s: float = 60.0
    # EMERGENCY 진입 후 ACK 가 이 시간 안에 안 오면 NORMAL 로 자동 복귀.
    # (보호자 ACK 경로가 없는 시연 환경에서 decider 가 EMERGENCY 에 고착돼
    #  후속 응급이 dedupe 되는 것을 방지)
    emergency_auto_clear_s: float = 60.0
    long_idle_threshold_s: float = 6 * 3600.0
    query_speech: str = "괜찮으세요?"
    long_idle_speech: str = "오래 조용하셨네요. 거기 계세요?"


# ----------------------------------------------------------------------
# 상태 머신
# ----------------------------------------------------------------------
@dc.dataclass
class DeciderStateMachine:
    cfg: DeciderConfig = dc.field(default_factory=DeciderConfig)
    state: State = State.NORMAL
    state_entered_at: float = dc.field(default_factory=time.time)
    last_alert: Optional[dict] = None
    # 가장 최근 이벤트들 — alert context 로 첨부
    recent_context: dict = dc.field(default_factory=dict)

    # ------------------------------------------------------------------
    # 메인 디스패처
    # ------------------------------------------------------------------
    def decide(self, ev: Event) -> List[Effect]:
        """이벤트 1개에 대해 효과 목록 반환. 다중 효과 가능 (예: 알림 + 발화)."""
        # 모든 이벤트는 컨텍스트 누적
        if ev.payload:
            self.recent_context.update(ev.payload)

        if self.state == State.NORMAL:
            return self._on_normal(ev)
        if self.state == State.QUERY:
            return self._on_query(ev)
        if self.state == State.EMERGENCY:
            return self._on_emergency(ev)
        if self.state == State.ACKED:
            return self._on_acked(ev)
        return []

    # ------------------------------------------------------------------
    # 상태별 핸들러
    # ------------------------------------------------------------------
    def _on_normal(self, ev: Event) -> List[Effect]:
        if ev.type == EventType.FALL_DETECTED:
            self._goto(State.QUERY, ev.ts)
            return [Effect(
                proactive_speech=self.cfg.query_speech,
                log=f"NORMAL→QUERY (fall_detected); 30s 타이머 시작",
            )]

        if ev.type == EventType.PANIC_WORD:
            return self._raise_emergency(AlertType.PANIC_WORD, ev,
                                         "NORMAL→EMERGENCY (panic_word)")

        if ev.type == EventType.LONG_IDLE:
            self._goto(State.QUERY, ev.ts)
            return [Effect(
                proactive_speech=self.cfg.long_idle_speech,
                log=f"NORMAL→QUERY (long_idle); 30s 타이머 시작",
            )]

        # 그 외 이벤트 (TICK, FALL_CONFIRMED 단독 등) 은 무시 — fall_detected
        # 가 먼저 들어와 QUERY 진입한 뒤에야 fall_confirmed 가 의미를 가짐.
        return []

    def _on_query(self, ev: Event) -> List[Effect]:
        if ev.type == EventType.USER_OK:
            self._goto(State.NORMAL, ev.ts)
            # 보호자 대시보드에 false_alarm 로그용 alert 발행 (severity=info)
            alert = self._build_alert(AlertType.FALSE_ALARM, ev,
                                      severity="info")
            return [Effect(
                publish_alert=alert,
                log="QUERY→NORMAL (user_ok); false_alarm 로그",
            )]

        if ev.type in (EventType.PANIC_WORD, EventType.FALL_CONFIRMED):
            atype = (AlertType.PANIC_WORD if ev.type == EventType.PANIC_WORD
                     else AlertType.FALL)
            return self._raise_emergency(atype, ev,
                                         f"QUERY→EMERGENCY ({ev.type.value})")

        if ev.type == EventType.QUERY_TIMEOUT:
            return self._raise_emergency(AlertType.FALL, ev,
                                         "QUERY→EMERGENCY (timeout 30s)")

        if ev.type == EventType.TICK:
            elapsed = ev.ts - self.state_entered_at
            if elapsed >= self.cfg.query_timeout_s:
                # 자동 timeout 변환 — 호출자가 별도 QUERY_TIMEOUT 안 보내도 안전
                return self._raise_emergency(AlertType.FALL, ev,
                                             f"QUERY→EMERGENCY (auto-timeout {elapsed:.1f}s)")
        return []

    def _on_emergency(self, ev: Event) -> List[Effect]:
        if ev.type == EventType.ACK_RECEIVED:
            self._goto(State.ACKED, ev.ts)
            return [Effect(log="EMERGENCY→ACKED (보호자 응답)")]
        if ev.type == EventType.TICK:
            elapsed = ev.ts - self.state_entered_at
            if elapsed >= self.cfg.emergency_auto_clear_s:
                # ACK 미수신 — 자동 복귀해 다음 응급을 받을 수 있게 re-arm
                self._goto(State.NORMAL, ev.ts)
                return [Effect(
                    log=f"EMERGENCY→NORMAL (auto-clear {elapsed:.1f}s, ACK 미수신)")]
        # EMERGENCY 진입 후 새 이벤트가 들어와도 한 번 발행한 alert 는 dedupe.
        return []

    def _on_acked(self, ev: Event) -> List[Effect]:
        if ev.type == EventType.COOLDOWN_DONE:
            self._goto(State.NORMAL, ev.ts)
            return [Effect(log="ACKED→NORMAL (cooldown 60s)")]
        if ev.type == EventType.TICK:
            elapsed = ev.ts - self.state_entered_at
            if elapsed >= self.cfg.cooldown_s:
                self._goto(State.NORMAL, ev.ts)
                return [Effect(log=f"ACKED→NORMAL (auto-cooldown {elapsed:.1f}s)")]
        return []

    # ------------------------------------------------------------------
    # 보조
    # ------------------------------------------------------------------
    def _goto(self, new_state: State, ts: float) -> None:
        self.state = new_state
        self.state_entered_at = ts

    def _raise_emergency(self, atype: AlertType, ev: Event,
                         log_msg: str) -> List[Effect]:
        alert = self._build_alert(atype, ev, severity="critical")
        self.last_alert = alert
        self._goto(State.EMERGENCY, ev.ts)
        return [Effect(publish_alert=alert, log=log_msg)]

    def _build_alert(self, atype: AlertType, ev: Event, *,
                     severity: str) -> dict:
        ctx = dict(self.recent_context)
        ctx.update({
            "duration_no_response_s": (ev.ts - self.state_entered_at
                                       if self.state == State.QUERY else 0.0),
        })
        return {
            "alert_id": str(uuid.uuid4()),
            "elder_id": self.cfg.elder_id,
            "ts": ev.ts,
            "type": atype.value,
            "severity": severity,
            "status": "raised",
            "context": ctx,
        }
