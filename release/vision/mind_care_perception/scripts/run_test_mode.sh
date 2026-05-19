#!/usr/bin/env bash
# 막힌 지점 디버그용 — venv python 으로 vision_deepstream_node 를 직접 띄워 stack trace 보기.
# (set -u 는 ROS setup 스크립트의 unbound var 와 충돌해서 사용 안 함)

source ~/마음돌봄/.venv-ros/bin/activate
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash

CFG="$HOME/마음돌봄/release/vision/mind_care_perception/config"
LOG="/tmp/ds_node.log"
: > "$LOG"

export PYTHONFAULTHANDLER=1
export GST_DEBUG=2
python -u -X faulthandler -m mind_care_perception.vision_deepstream_node --ros-args \
  -p source_mode:=test \
  -p pgie_config_file:="$CFG/pgie_yolov8n_face.txt" \
  -p sgie_config_file:="$CFG/sgie_emotion.txt" \
  -p tracker_config_file:="$CFG/tracker_NvDCF.yml" \
  > "$LOG" 2>&1 &
PID=$!

# nvinfer engine 로드까지 ~5s 정도. 여유 두고 8s 대기.
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
echo "=== /vision/state echo (1 msg) ==="
timeout 4 ros2 topic echo --once /vision/state 2>&1 | head -25

echo
echo "=== /vision/state hz over 4s ==="
timeout 5 ros2 topic hz /vision/state 2>&1 | head -10

kill -INT "$PID" 2>/dev/null
sleep 1
kill -9 "$PID" 2>/dev/null
wait 2>/dev/null

echo
echo "=== last 20 lines of node log ==="
tail -20 "$LOG"
