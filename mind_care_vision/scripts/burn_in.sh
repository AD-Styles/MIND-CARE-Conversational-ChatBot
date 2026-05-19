#!/usr/bin/env bash
# 마음돌봄 — Burn-in 안정성 테스트.
#
# HRI 전체 스택을 N 시간 연속 가동, 매 1 분마다:
#   - llama-server RSS, GPU VRAM·온도
#   - ROS 노드 RSS 추이
#   - /llm/responses 누적 카운트 (응답 누락 감지)
#   - alerts.db 행 수
# 기록 → /var/log/mindcare/burnin/*.csv
#
# 사용:
#   bash scripts/burn_in.sh 24      # 24시간
#   bash scripts/burn_in.sh 168     # 168시간 (1주, 권장)
#   DURATION_H=2 bash scripts/burn_in.sh   # 환경변수 override

set -u
DURATION_H="${1:-${DURATION_H:-24}}"
DURATION_S=$((DURATION_H * 3600))
TICK_S=60          # 샘플 간격
LOG_DIR="${LOG_DIR:-$HOME/마음돌봄/burnin_logs}"
mkdir -p "$LOG_DIR"

TS=$(date '+%Y%m%d_%H%M%S')
RUN_DIR="$LOG_DIR/$TS"
mkdir -p "$RUN_DIR"

CSV="$RUN_DIR/metrics.csv"
HRI_LOG="$RUN_DIR/hri.log"

source ~/마음돌봄/.venv-ros/bin/activate
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash 2>/dev/null

echo "════════════════════════════════════════════════════"
echo "  Burn-in: ${DURATION_H} h, log dir: $RUN_DIR"
echo "════════════════════════════════════════════════════"

# CSV 헤더
echo "ts_iso,uptime_s,llama_rss_mb,gpu_used_mb,gpu_temp_c,ros_rss_total_mb,llm_responses_n,alert_n" > "$CSV"

# --- HRI 기동 ---
echo "[1/2] HRI 기동 …"
nohup bash ~/마음돌봄/mind_care_vision/scripts/start_hri.sh > "$HRI_LOG" 2>&1 &
HRI_PID=$!
sleep 30  # llama-server + 노드 모두 뜰 때까지

# --- 응답 카운터 (백그라운드 echo → wc) ---
RESP_LOG="$RUN_DIR/responses.jsonl"
ALERT_LOG="$RUN_DIR/alerts.jsonl"
ros2 topic echo /llm/responses --csv 2>/dev/null > "$RESP_LOG" &
RESP_PID=$!
ros2 topic echo /emergency/alert --csv 2>/dev/null > "$ALERT_LOG" &
ALERT_PID=$!

trap 'echo "[INT] cleanup"; kill -INT $HRI_PID $RESP_PID $ALERT_PID 2>/dev/null; wait; exit 0' INT TERM

# --- 메트릭 수집 루프 ---
echo "[2/2] 메트릭 수집 시작 (tick=${TICK_S}s)"
START=$(date +%s)
while [ $(($(date +%s) - START)) -lt $DURATION_S ]; do
    NOW=$(date +%s)
    UPTIME=$((NOW - START))

    # llama-server RSS
    LLAMA_PID=$(pgrep -f llama-server | head -1)
    if [ -n "${LLAMA_PID:-}" ]; then
        LLAMA_RSS=$(awk '/VmRSS/ {print int($2/1024)}' /proc/$LLAMA_PID/status 2>/dev/null)
    else
        LLAMA_RSS=0
    fi

    # GPU
    GPU=$(nvidia-smi --query-gpu=memory.used,temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1)
    GPU_USED=$(echo "$GPU" | awk -F, '{print $1+0}')
    GPU_TEMP=$(echo "$GPU" | awk -F, '{print $2+0}')

    # ROS 노드 합산 RSS
    ROS_RSS=0
    for pid in $(pgrep -f 'audio_bridge_node\|llm_dialogue_node\|tts_node\|emergency_decider\|alert_dispatcher\|api_gateway' 2>/dev/null); do
        rss=$(awk '/VmRSS/ {print int($2/1024)}' /proc/$pid/status 2>/dev/null || echo 0)
        ROS_RSS=$((ROS_RSS + rss))
    done

    # 응답 카운트
    RESP_N=$(wc -l < "$RESP_LOG" 2>/dev/null || echo 0)
    ALERT_N=$(wc -l < "$ALERT_LOG" 2>/dev/null || echo 0)

    ISO=$(date '+%Y-%m-%dT%H:%M:%S')
    echo "$ISO,$UPTIME,$LLAMA_RSS,$GPU_USED,$GPU_TEMP,$ROS_RSS,$RESP_N,$ALERT_N" >> "$CSV"

    # 매 10 분마다 1 줄 stdout
    if [ $((UPTIME % 600)) -lt $TICK_S ]; then
        printf "  [%s] up=%ds llama=%dMB gpu=%dMB/%d°C ros=%dMB resp=%d alert=%d\n" \
            "$ISO" "$UPTIME" "$LLAMA_RSS" "$GPU_USED" "$GPU_TEMP" "$ROS_RSS" "$RESP_N" "$ALERT_N"
    fi

    sleep $TICK_S
done

# --- 종료 ---
echo ""
echo "[정리] HRI 종료 + 결과 요약"
kill -INT $HRI_PID $RESP_PID $ALERT_PID 2>/dev/null
sleep 5
kill -9 $HRI_PID $RESP_PID $ALERT_PID 2>/dev/null
wait 2>/dev/null

# 요약
TICKS=$(wc -l < "$CSV")
TICKS=$((TICKS - 1))  # 헤더 제외

LLAMA_PEAK=$(awk -F, 'NR>1 {if($3>m)m=$3} END {print m+0}' "$CSV")
GPU_PEAK=$(awk -F, 'NR>1 {if($4>m)m=$4} END {print m+0}' "$CSV")
GPU_TEMP_MAX=$(awk -F, 'NR>1 {if($5>m)m=$5} END {print m+0}' "$CSV")
ROS_PEAK=$(awk -F, 'NR>1 {if($6>m)m=$6} END {print m+0}' "$CSV")
RESP_TOTAL=$(awk -F, 'END {print $(NF-1)}' "$CSV")
ALERT_TOTAL=$(awk -F, 'END {print $NF}' "$CSV")

# 누수 추정 — 첫 10분 평균 vs 마지막 10분 평균
EARLY=$(awk -F, 'NR>1 && NR<=11 {s+=$3; n++} END {print n>0 ? s/n : 0}' "$CSV")
LATE=$(awk -F, 'NR>1 {a[NR]=$3} END {s=0; n=0; for(i=NR-9;i<=NR;i++){s+=a[i]; n++}; print n>0 ? s/n : 0}' "$CSV")

cat <<SUMMARY > "$RUN_DIR/summary.txt"
Burn-in Summary  (run dir: $RUN_DIR)
───────────────────────────────────────
Duration       : ${DURATION_H} h ($TICKS samples @ ${TICK_S}s)
Peak llama RSS : ${LLAMA_PEAK} MB
Peak GPU VRAM  : ${GPU_PEAK} MB
Peak GPU temp  : ${GPU_TEMP_MAX} °C
Peak ROS RSS   : ${ROS_PEAK} MB
Total responses: ${RESP_TOTAL}
Total alerts   : ${ALERT_TOTAL}
Memory drift   : llama early=${EARLY} MB → late=${LATE} MB

CSV: $CSV
HRI log: $HRI_LOG
SUMMARY

cat "$RUN_DIR/summary.txt"
