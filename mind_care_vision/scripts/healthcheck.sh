#!/bin/bash
# 마음돌봄 Vision — 실행 전 시스템 리소스 점검
#
# 사용법: bash scripts/healthcheck.sh
#
# 체크 항목:
#   - GPU VRAM 사용량·온도 (1650 Ti 4GB는 경계값이 작아 중요)
#   - CPU/RAM 여유
#   - llama-server HTTP 생존 여부
#   - ROS 2 노드 실행 상태
#   - 모델 파일 존재 확인

set -u
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'

ok()   { echo -e "${GRN}✔${NC} $1"; }
warn() { echo -e "${YLW}⚠${NC} $1"; }
err()  { echo -e "${RED}✖${NC} $1"; }

echo "════════════════════════════════════════════════════"
echo "  마음돌봄 Vision 헬스체크  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════"

# --- GPU ---
echo ""
echo "[GPU]"
if command -v nvidia-smi &>/dev/null; then
    vram_info=$(nvidia-smi --query-gpu=memory.free,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader,nounits | head -1)
    IFS=',' read -r free used total util temp <<< "$vram_info"
    free=$(echo $free | xargs)
    used=$(echo $used | xargs)
    total=$(echo $total | xargs)
    util=$(echo $util | xargs)
    temp=$(echo $temp | xargs)

    echo "  VRAM : ${used} / ${total} MiB 사용 (free ${free} MiB)"
    echo "  Util : ${util}%    Temp : ${temp}°C"
    if [ "$free" -lt "1000" ]; then
        warn "  free VRAM < 1GB — llama-server 기동 시 SAFE_MODE=1 권장"
    elif [ "$free" -lt "2000" ]; then
        warn "  free VRAM < 2GB — NGL=10 이하 권장"
    else
        ok "  VRAM 여유 충분"
    fi

    if [ "${temp:-0}" -gt "80" ]; then
        err "  GPU 온도 > 80°C — 발열 확인 필요"
    fi
elif command -v tegrastats &>/dev/null; then
    # Jetson 계열: nvidia-smi 가 없고 tegrastats 를 씀
    sample=$(timeout 1.5 tegrastats --interval 500 2>/dev/null | head -1)
    if [ -n "$sample" ]; then
        gr3d=$(echo "$sample" | grep -oE "GR3D_FREQ [0-9]+%" | head -1)
        gpu_temp=$(echo "$sample" | grep -oE "GPU@[0-9.]+C" | head -1 | tr -d 'GPU@C')
        ram=$(echo "$sample" | grep -oE "RAM [0-9]+/[0-9]+MB" | head -1)
        ok "  Jetson tegrastats: $ram, $gr3d, GPU온도 ${gpu_temp}°C (CUDA $(/usr/local/cuda/bin/nvcc --version 2>/dev/null | grep release | awk '{print $5}' | tr -d ,))"
        if [ -n "$gpu_temp" ] && (( $(echo "$gpu_temp > 80" | bc -l 2>/dev/null) )); then
            err "  GPU 온도 > 80°C — 발열 확인 필요"
        fi
    else
        warn "  tegrastats 응답 없음 (권한 또는 일시 오류)"
    fi
    # torch.cuda 보조 확인
    if [ -d /home/user/마음돌봄/.venv-ros ]; then
        cuda_ok=$(/home/user/마음돌봄/.venv-ros/bin/python -c "import torch; print('OK' if torch.cuda.is_available() else 'FAIL')" 2>/dev/null)
        [ "$cuda_ok" = "OK" ] && ok "  torch.cuda.is_available() = True (Xavier sm_72)"
    fi
else
    warn "  nvidia-smi/tegrastats 모두 미발견"
fi

# --- CPU / RAM ---
echo ""
echo "[CPU / RAM]"
load=$(uptime | awk -F'load average:' '{print $2}')
echo "  Load:${load}"
mem_info=$(free -m | awk 'NR==2 {printf "%d/%d MB free, %d MB cached", $7, $2, $6}')
echo "  RAM : $mem_info"
free_mb=$(free -m | awk 'NR==2 {print $7}')
if [ "$free_mb" -lt "1500" ]; then
    err "  여유 RAM < 1.5GB — 무거운 프로세스 있음"
elif [ "$free_mb" -lt "3000" ]; then
    warn "  여유 RAM < 3GB"
else
    ok "  RAM 여유 충분"
fi

# --- 프로세스 ---
echo ""
echo "[프로세스]"
if pgrep -f llama-server >/dev/null; then
    pid=$(pgrep -f llama-server | head -1)
    rss=$(ps -o rss= -p "$pid" 2>/dev/null | awk '{print int($1/1024)}')
    ok "  llama-server 실행 중 (PID=$pid, RSS=${rss}MiB)"
else
    warn "  llama-server 미실행"
fi

ros_count=$(pgrep -f 'audio_bridge_node\|llm_dialogue_node\|tts_node' 2>/dev/null | wc -l)
if [ "${ros_count:-0}" -gt "0" ]; then
    ok "  ROS 노드 $ros_count개 실행 중"
    pgrep -af 'audio_bridge_node\|llm_dialogue_node\|tts_node' | sed 's/^/    /'
else
    warn "  ROS 노드 미실행"
fi

heavy=$(pgrep -af 'pip install\|cmake --build\|docker build\|apt-get install' 2>/dev/null | head -3)
if [ -n "$heavy" ]; then
    warn "  무거운 작업 진행 중 (llama-server와 동시 실행 위험):"
    echo "$heavy" | sed 's/^/    /'
fi

# --- HTTP ---
echo ""
echo "[llama-server HTTP]"
if curl -sf --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1; then
    ok "  /health OK"
else
    warn "  http://127.0.0.1:8080 응답 없음"
fi

# --- 모델 파일 ---
echo ""
echo "[모델 파일]"
GGUFS=(
    "$HOME/models/EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf"
    "$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf"
)
found=0
for g in "${GGUFS[@]}"; do
    if [ -f "$g" ]; then
        size=$(du -h "$g" | cut -f1)
        ok "  GGUF: $(basename "$g") ($size)"
        found=1
    fi
done
if [ "$found" -eq "0" ]; then
    err "  GGUF 하나도 없음"
    for g in "${GGUFS[@]}"; do echo "    - $g"; done
fi

# --- venv ---
echo ""
echo "[venv-ros Python 패키지]"
VENV_PY="$HOME/마음돌봄/.venv-ros/bin/python"
if [ -x "$VENV_PY" ]; then
    # rclpy는 venv가 아닌 /opt/ros/jazzy 에서 제공됨 — 별도 체크
    $VENV_PY - <<'PYEOF'
import importlib
mods = ["sounddevice", "webrtcvad", "faster_whisper", "requests", "edge_tts",
        "soundfile", "numpy", "resemblyzer", "scipy", "chromadb"]
opt_mods = ["melo", "torch", "TTS", "langchain_chroma", "langchain_huggingface"]
for m in mods:
    try:
        importlib.import_module(m)
        print(f"  \u2714 {m}")
    except Exception as e:
        print(f"  \u2716 {m}  ({type(e).__name__})")
for m in opt_mods:
    try:
        importlib.import_module(m)
        print(f"  \u2714 {m} (optional)")
    except Exception:
        print(f"  - {m} (optional, 미설치)")
PYEOF
else
    err "  venv 없음: $VENV_PY"
fi

# rclpy는 ROS 2 환경에서만 import 가능 — 설치된 distro 자동 감지 (foxy/humble/jazzy)
echo ""
echo "[ROS 2 rclpy]"
ROS_FOUND=""
for d in foxy humble jazzy; do
    [ -f "/opt/ros/$d/setup.bash" ] && ROS_FOUND="$d" && break
done
if [ -n "$ROS_FOUND" ]; then
    if bash -c "source /opt/ros/$ROS_FOUND/setup.bash && \"\$1\" -c 'import rclpy'" _ "$VENV_PY" 2>/dev/null; then
        ok "  rclpy import OK (source /opt/ros/$ROS_FOUND/setup.bash 필요)"
    else
        err "  rclpy import 실패 — ROS 2 $ROS_FOUND 환경 확인"
    fi
else
    warn "  /opt/ros/{foxy,humble,jazzy} 미발견"
fi

# --- 화자 검증 ---
echo ""
echo "[화자 검증 (SV)]"
SV_PATH="$HOME/models/speaker.npy"
if [ -f "$SV_PATH" ]; then
    sz=$(stat -c%s "$SV_PATH" 2>/dev/null)
    ok "  speaker.npy 존재 (${sz} bytes)"
else
    warn "  speaker.npy 없음 — enroll_speaker.py 미실행"
fi

# --- RAG Chroma ---
echo ""
echo "[RAG Chroma]"
CHROMA_DIR="$HOME/마음돌봄/med_data/chroma_db"
if [ -d "$CHROMA_DIR" ]; then
    sz=$(du -sh "$CHROMA_DIR" 2>/dev/null | cut -f1)
    ok "  chroma_db 존재 ($sz)"
    STATS="$CHROMA_DIR/_build_stats.json"
    if [ -f "$STATS" ]; then
        model=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(\"model\",\"?\"))" "$STATS" 2>/dev/null)
        kept=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(\"kept\",\"?\"))" "$STATS" 2>/dev/null)
        echo "    model=$model"
        echo "    kept=$kept"
    fi
else
    err "  chroma_db 없음 — tools/build_chroma_*.py 재빌드 필요"
fi

# --- numba 패치 ---
echo ""
echo "[numba coverage_support 패치]"
NUMBA_PATCH="$HOME/마음돌봄/.venv-ros/lib/python3.12/site-packages/numba/misc/coverage_support.py"
if [ -f "$NUMBA_PATCH" ] && head -1 "$NUMBA_PATCH" | grep -q "stub"; then
    ok "  패치 적용됨"
elif [ -f "$NUMBA_PATCH" ]; then
    warn "  numba 원본 — coverage 충돌 시 SV import 실패 가능"
fi

echo ""
echo "════════════════════════════════════════════════════"
