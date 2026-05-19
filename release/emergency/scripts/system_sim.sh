#!/usr/bin/env bash
# Phase 5 시스템 시뮬레이션 — 한 영상으로 fall + decider + dispatcher + topic echo.
#
# 사용:
#   bash system_sim.sh /path/to/fall-01.mp4 [duration_s]

VIDEO="${1:-/home/eslee03/eval/urfdd/videos/fall-01.mp4}"
DURATION="${2:-15}"

source ~/마음돌봄/.venv-ros/bin/activate
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash

CFG="$HOME/마음돌봄/release/vision/mind_care_perception/config"
LOG_DIR=/tmp/sys_sim
mkdir -p "$LOG_DIR"
: > "$LOG_DIR/fall.log"
: > "$LOG_DIR/decider.log"
: > "$LOG_DIR/dispatcher.log"
: > "$LOG_DIR/alert.log"
: > "$LOG_DIR/delivery.log"
: > "$LOG_DIR/speech.log"

echo "[1/4] starting fall_detection_node on $VIDEO …"
python -u -m mind_care_perception.fall_detection_node --ros-args \
    -p source_mode:=file \
    -p file_uri:="file://$VIDEO" \
    -p pgie_config_file:="$CFG/pgie_yolov8n_pose.txt" \
    -p tracker_config_file:="$CFG/tracker_NvDCF.yml" \
    > "$LOG_DIR/fall.log" 2>&1 &
FALL_PID=$!
sleep 3

echo "[2/4] starting emergency_decider_node …"
python -u -m mind_care_emergency.emergency_decider_node --ros-args \
    -p elder_id:=test_elder -p query_timeout_s:=15.0 \
    > "$LOG_DIR/decider.log" 2>&1 &
DEC_PID=$!
sleep 1

echo "[3/4] starting alert_dispatcher_node (mock mode) …"
python -u -m mind_care_emergency.alert_dispatcher_node --ros-args \
    -p dispatch_mode:=mock \
    -p db_path:=/tmp/sys_sim/mindcare.db \
    > "$LOG_DIR/dispatcher.log" 2>&1 &
DISP_PID=$!
sleep 1

echo "[4/4] recording topics …"
timeout "$DURATION" ros2 topic echo /emergency/alert            > "$LOG_DIR/alert.log"    2>&1 &
timeout "$DURATION" ros2 topic echo /emergency/delivery         > "$LOG_DIR/delivery.log" 2>&1 &
timeout "$DURATION" ros2 topic echo /dialogue/proactive_speech  > "$LOG_DIR/speech.log"   2>&1 &

sleep "$DURATION"

# 종료
for pid in $FALL_PID $DEC_PID $DISP_PID; do
    kill -INT "$pid" 2>/dev/null
done
sleep 2
for pid in $FALL_PID $DEC_PID $DISP_PID; do
    kill -9 "$pid" 2>/dev/null
done
wait 2>/dev/null

echo
echo "=== /dialogue/proactive_speech ==="
grep -aE '^data:' "$LOG_DIR/speech.log" | head -3
echo "  msgs: $(grep -aco '^data:' $LOG_DIR/speech.log)"
echo
echo "=== /emergency/alert ==="
grep -aE '^data:' "$LOG_DIR/alert.log" | head -3
echo "  alert msgs: $(grep -aco '^data:' $LOG_DIR/alert.log)"
echo
echo "=== /emergency/delivery ==="
grep -aE '^data:' "$LOG_DIR/delivery.log" | head -5
echo "  delivery msgs: $(grep -aco '^data:' $LOG_DIR/delivery.log)"
echo
echo "=== decider 로그 ==="
grep -aE 'NORMAL|QUERY|EMERGENCY|ACKED|publish_alert' "$LOG_DIR/decider.log" | head -10
echo
echo "=== dispatcher 로그 ==="
grep -aE 'channel|alert|MOCK' "$LOG_DIR/dispatcher.log" | head -10
