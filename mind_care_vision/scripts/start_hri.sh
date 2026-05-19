#!/bin/bash
# 마음돌봄 Vision — 단계적 기동 래퍼
#
# 1) healthcheck 실행 (리소스·프로세스·모델 확인)
# 2) llama-server 백그라운드 기동 + /health 대기
# 3) ROS 2 launch 실행
#
# 사용법:
#   bash scripts/start_hri.sh              # 기본: 안전 프로필 (NGL=14)
#   SAFE_MODE=1 bash scripts/start_hri.sh  # CPU-only
#   SKIP_LLAMA=1 bash scripts/start_hri.sh # 이미 llama-server 떠 있으면
#
# 환경변수:
#   WS_DIR        : ROS 2 워크스페이스 (기본 ~/ros2_ws)
#   PROJECT_DIR   : 프로젝트 루트 (기본 ~/마음돌봄/mind_care_vision)
#   LLAMA_PORT    : llama-server 포트 (기본 8080)
#   LLAMA_WAIT_S  : /health 대기 최대 초 (기본 60)

# ROS 2 setup.bash 는 unbound 변수(AMENT_TRACE_SETUP_FILES 등)를 참조하므로
# set -u 는 사용하지 않음. 대신 중요한 변수는 기본값으로 보호.
PROJECT_DIR="${PROJECT_DIR:-$HOME/마음돌봄/mind_care_vision}"
WS_DIR="${WS_DIR:-$HOME/ros2_ws}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
LLAMA_WAIT_S="${LLAMA_WAIT_S:-60}"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'

# [0] PulseAudio AEC (스피커→마이크 에코 제거). PA 재시작 시 풀리므로 매번 적용.
# SKIP_AEC=1 로 우회 가능.
if [ "${SKIP_AEC:-0}" != "1" ]; then
    echo -e "${GRN}[0/3] PulseAudio AEC${NC}"
    if ! bash "$PROJECT_DIR/scripts/enable_aec.sh"; then
        echo -e "${YLW}[WARN] AEC 활성화 실패 — 에코 가능. tts_guard_tail_s 만으로 계속.${NC}"
    fi
    echo ""
fi

echo -e "${GRN}[1/3] Health check${NC}"
if ! bash "$PROJECT_DIR/scripts/healthcheck.sh"; then
    echo -e "${YLW}[WARN] healthcheck 실패/경고. 계속 진행합니다...${NC}"
fi

echo ""
echo -e "${GRN}[2/3] llama-server 기동${NC}"
if [ "${SKIP_LLAMA:-0}" = "1" ]; then
    echo "  SKIP_LLAMA=1 — 기존 llama-server 사용"
elif pgrep -f llama-server >/dev/null; then
    echo "  이미 실행 중 — 스킵"
else
    LOG_FILE="/tmp/llama-server-$(date +%Y%m%d-%H%M%S).log"
    echo "  로그: $LOG_FILE"
    setsid bash "$PROJECT_DIR/scripts/start_llama_server.sh" \
        > "$LOG_FILE" 2>&1 < /dev/null &
    disown
    echo "  PID=$! — /health 대기 (최대 ${LLAMA_WAIT_S}초)..."

    start=$(date +%s)
    while true; do
        if curl -sf --max-time 1 "http://127.0.0.1:${LLAMA_PORT}/health" >/dev/null 2>&1; then
            echo -e "  ${GRN}✔ llama-server ready${NC}"
            break
        fi
        elapsed=$(( $(date +%s) - start ))
        if [ "$elapsed" -ge "$LLAMA_WAIT_S" ]; then
            echo -e "  ${RED}✖ 타임아웃${NC} — $LOG_FILE 확인"
            tail -20 "$LOG_FILE"
            exit 1
        fi
        sleep 1
    done
fi

echo ""
echo -e "${GRN}[3/3] ROS 2 HRI 런치${NC}"

# ROS 2 환경 (Xavier JP5.1.x = foxy, JP6.x = humble — 설치된 것 자동 검출)
ROS_SETUP=""
for d in foxy humble jazzy; do
    if [ -f "/opt/ros/$d/setup.bash" ]; then
        ROS_SETUP="/opt/ros/$d/setup.bash"
        break
    fi
done
if [ -z "$ROS_SETUP" ]; then
    echo -e "${RED}[ERROR] /opt/ros/{foxy,humble,jazzy}/setup.bash not found${NC}"
    exit 1
fi
# shellcheck disable=SC1091
source "$ROS_SETUP"

if [ -f "$WS_DIR/install/setup.bash" ]; then
    # shellcheck disable=SC1091
    source "$WS_DIR/install/setup.bash"
else
    echo -e "${YLW}[WARN] $WS_DIR/install/setup.bash 없음 — colcon build 미실행?${NC}"
fi

# venv 활성화 — 노드 shebang 은 /usr/bin/python3 이지만 venv 가 --system-site-packages
# 로 만들어졌고 ABI 동일 (3.8) 이라 PYTHONPATH 노출만으로 pyds/edge_tts/chromadb 등이
# 시스템 python 에서도 import 됨. (smoke_e2e/start_demo 와 동일한 환경 보장)
VENV_DIR="${VENV_DIR:-$HOME/마음돌봄/.venv-ros}"
if [ -f "$VENV_DIR/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    export PYTHONPATH="$VENV_DIR/lib/python3.8/site-packages:$PYTHONPATH"
else
    echo -e "${YLW}[WARN] venv 없음: $VENV_DIR — pyds 등 누락 가능${NC}"
fi

# rclpy 가 먼저 dlopen 한 .so 들이 static TLS 슬롯을 다 써버려 이후 sklearn
# (RAG bge-m3 embedding) 의 libgomp 가 'cannot allocate memory in static TLS
# block' 으로 fail. libgomp 를 미리 로드해 큰 TLS 블록 선점.
if [ -z "${LD_PRELOAD:-}" ] && [ -f /usr/lib/aarch64-linux-gnu/libgomp.so.1 ]; then
    export LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libgomp.so.1
fi
# faster-whisper 의 lazy dep 가 numba LLVM JIT 호출 시 AArch64 32-bit
# relocation overflow 로 SIGABRT (RuntimeDyldELF assertion). LD_PRELOAD 이후
# 메모리 layout 영향. JIT 끄면 numba 가 인터프리터 모드로 fallback → 안전.
export NUMBA_DISABLE_JIT="${NUMBA_DISABLE_JIT:-1}"

LAUNCH_FILE="$WS_DIR/install/mind_care_vision/share/mind_care_vision/launch/hri_system.launch.py"
if [ ! -f "$LAUNCH_FILE" ]; then
    echo -e "${RED}[ERROR] launch 파일 없음: $LAUNCH_FILE${NC}"
    echo "  cd $WS_DIR && colcon build --symlink-install --packages-select mind_care_vision"
    exit 1
fi

# Xavier 환경 override config (있으면 사용 — RAG / SV 경로가 /home/user/ 로 박혀있음).
# 없으면 패키지 기본값 hri_params.yaml 사용.
LAUNCH_CONFIG="${LAUNCH_CONFIG:-$PROJECT_DIR/config/hri_params.xavier.yaml}"
if [ -f "$LAUNCH_CONFIG" ]; then
    echo "  config: $LAUNCH_CONFIG"
    exec ros2 launch mind_care_vision hri_system.launch.py config_file:="$LAUNCH_CONFIG"
else
    echo "  config: (패키지 기본 hri_params.yaml)"
    exec ros2 launch mind_care_vision hri_system.launch.py
fi
