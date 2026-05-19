#!/usr/bin/env bash
# Phase 5 — Voice 통합 sanity check.
# emergency_decider 띄우고 mock STT 메시지 (panic_word) publish → 즉시 alert 발생 확인.

source ~/마음돌봄/.venv-ros/bin/activate
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash

LOG_DIR=/tmp/voice_sim
mkdir -p "$LOG_DIR"
: > "$LOG_DIR/decider.log"
: > "$LOG_DIR/alert.log"
: > "$LOG_DIR/speech.log"

echo "[1/3] starting emergency_decider …"
python -u -m mind_care_emergency.emergency_decider_node --ros-args \
    -p elder_id:=test_elder \
    > "$LOG_DIR/decider.log" 2>&1 &
DEC_PID=$!
sleep 3

echo "[2/3] recording topics …"
timeout 10 ros2 topic echo /emergency/alert > "$LOG_DIR/alert.log" 2>&1 &
timeout 10 ros2 topic echo /dialogue/proactive_speech > "$LOG_DIR/speech.log" 2>&1 &
sleep 1

echo "[3/3] publish panic_word transcript …"
TS=$(date +%s%N)
ros2 topic pub --once /audio/transcripts std_msgs/String \
  "data: '{\"text\": \"도와줘\", \"timestamp_ns\": $TS}'" 2>&1 | tail -3

sleep 5

kill -INT $DEC_PID 2>/dev/null
sleep 1
kill -9 $DEC_PID 2>/dev/null
wait 2>/dev/null

echo
echo "=== decider 로그 ==="
grep -aE 'NORMAL|QUERY|EMERGENCY|panic|publish' "$LOG_DIR/decider.log" | head -10

echo
echo "=== /emergency/alert ==="
grep -aE '^data:' "$LOG_DIR/alert.log" | head -3

echo
echo "=== alert msgs: $(grep -aco '^data:' $LOG_DIR/alert.log) ==="
echo "=== proactive_speech msgs: $(grep -aco '^data:' $LOG_DIR/speech.log) ==="
