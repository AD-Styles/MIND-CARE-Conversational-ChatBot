"""buzzer_channel.py — Jetson GPIO active buzzer 채널.

가이드 §11.3 (Jetson AGX Xavier 40-pin J30, BOARD pin 7, BCM 4) 기반.
- pattern="siren" : HIGH/LOW 각 0.5s 반복
- pattern="beep"  : HIGH/LOW 각 0.1s 반복
- pattern="solid" : duration 동안 HIGH

Jetson.GPIO 미설치/권한 없으면 available() == False 로 dispatcher 가 자동 스킵.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

from .base import Channel, ChannelResult

log = logging.getLogger("mind_care_emergency.channels.buzzer_gpio")

DEFAULT_PIN = 7  # BOARD 모드 핀 7 (BCM 4) — 가이드 §11.2 배선


class BuzzerChannel(Channel):
    name = "buzzer_gpio"

    def __init__(self, pin: int = DEFAULT_PIN, default_pattern: str = "siren",
                 default_duration_s: float = 3.0):
        self.pin = pin
        self.default_pattern = default_pattern
        self.default_duration_s = default_duration_s
        self._gpio = None
        self._lock = threading.Lock()
        self._init_error: Optional[str] = None
        self._init_gpio()

    def _init_gpio(self) -> None:
        try:
            import Jetson.GPIO as GPIO  # type: ignore[import-not-found]
        except Exception as exc:
            self._init_error = f"Jetson.GPIO import 실패: {exc}"
            log.info("buzzer_gpio 비활성 — %s", self._init_error)
            return
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(self.pin, GPIO.OUT, initial=GPIO.LOW)
            self._gpio = GPIO
        except Exception as exc:
            self._init_error = f"GPIO setup 실패 (gpio 그룹 권한 확인): {exc}"
            log.info("buzzer_gpio 비활성 — %s", self._init_error)

    def available(self) -> bool:
        return self._gpio is not None

    def _pulse(self, duration_s: float, pattern: str) -> None:
        assert self._gpio is not None
        GPIO = self._gpio
        if pattern == "siren":
            cycles = max(1, int(duration_s / 1.0))
            for _ in range(cycles):
                GPIO.output(self.pin, GPIO.HIGH); time.sleep(0.5)
                GPIO.output(self.pin, GPIO.LOW);  time.sleep(0.5)
        elif pattern == "beep":
            cycles = max(1, int(duration_s / 0.2))
            for _ in range(cycles):
                GPIO.output(self.pin, GPIO.HIGH); time.sleep(0.1)
                GPIO.output(self.pin, GPIO.LOW);  time.sleep(0.1)
        else:  # solid
            GPIO.output(self.pin, GPIO.HIGH)
            time.sleep(duration_s)
            GPIO.output(self.pin, GPIO.LOW)

    def alert(self, duration_s: Optional[float] = None,
              pattern: Optional[str] = None) -> bool:
        """동기 부저 실행 — 직접 호출용 (테스트/디버그)."""
        if not self.available():
            return False
        with self._lock:
            try:
                self._pulse(duration_s or self.default_duration_s,
                            pattern or self.default_pattern)
                return True
            except Exception as exc:
                log.warning("buzzer alert 실패: %s", exc)
                return False

    def send(self, alert: dict, guardians: List[dict]) -> ChannelResult:
        """Channel 인터페이스 — emergency dispatcher 가 부르는 진입점."""
        if not self.available():
            return ChannelResult(ok=False, detail={},
                                  error=self._init_error or "Jetson.GPIO 미사용")
        duration_s = float(alert.get("buzzer_duration_s", self.default_duration_s))
        pattern    = str(alert.get("buzzer_pattern", self.default_pattern))
        with self._lock:
            try:
                self._pulse(duration_s, pattern)
                return ChannelResult(ok=True,
                                     detail={"pin": self.pin,
                                             "pattern": pattern,
                                             "duration_s": duration_s})
            except Exception as exc:
                log.warning("buzzer send 실패: %s", exc)
                return ChannelResult(ok=False, detail={}, error=str(exc))

    def close(self) -> None:
        if self._gpio is not None:
            try:
                self._gpio.cleanup(self.pin)
            except Exception:
                pass
