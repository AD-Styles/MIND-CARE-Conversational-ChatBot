"""local_buzzer.py — 네트워크 단절 fallback.

ALSA `aplay` 로 기본 wav 재생. PC 의 system bell 은 wsl 환경에서 무음일 수 있음.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import List

from .base import Channel, ChannelResult

log = logging.getLogger("mind_care_emergency.channels.buzzer")

# 패키지 동봉 알람음 (2-tone 비상 사이렌) 을 최우선 사용.
# fallback 으로 Ubuntu 표준 위치 — Front_Center.wav 는 채널 테스트 파일이라
# "Front Center" 음성이 재생되므로 알림음으로 부적합, 마지막 순위.
_ASSET_WAV = os.path.join(os.path.dirname(__file__), "assets", "alert.wav")
DEFAULT_WAV_CANDIDATES = [
    _ASSET_WAV,
    "/usr/share/sounds/alsa/Front_Center.wav",
    "/usr/share/sounds/alsa/Side_Right.wav",
]


def _find_default_wav() -> str | None:
    for p in DEFAULT_WAV_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


class LocalBuzzerChannel(Channel):
    name = "buzzer"

    def __init__(self, wav_path: str | None = None, repeat: int = 3):
        self.wav_path = wav_path or _find_default_wav()
        self.repeat = repeat

    def available(self) -> bool:
        return shutil.which("aplay") is not None and self.wav_path is not None

    def send(self, alert: dict, guardians: List[dict]) -> ChannelResult:
        if not self.available():
            return ChannelResult(ok=False, detail={},
                                  error="aplay or wav 없음")
        try:
            for _ in range(self.repeat):
                subprocess.run(["aplay", "-q", self.wav_path],
                               check=True, timeout=5.0,
                               stderr=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL)
            return ChannelResult(ok=True, detail={"wav": self.wav_path,
                                                   "repeat": self.repeat})
        except Exception as exc:
            log.warning("buzzer 재생 실패: %s", exc)
            return ChannelResult(ok=False, detail={}, error=str(exc))
