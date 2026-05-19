#!/bin/bash
# 마음돌봄 — 시연 한 줄 기동 스크립트
#
# 1) llama-server (없으면 띄움 + /health 대기)
# 2) HRI ROS launch (없으면 띄움) — api_gateway_node 포함 (:8000, /demo/ 서빙)
# 3) launch 내장 mind_care_api health 대기
# 4) Firefox 자동 오픈 (DISPLAY 있고 --no-browser 아닐 때)
#
# 사용:
#   bash scripts/start_demo.sh                     # 전체 기동 + Firefox
#   bash scripts/start_demo.sh --no-browser        # 헤드리스 (노트북 LAN 접속용)
#   bash scripts/start_demo.sh status              # 상태만 확인
#   bash scripts/start_demo.sh stop                # API + HRI 정리 (llama 는 별도)
#
# 환경변수 override:
#   API_PORT       (기본 8000)
#   API_ELDER_ID   (기본 elder_01)
#   API_DEV_OPEN   (기본 1 — 인증 우회, 시연용)
#   LLAMA_PORT     (기본 8080)
#   WS_DIR         (기본 ~/ros2_ws)
#   PROJECT_DIR    (기본 ~/마음돌봄/mind_care_vision)

# NOTE: set -u 는 사용 금지 — /opt/ros/foxy/setup.bash 가 AMENT_TRACE_SETUP_FILES
# 등 unbound 변수를 참조해 즉시 죽음. start_hri.sh 와 동일한 정책.

PROJECT_DIR="${PROJECT_DIR:-$HOME/마음돌봄/mind_care_vision}"
WS_DIR="${WS_DIR:-$HOME/ros2_ws}"
API_PORT="${API_PORT:-8000}"
API_ELDER_ID="${API_ELDER_ID:-elder_01}"
API_DEV_OPEN="${API_DEV_OPEN:-1}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
LLAMA_WAIT_S="${LLAMA_WAIT_S:-90}"

PID_DIR=/tmp
LLAMA_PID="$PID_DIR/mindcare_llama.pid"
HRI_PID="$PID_DIR/mindcare_hri.pid"
LLAMA_LOG="$PID_DIR/mindcare_llama.log"
HRI_LOG="$PID_DIR/mindcare_hri.log"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; BLU='\033[0;34m'; NC='\033[0m'

# --- 인자 파싱 ---
NO_BROWSER=0
CMD="up"
for arg in "$@"; do
    case "$arg" in
        --no-browser) NO_BROWSER=1 ;;
        up|stop|down|status) CMD="$arg" ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0 ;;
    esac
done

# --- 환경 세팅 (ROS 자동 검출 + venv) ---
source_env() {
    for d in foxy humble jazzy; do
        if [ -f "/opt/ros/$d/setup.bash" ]; then
            # shellcheck disable=SC1091
            source "/opt/ros/$d/setup.bash"
            break
        fi
    done
    # shellcheck disable=SC1091
    [ -f "$HOME/마음돌봄/.venv-ros/bin/activate" ] && \
        source "$HOME/마음돌봄/.venv-ros/bin/activate"
    # shellcheck disable=SC1091
    [ -f "$WS_DIR/install/setup.bash" ] && source "$WS_DIR/install/setup.bash"

    # rclpy 가 먼저 dlopen 한 .so 들이 static TLS 슬롯을 다 써버려서, 이후 sklearn
    # (RAG bge-m3 embedding 경로) 의 libgomp 가 'cannot allocate memory in static
    # TLS block' 으로 fail. libgomp 를 미리 로드해 큰 TLS 블록 선점.
    if [ -z "${LD_PRELOAD:-}" ] && [ -f /usr/lib/aarch64-linux-gnu/libgomp.so.1 ]; then
        export LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libgomp.so.1
    fi
    # faster-whisper lazy dep 의 numba LLVM JIT 가 AArch64 32-bit relocation
    # overflow 로 SIGABRT. LD_PRELOAD 영향. JIT off → 인터프리터 fallback 안전.
    export NUMBA_DISABLE_JIT="${NUMBA_DISABLE_JIT:-1}"
    # api_gateway_node 는 HRI launch 의 자식 → 여기서 export 한 env 를 상속.
    # 시연용 인증 우회(dev_open)를 launch 내장 API 에 전달.
    export MIND_CARE_DEV_OPEN="$API_DEV_OPEN"
}

is_alive() {
    [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

# --- 1) llama-server ---
start_llama() {
    if pgrep -f llama-server >/dev/null; then
        echo -e "  ${GRN}✔${NC} llama-server 이미 실행 중"
        return 0
    fi
    echo "  llama-server 기동 (로그: $LLAMA_LOG)..."
    setsid bash "$PROJECT_DIR/scripts/start_llama_server.sh" \
        > "$LLAMA_LOG" 2>&1 < /dev/null &
    echo $! > "$LLAMA_PID"
    disown
    local start; start=$(date +%s)
    while true; do
        if curl -sf --max-time 1 "http://127.0.0.1:${LLAMA_PORT}/health" >/dev/null 2>&1; then
            echo -e "  ${GRN}✔${NC} llama-server ready"
            return 0
        fi
        if [ $(($(date +%s) - start)) -ge "$LLAMA_WAIT_S" ]; then
            echo -e "  ${RED}✖${NC} llama-server 타임아웃 — 로그 확인:"
            tail -10 "$LLAMA_LOG"
            return 1
        fi
        sleep 1
    done
}

# --- 2) HRI launch ---
start_hri() {
    if pgrep -f "ros2 launch mind_care_vision" >/dev/null; then
        echo -e "  ${GRN}✔${NC} HRI launch 이미 실행 중"
        return 0
    fi
    local lf="$WS_DIR/install/mind_care_vision/share/mind_care_vision/launch/hri_system.launch.py"
    if [ ! -f "$lf" ]; then
        echo -e "  ${RED}✖${NC} launch 파일 없음: $lf"
        echo "     cd $WS_DIR && colcon build --symlink-install --packages-select mind_care_vision"
        return 1
    fi
    # Xavier override config (RAG/SV 경로가 /home/user/ 로 박혀있음).
    # 기본 hri_params.yaml 은 /home/eslee03/ 가 박혀있어 RAG init/화자검증 모두 fail.
    local launch_config="${LAUNCH_CONFIG:-$PROJECT_DIR/config/hri_params.xavier.yaml}"
    local launch_args=()
    if [ -f "$launch_config" ]; then
        launch_args+=("config_file:=$launch_config")
        echo "  config: $launch_config"
    else
        echo -e "  ${YLW}⚠${NC} xavier config 없음 — 패키지 기본 사용"
    fi
    echo "  HRI launch 기동 (로그: $HRI_LOG)..."
    setsid ros2 launch mind_care_vision hri_system.launch.py "${launch_args[@]}" \
        > "$HRI_LOG" 2>&1 < /dev/null &
    echo $! > "$HRI_PID"
    disown
    sleep 5
    if is_alive "$HRI_PID"; then
        echo -e "  ${GRN}✔${NC} HRI launch up (PID=$(cat "$HRI_PID"))"
        return 0
    fi
    echo -e "  ${RED}✖${NC} HRI launch 즉시 죽음 — 로그 확인:"
    tail -10 "$HRI_LOG"
    return 1
}

# --- 3) mind_care_api — HRI launch 의 api_gateway_node 가 :8000 서빙 ---
# af5b059 부터 api_gateway 가 hri_system.launch.py 에 통합됨. 여기서 별도
# uvicorn 을 띄우면 :8000 충돌(레이스). launch 가 띄운 API 의 health 만 대기.
wait_api() {
    echo "  mind_care_api (launch 내장 api_gateway_node) health 대기..."
    # api_gateway_node 는 rclpy/ros_bridge init 후 늦게 bind → 넉넉히 대기
    local api_wait_s="${API_WAIT_S:-30}" start; start=$(date +%s)
    while true; do
        if curl -sf "http://127.0.0.1:$API_PORT/api/v1/health" >/dev/null 2>&1; then
            echo -e "  ${GRN}✔${NC} mind_care_api ready"
            return 0
        fi
        if [ $(($(date +%s) - start)) -ge "$api_wait_s" ]; then
            echo -e "  ${RED}✖${NC} API health 실패 (${api_wait_s}s 타임아웃) — HRI 로그 확인:"
            tail -20 "$HRI_LOG"
            return 1
        fi
        sleep 1
    done
}

# --- 4) Firefox ---
open_browser() {
    [ "$NO_BROWSER" = "1" ] && return 0
    [ -z "${DISPLAY:-}" ] && { echo "  (DISPLAY 없음 — 브라우저 스킵, 노트북에서 LAN 접속)"; return 0; }
    local url="http://127.0.0.1:$API_PORT/demo/?elder_id=$API_ELDER_ID"
    echo "  Firefox 오픈: $url"
    DISPLAY="$DISPLAY" firefox --new-window "$url" > /dev/null 2>&1 &
    disown
}

# --- 상태 표 ---
show_status() {
    local lan_ip
    lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo ""
    echo "════════════════════════════════════════════════════"
    echo -e "  ${BLU}🩷 마음돌봄 시연 환경${NC}"
    echo "════════════════════════════════════════════════════"
    if pgrep -f llama-server >/dev/null; then
        echo -e "  ${GRN}●${NC} llama-server   http://127.0.0.1:$LLAMA_PORT"
    else
        echo -e "  ${RED}○${NC} llama-server   (멈춤)"
    fi
    if pgrep -f "ros2 launch mind_care_vision" >/dev/null; then
        echo -e "  ${GRN}●${NC} HRI launch     (ros2 nodes alive)"
    else
        echo -e "  ${RED}○${NC} HRI launch     (멈춤)"
    fi
    if curl -sf "http://127.0.0.1:$API_PORT/api/v1/health" >/dev/null 2>&1; then
        echo -e "  ${GRN}●${NC} mind_care_api  http://127.0.0.1:$API_PORT  ($(curl -sf http://127.0.0.1:$API_PORT/api/v1/health))"
    else
        echo -e "  ${RED}○${NC} mind_care_api  (응답 없음)"
    fi
    echo "────────────────────────────────────────────────────"
    echo -e "  📺 Xavier 모니터:  ${BLU}http://127.0.0.1:$API_PORT/demo/${NC}"
    echo -e "  📱 노트북 (LAN):    ${BLU}http://$lan_ip:$API_PORT/demo/?elder_id=$API_ELDER_ID${NC}"
    echo "  📜 로그:           tail -f $HRI_LOG  |  $LLAMA_LOG  (API 는 HRI 로그에 포함)"
    echo "  🛑 정리:           bash $0 stop"
    echo "════════════════════════════════════════════════════"
}

stop_one() {
    local label="$1" pidfile="$2"
    if is_alive "$pidfile"; then
        local pid; pid=$(cat "$pidfile")
        echo "  $label PID=$pid 종료..."
        kill "$pid" 2>/dev/null
        sleep 1
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$pidfile"
}

case "$CMD" in
    up)
        echo -e "${BLU}═══ 마음돌봄 시연 기동 ═══${NC}"
        source_env
        start_llama || exit 1
        start_hri   || exit 1
        wait_api    || exit 1
        show_status
        open_browser
        ;;
    stop|down)
        echo -e "${BLU}═══ 마음돌봄 시연 정리 ═══${NC}"
        # mind_care_api 는 HRI launch 의 자식(api_gateway_node) → 아래 pkill 로 정리
        stop_one "HRI launch"    "$HRI_PID"
        # PID 파일이 stale/유실되면 launch 부모가 고아로 누수 → 패턴으로도 정리.
        # (start_hri 의 '이미 실행 중' 오판 방지)
        pkill -9 -f "ros2 launch mind_care_vision" 2>/dev/null
        # ros2 launch 가 SIGTERM 받아도 자식 노드가 종종 살아남아 다음 기동 때
        # 중복 실행 → 응급 alert 다중 발행 / TTS 두 번 발화 등 사이드이펙트.
        # launch 의 모든 노드를 강제 정리 (decider/dispatcher 포함 — 누락 시 유령 누적).
        echo "  잔존 자식 노드 정리..."
        HRI_NODES=(audio_bridge_node llm_dialogue_node tts_node
                   emergency_decider_node alert_dispatcher_node api_gateway_node)
        for n in "${HRI_NODES[@]}"; do
            pkill -9 -f "$n" 2>/dev/null
        done
        sleep 1
        # 정리 확인 — 살아남은 게 있으면 경고
        for n in "${HRI_NODES[@]}"; do
            if pgrep -f "$n" >/dev/null 2>&1; then
                echo -e "  ${YLW}⚠${NC} $n 잔존 — 재시도"
                pkill -9 -f "$n" 2>/dev/null
            fi
        done
        echo "  (llama-server 는 별도: pkill -f llama-server  또는  systemctl stop mindcare-llama)"
        ;;
    status)
        show_status
        ;;
esac
