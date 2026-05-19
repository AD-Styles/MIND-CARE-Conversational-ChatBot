"""decider_states 단위 테스트 — ROS 없이 pytest 로 돌릴 수 있다.

    pytest release/emergency/mind_care_emergency/tests/test_decider_states.py -v
"""
from __future__ import annotations

import pytest

from mind_care_emergency.decider_states import (
    DeciderConfig, DeciderStateMachine, Event, EventType, State,
)


@pytest.fixture
def sm():
    return DeciderStateMachine(cfg=DeciderConfig(
        elder_id="test_elder", query_timeout_s=30.0, cooldown_s=60.0,
    ))


def _ev(t: EventType, ts: float = 1000.0, **payload):
    return Event(type=t, ts=ts, payload=payload)


# ----------------------------------------------------------------------
# 시나리오 1 — 거실 낙상 + 무응답 → EMERGENCY
# ----------------------------------------------------------------------
def test_fall_then_timeout(sm):
    effs = sm.decide(_ev(EventType.FALL_DETECTED, ts=100.0))
    assert sm.state == State.QUERY
    assert effs[0].proactive_speech == "괜찮으세요?"

    # 30 초 안에 응답 없음 → timeout 자동 변환
    effs = sm.decide(_ev(EventType.TICK, ts=131.0))
    assert sm.state == State.EMERGENCY
    assert effs[0].publish_alert["type"] == "fall"
    assert effs[0].publish_alert["severity"] == "critical"


# ----------------------------------------------------------------------
# 시나리오 2 — 낙상 후 의식 유지 ("괜찮아요")
# ----------------------------------------------------------------------
def test_fall_then_user_ok(sm):
    sm.decide(_ev(EventType.FALL_DETECTED, ts=100.0))
    effs = sm.decide(_ev(EventType.USER_OK, ts=110.0))
    assert sm.state == State.NORMAL
    # false_alarm 로그용 alert (severity=info)
    assert effs[0].publish_alert["severity"] == "info"
    assert effs[0].publish_alert["type"] == "false_alarm"


# ----------------------------------------------------------------------
# 시나리오 3 — fall_confirmed 가 30 s 전 들어오면 즉시 EMERGENCY
# ----------------------------------------------------------------------
def test_fall_confirmed_short_circuit(sm):
    sm.decide(_ev(EventType.FALL_DETECTED, ts=100.0))
    effs = sm.decide(_ev(EventType.FALL_CONFIRMED, ts=108.0))
    assert sm.state == State.EMERGENCY
    assert effs[0].publish_alert["type"] == "fall"


# ----------------------------------------------------------------------
# 시나리오 4 — 응급어 (NORMAL 직접 → EMERGENCY)
# ----------------------------------------------------------------------
def test_panic_word_direct(sm):
    effs = sm.decide(_ev(EventType.PANIC_WORD, ts=200.0,
                          user_quote="도와줘"))
    assert sm.state == State.EMERGENCY
    assert effs[0].publish_alert["type"] == "panic_word"
    assert effs[0].publish_alert["context"]["user_quote"] == "도와줘"


# ----------------------------------------------------------------------
# 시나리오 5 — long_idle → QUERY → 무응답 → EMERGENCY
# ----------------------------------------------------------------------
def test_long_idle_path(sm):
    sm.decide(_ev(EventType.LONG_IDLE, ts=300.0, idle_s=21600.0))
    assert sm.state == State.QUERY
    sm.decide(_ev(EventType.TICK, ts=331.0))
    assert sm.state == State.EMERGENCY
    assert sm.last_alert["type"] == "fall"   # long_idle path 도 fall 로 보고됨


# ----------------------------------------------------------------------
# 시나리오 6 — EMERGENCY → ACK → cooldown → NORMAL
# ----------------------------------------------------------------------
def test_emergency_ack_cooldown(sm):
    sm.decide(_ev(EventType.PANIC_WORD, ts=400.0))
    assert sm.state == State.EMERGENCY
    sm.decide(_ev(EventType.ACK_RECEIVED, ts=405.0))
    assert sm.state == State.ACKED
    sm.decide(_ev(EventType.TICK, ts=470.0))   # cooldown 60s 후
    assert sm.state == State.NORMAL


# ----------------------------------------------------------------------
# 시나리오 7 — TV 음성 무시 (speaker_match=False 인 panic_word 는 노드 측
#               에서 차단되므로 머신엔 안 들어옴; 여기서는 정상 전이만 확인)
# ----------------------------------------------------------------------
def test_emergency_dedupe(sm):
    """EMERGENCY 진입 후 추가 panic_word 가 들어와도 중복 발행 안 됨."""
    sm.decide(_ev(EventType.PANIC_WORD, ts=500.0))
    effs2 = sm.decide(_ev(EventType.PANIC_WORD, ts=501.0))
    assert effs2 == []   # 효과 없음 — 호출자도 dedupe 보장


# ----------------------------------------------------------------------
# 시나리오 8 — EMERGENCY → ACK 없이 자동복귀(auto-clear) → NORMAL → re-arm
# ----------------------------------------------------------------------
def test_emergency_auto_clear(sm):
    """ACK 가 안 와도 emergency_auto_clear_s(기본 60s) 후 NORMAL 복귀,
    이후 새 panic_word 가 정상적으로 다시 EMERGENCY 를 발동한다."""
    sm.decide(_ev(EventType.PANIC_WORD, ts=400.0))
    assert sm.state == State.EMERGENCY

    # 60 s 전 — 아직 EMERGENCY 유지
    sm.decide(_ev(EventType.TICK, ts=430.0))
    assert sm.state == State.EMERGENCY

    # 60 s 경과 — ACK 없이 NORMAL 자동 복귀
    effs = sm.decide(_ev(EventType.TICK, ts=461.0))
    assert sm.state == State.NORMAL
    assert "auto-clear" in effs[0].log

    # re-arm 확인 — 복귀 후 새 panic_word 가 새 alert 를 발행
    effs2 = sm.decide(_ev(EventType.PANIC_WORD, ts=462.0))
    assert sm.state == State.EMERGENCY
    assert effs2[0].publish_alert["type"] == "panic_word"
