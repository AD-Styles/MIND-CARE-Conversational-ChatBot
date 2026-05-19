#!/usr/bin/env bash
# Phase 4 — fall_detection_node 를 test 모드로 띄워 /vision/fall_state 발행 확인.

source ~/마음돌봄/.venv-ros/bin/activate
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash

CFG="$HOME/마음돌봄/release/vision/mind_care_perception/config"
LOG="/tmp/fall_node.log"
: > "$LOG"

export PYTHONFAULTHANDLER=1
export GST_DEBUG=2

python -u -X faulthandler -m mind_care_perception.fall_detection_node --ros-args \
  -p source_mode:=test \
  -p pgie_config_file:="$CFG/pgie_yolov8n_pose.txt" \
  -p tracker_config_file:="$CFG/tracker_NvDCF.yml" \
  > "$LOG" 2>&1 &
PID=$!

# nvinfer engine 로드까지 ~3-5s
sleep 8
if kill -0 "$PID" 2>/dev/null; then
  echo "alive"
else
  echo "died"
  echo "=== tail of $LOG ==="
  tail -120 "$LOG"
  exit 1
fi

echo
echo "=== /vision/fall_state echo (1 msg) ==="
timeout 4 ros2 topic echo --once /vision/fall_state 2>&1 | head -25

echo
echo "=== /vision/fall_state hz over 4s ==="
timeout 5 ros2 topic hz /vision/fall_state 2>&1 | head -10

kill -INT "$PID" 2>/dev/null
sleep 1
kill -9 "$PID" 2>/dev/null
wait 2>/dev/null

echo
echo "=== last 25 lines of node log ==="
tail -25 "$LOG"
