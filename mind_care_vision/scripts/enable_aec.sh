#!/usr/bin/env bash
# PulseAudio AEC (WebRTC) 활성화 — 스피커 출력이 마이크로 다시 들어오는 에코 제거.
#
# 동작:
#   1) 현재 default sink/source 를 찾아 raw 디바이스로 사용
#   2) module-echo-cancel 을 webrtc 백엔드로 로드 → 가상 sink/source 생성
#   3) AEC sink/source 를 default 로 승격
#
# 멱등성: 이미 로드돼 있으면 언로드 후 재로드.
# 영속화: PulseAudio 재시작 시 풀림 → 시연 직전 또는 부팅 후 1회 실행.
#         systemd user 유닛으로 자동화하려면 ~/.config/pulse/default.pa 에 동일 라인 추가.

set -euo pipefail

if ! command -v pactl >/dev/null; then
    echo "[aec] pactl 없음. PulseAudio 설치 필요." >&2
    exit 1
fi

# 1) 기존 echo-cancel 모듈 언로드
mapfile -t old_ids < <(pactl list short modules | awk '/module-echo-cancel/ {print $1}')
for id in "${old_ids[@]}"; do
    pactl unload-module "$id" || true
done

# 2) raw default 디바이스 캡쳐 (AEC 가 자기 자신을 잡지 않게)
raw_sink=$(pactl info | awk -F': ' '/Default Sink/ {print $2}')
raw_source=$(pactl info | awk -F': ' '/Default Source/ {print $2}')
echo "[aec] raw sink   = $raw_sink"
echo "[aec] raw source = $raw_source"

# 3) AEC 모듈 로드
pactl load-module module-echo-cancel \
    aec_method=webrtc \
    aec_args="'noise_suppression=true voice_detection=true high_pass_filter=true extended_filter=true'" \
    source_master="$raw_source" \
    sink_master="$raw_sink" \
    source_name=mic_aec \
    sink_name=spk_aec \
    source_properties=device.description=Mic_AEC \
    sink_properties=device.description=Spk_AEC >/dev/null

# 4) AEC 가상 디바이스를 default 로
pactl set-default-source mic_aec
pactl set-default-sink   spk_aec

echo "[aec] 활성화 완료. 현재 default:"
pactl info | grep -E "Default (Sink|Source)"
