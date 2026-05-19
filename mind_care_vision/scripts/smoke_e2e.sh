#!/usr/bin/env bash
# 마음돌봄 — E2E 스모크 테스트.
#
# 시나리오:
#   1. 일반 대화: 등록된 화자 발화 → LLM 응답 (RAG 포함)
#   2. 미등록 화자 차단: speaker_verified=false 페이로드 → 응답 없음
#   3. 응급 트리거: panic_word ("도와줘") → /emergency/alert 1건 발생
#   4. 능동 발화: /dialogue/proactive_speech → /llm/responses forward
#
# 전제: llama-server 가 8080 에서 떠 있어야 함. 없으면 자동 기동.
#
# 사용:
#   bash scripts/smoke_e2e.sh
#   SKIP_LLAMA=1 bash scripts/smoke_e2e.sh   # llama 자동 기동 안 함

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GRN}✔${NC} $1"; }
warn() { echo -e "${YLW}⚠${NC} $1"; }
err()  { echo -e "${RED}✖${NC} $1"; }

# ROS sourcing 은 unbound var 사용 — set -u 비활성 상태에서 source
source ~/마음돌봄/.venv-ros/bin/activate
ROS_DISTRO_DETECT=$(ls /opt/ros 2>/dev/null | head -1)
source /opt/ros/${ROS_DISTRO_DETECT}/setup.bash
source ~/ros2_ws/install/setup.bash 2>/dev/null

# 이후 단계에서만 strict mode (소싱은 끝남)
set -e

LOG_DIR=/tmp/smoke_e2e
mkdir -p "$LOG_DIR"
: > "$LOG_DIR/dialogue.log"
: > "$LOG_DIR/decider.log"

echo "════════════════════════════════════════════════════"
echo "  E2E 스모크 테스트  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════"

# --- llama-server 체크 ---
if ! curl -sf --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1; then
    if [ "${SKIP_LLAMA:-0}" = "1" ]; then
        err "llama-server 미실행 — SKIP_LLAMA=1 이라 자동 기동 안 함"
        exit 1
    fi
    warn "llama-server 미실행 — 자동 기동 시도"
    NGL=33 bash ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh >"$LOG_DIR/llama.log" 2>&1 &
    for i in 1 2 3 4 5 6 7 8 9 10; do
        sleep 3
        if curl -sf --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1; then
            ok "llama-server 기동 (~$((i*3))s)"
            break
        fi
    done
    curl -sf --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1 \
      || { err "llama-server 기동 실패"; exit 1; }
else
    ok "llama-server 이미 실행 중"
fi

# --- 노드 기동 ---
echo ""
echo "[1/4] llm_dialogue_node 기동 …"
python -u -m mind_care_vision.llm_dialogue_node --ros-args \
    --params-file ~/마음돌봄/mind_care_vision/config/hri_params.xavier.yaml \
    > "$LOG_DIR/dialogue.log" 2>&1 &
DLG_PID=$!

echo "[2/4] emergency_decider_node 기동 …"
python -u -m mind_care_emergency.emergency_decider_node --ros-args \
    -p elder_id:=test_elder \
    > "$LOG_DIR/decider.log" 2>&1 &
DEC_PID=$!

# llm_dialogue 의 RAG init 이 ~30 s 걸림 — `/llm/responses` 토픽 publisher 등장까지 대기
echo "  - llm_dialogue RAG init 대기 (max 60s) …"
for i in $(seq 1 60); do
    if ros2 topic info /llm/responses 2>/dev/null | grep -q "Publisher count: [1-9]"; then
        ok "  /llm/responses publisher ready (~${i}s)"
        break
    fi
    sleep 1
done

# --- 토픽 모니터 ---
# NOTE: Xavier(Foxy + FastRTPS) 환경에서 `ros2 topic echo` CLI 가 멀티프로세스
# discovery 는 되지만 데이터 수신이 안 되는 이슈 — 노드 끼리는 정상 통신. 따라서
# echo 의존 제거, 결과 검증은 dialogue.log / decider.log grep 으로 처리.
echo "[3/4] 노드 로그 직접 검증 모드 (ros2 echo 우회) …"
sleep 1

# ============================================================
# 시나리오 1: 정상 발화 (등록 화자)
# ============================================================
echo ""
echo "  ── 시나리오 1: 정상 발화 (speaker_verified=true) ──"
TS=$(date +%s%N)
ros2 topic pub --once /audio/transcripts std_msgs/String \
  "data: '{\"text\": \"무릎이 자주 아파요\", \"timestamp_ns\": $TS, \"speaker_verified\": true, \"speaker_score\": 0.85}'" \
  >/dev/null 2>&1
echo "    publish: '무릎이 자주 아파요' (verified)"
sleep 6

# ============================================================
# 시나리오 2: 미등록 화자
# ============================================================
echo ""
echo "  ── 시나리오 2: 미등록 화자 (speaker_verified=false) ──"
TS=$(date +%s%N)
ros2 topic pub --once /audio/transcripts std_msgs/String \
  "data: '{\"text\": \"안녕하세요\", \"timestamp_ns\": $TS, \"speaker_verified\": false, \"speaker_score\": 0.42}'" \
  >/dev/null 2>&1
echo "    publish: '안녕하세요' (unverified)"
sleep 3

# ============================================================
# 시나리오 3: 응급 (panic_word)
# ============================================================
echo ""
echo "  ── 시나리오 3: panic_word 응급 ──"
TS=$(date +%s%N)
ros2 topic pub --once /audio/transcripts std_msgs/String \
  "data: '{\"text\": \"도와줘\", \"timestamp_ns\": $TS, \"speaker_verified\": true, \"speaker_score\": 0.9}'" \
  >/dev/null 2>&1
echo "    publish: '도와줘' (panic_word)"
sleep 4

# ============================================================
# 시나리오 4: proactive_speech forward
# ============================================================
echo ""
echo "  ── 시나리오 4: proactive_speech ──"
ros2 topic pub --once /dialogue/proactive_speech std_msgs/String \
  "data: '괜찮으세요? 어디 불편하신 곳 있으신가요?'" \
  >/dev/null 2>&1
echo "    publish: proactive '괜찮으세요?'"
sleep 3

# --- 정리 ---
echo ""
echo "[4/4] 노드 종료 …"
kill -INT $DLG_PID $DEC_PID 2>/dev/null
sleep 2
kill -9 $DLG_PID $DEC_PID 2>/dev/null
wait 2>/dev/null

# ============================================================
# 결과 분석
# ============================================================
echo ""
echo "════════════════ 결과 ════════════════"

# 시나리오 1: dialogue 노드가 '무릎이 자주 아파요' USER 입력에 대해 REPLY 생성
NORMAL_HITS=$(grep -cE "USER='무릎이 자주 아파요'.*-> REPLY=" "$LOG_DIR/dialogue.log" 2>/dev/null || true)
if [ "$NORMAL_HITS" -ge 1 ]; then
    ok "시나리오 1 (정상): LLM 응답 생성 ($NORMAL_HITS 건)"
else
    err "시나리오 1 (정상): 응답 없음"
fi

# 시나리오 2: SV 차단 로그 발생 + '안녕하세요' 에 대한 REPLY 없음
REJECT_HITS=$(grep -cE "USER='안녕하세요'.*-> REPLY=" "$LOG_DIR/dialogue.log" 2>/dev/null || true)
SV_LOG=$(grep -c "미등록 화자" "$LOG_DIR/dialogue.log" 2>/dev/null || true)
if [ "$REJECT_HITS" -eq 0 ] && [ "$SV_LOG" -ge 1 ]; then
    ok "시나리오 2 (차단): SV 차단 로그 $SV_LOG 건, 응답 없음"
else
    err "시나리오 2 (차단): reject_hits=$REJECT_HITS, sv_log=$SV_LOG"
fi

# 시나리오 3: decider 가 NORMAL→EMERGENCY 전이 (panic_word)
ALERT_HITS=$(grep -c "NORMAL→EMERGENCY" "$LOG_DIR/decider.log" 2>/dev/null || true)
if [ "$ALERT_HITS" -ge 1 ]; then
    ok "시나리오 3 (응급): NORMAL→EMERGENCY 전이 $ALERT_HITS 건"
else
    err "시나리오 3 (응급): emergency 전이 없음"
fi

# 시나리오 4: proactive 처리 로그 (turn_id=-1 forward)
PROACT_HITS=$(grep -c "\[proactive\]" "$LOG_DIR/dialogue.log" 2>/dev/null || true)
if [ "$PROACT_HITS" -ge 1 ]; then
    ok "시나리오 4 (proactive): forward $PROACT_HITS 건"
else
    err "시나리오 4 (proactive): forward 누락"
fi

echo ""
echo "로그: $LOG_DIR/{dialogue,decider}.log"
echo "════════════════════════════════════════"
