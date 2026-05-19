#!/bin/bash
# 마음돌봄 Vision — llama.cpp 서버 실행 헬퍼
#
# 사용법:
#   bash scripts/start_llama_server.sh           # 기본: EXAONE-3.5-7.8B (Xavier 32GB 대상)
#   MODEL_PROFILE=qwen bash ...                  # Qwen2.5-3B (다국어 비교용)
#   SAFE_MODE=1 bash ...                         # CPU-only
#   NGL=20 CTX=4096 bash ...                     # 메모리 제약 환경 (NGL 낮게)
#
# 환경변수:
#   MODEL_PROFILE : exaone(기본) | qwen | custom
#                   custom 일 때만 MODEL 환경변수로 직접 지정
#   MODEL         : GGUF 경로 (custom 일 때만)
#   LLAMA_BIN     : llama-server 바이너리 (기본 ~/llama.cpp/build/bin/llama-server)
#   CTX           : context size (기본 8192 — RAG 3 hits + history 안전 수용)
#   NGL           : GPU layers (프로필별 기본; SAFE_MODE=1 이면 0)
#   PORT          : 포트 (기본 8080)
#   SAFE_MODE     : 1 이면 CPU-only
#   THREADS       : CPU 스레드 (기본 nproc)
#
# 모델 프리셋:
#   exaone (7.8B Q4_K_M, 4.7 GB) ⭐ : NGL=33 기본, VRAM ~6GB, Korean native (LG)
#                                     Xavier 32GB / 24GB+ GPU 권장
#   qwen   (3B   Q4_K_M, 2.0 GB)    : NGL=14, 다국어 (Alibaba)
#
# ⚠️ 7.8B 는 6GB VRAM 필요. 4GB GPU 환경이면 SAFE_MODE=1 (CPU-only) 또는 NGL 낮춤.

set -e

LLAMA_BIN="${LLAMA_BIN:-$HOME/llama.cpp/build/bin/llama-server}"
CTX="${CTX:-2048}"                 # Xavier 속도 우선 — RAG 3 hit + 대화 6턴 충분
PORT="${PORT:-8080}"
THREADS="${THREADS:-$(nproc)}"
PARALLEL="${PARALLEL:-1}"          # parallel slot=1 → KV 캐시 메모리/대역폭 ↓

# ---- 모델 프로필 해석 ----
# MODEL_QUANT 로 양자화 선택 (exaone 프로필 한정): Q4_K_M(기본) | Q3_K_M(속도↑)
PROFILE="${MODEL_PROFILE:-exaone}"
MODEL_QUANT="${MODEL_QUANT:-Q3_K_M}"   # 기본을 Q3_K_M 로 — 토큰/초 향상
case "$PROFILE" in
    exaone)
        # EXAONE-3.5-7.8B-Instruct $MODEL_QUANT (Xavier 32GB)
        MODEL="${MODEL:-$HOME/models/EXAONE-3.5-7.8B-Instruct-${MODEL_QUANT}.gguf}"
        PROFILE_NGL_DEFAULT=33
        ;;
    qwen)
        MODEL="${MODEL:-$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf}"
        PROFILE_NGL_DEFAULT=14
        ;;
    custom)
        if [ -z "${MODEL:-}" ]; then
            echo "[ERROR] MODEL_PROFILE=custom 이면 MODEL 환경변수 필수" >&2
            exit 1
        fi
        PROFILE_NGL_DEFAULT=14
        ;;
    *)
        echo "[ERROR] Unknown MODEL_PROFILE: $PROFILE (exaone|qwen|custom)" >&2
        exit 1
        ;;
esac

if [ "${SAFE_MODE:-0}" = "1" ]; then
    NGL=0
    echo "[SAFE_MODE] CPU-only 실행"
else
    NGL="${NGL:-$PROFILE_NGL_DEFAULT}"
fi

echo "[PROFILE] $PROFILE"

# ---- 사전 검증 ----
if [ ! -x "$LLAMA_BIN" ]; then
    echo "[ERROR] llama-server not found: $LLAMA_BIN" >&2
    echo "  빌드: cd ~/llama.cpp && cmake -B build -DGGML_CUDA=ON && cmake --build build --config Release -j\$(nproc)" >&2
    exit 1
fi

if [ ! -f "$MODEL" ]; then
    echo "[ERROR] Model not found: $MODEL" >&2
    echo "" >&2
    echo "  다운로드 가이드:" >&2
    echo "    mkdir -p ~/models && cd ~/models" >&2
    if [[ "$PROFILE" == "exaone" ]]; then
        echo "    huggingface-cli download bartowski/EXAONE-3.5-7.8B-Instruct-GGUF \\" >&2
        echo "        EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf --local-dir ." >&2
    fi
    exit 1
fi

# VRAM 사전 체크 (SAFE_MODE 아닐 때만)
if [ "$NGL" != "0" ] && command -v nvidia-smi &>/dev/null; then
    free_mib=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
    used_mib=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
    echo "[VRAM] free=${free_mib}MiB, used=${used_mib}MiB"
    # NGL 당 약 60MiB 가정 + 여유분 500MiB 필요
    required=$((NGL * 60 + 500))
    if [ "$free_mib" -lt "$required" ]; then
        echo "[WARN] 예상 VRAM ${required}MiB 필요, 현재 free ${free_mib}MiB"
        echo "       SAFE_MODE=1 로 CPU 전용 실행을 권장합니다."
        echo "       3초 후 진행..."
        sleep 3
    fi
fi

# RAM 체크 (최소 2GB 여유)
if command -v free &>/dev/null; then
    free_mb=$(free -m | awk 'NR==2 {print $7}')
    echo "[RAM ] available=${free_mb}MiB"
    if [ "$free_mb" -lt "2000" ]; then
        echo "[WARN] 여유 RAM 부족 (< 2GB). 다른 프로세스 확인 필요"
    fi
fi

echo "[INFO] llama-server 기동"
echo "  binary  : $LLAMA_BIN"
echo "  model   : $MODEL"
echo "  ctx     : $CTX"
echo "  n-gpu   : $NGL"
echo "  threads : $THREADS"
echo "  port    : $PORT"
echo ""

# flash attention: 신/구 버전 모두 대응
#   신버전(>= b4xxx): -fa on|off|auto   (값 필수)
#   구버전           : -fa             (플래그)
EXTRA_FLAGS=()
HELP_OUT=$("$LLAMA_BIN" --help 2>&1 || true)
if echo "$HELP_OUT" | grep -qE -- '-fa[,[:space:]].*\[on\|off\|auto\]'; then
    EXTRA_FLAGS+=(-fa on)
elif echo "$HELP_OUT" | grep -qE -- '(^|[[:space:]])-fa([[:space:]]|,|$)'; then
    EXTRA_FLAGS+=(-fa)
fi

echo "  parallel: $PARALLEL"
echo "  quant   : $MODEL_QUANT"
echo ""

exec "$LLAMA_BIN" \
    -m "$MODEL" \
    -c "$CTX" \
    -ngl "$NGL" \
    -t "$THREADS" \
    -np "$PARALLEL" \
    --host 127.0.0.1 \
    --port "$PORT" \
    "${EXTRA_FLAGS[@]}"
