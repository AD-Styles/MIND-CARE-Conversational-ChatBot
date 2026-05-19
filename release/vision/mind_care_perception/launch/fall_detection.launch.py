"""fall_detection.launch.py — Phase 4 낙상 감지 노드 런치.

사용 예
  # test 모드 (videotestsrc — 사람 0명 → fall_detected=false)
  ros2 launch mind_care_perception fall_detection.launch.py source_mode:=test

  # v4l2 웹캠
  ros2 launch mind_care_perception fall_detection.launch.py source_mode:=v4l2

  # 영상 파일 회귀 (Le2i 등)
  ros2 launch mind_care_perception fall_detection.launch.py \
      source_mode:=file file_uri:=file:///home/me/fall_sample.mp4
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument("source_mode", default_value="test"),
        DeclareLaunchArgument("image_topic", default_value="/camera/image_raw"),
        DeclareLaunchArgument("v4l2_device", default_value="/dev/video0"),
        DeclareLaunchArgument("file_uri", default_value=""),
        DeclareLaunchArgument("width", default_value="1280"),
        DeclareLaunchArgument("height", default_value="720"),
        DeclareLaunchArgument("fps", default_value="30"),
        DeclareLaunchArgument("publish_topic", default_value="/vision/fall_state"),
        DeclareLaunchArgument("publish_period_s", default_value="0.2"),
        DeclareLaunchArgument("pgie_config_file", default_value=""),
        DeclareLaunchArgument("tracker_config_file", default_value=""),
        DeclareLaunchArgument("tilt_deg_thr", default_value="60.0"),
        DeclareLaunchArgument("compression_thr", default_value="0.30"),
        DeclareLaunchArgument("aspect_thr", default_value="1.4"),
        DeclareLaunchArgument("window_s", default_value="0.2"),
        DeclareLaunchArgument("ratio_thr", default_value="0.33"),
        DeclareLaunchArgument("confirm_idle_s", default_value="5.0"),
    ]

    node = Node(
        package="mind_care_perception",
        executable="fall_detection_node",
        name="fall_detection_node",
        output="screen",
        parameters=[{
            k: LaunchConfiguration(k) for k in [
                "source_mode", "image_topic", "v4l2_device", "file_uri",
                "width", "height", "fps", "publish_topic", "publish_period_s",
                "pgie_config_file", "tracker_config_file",
                "tilt_deg_thr", "compression_thr", "aspect_thr",
                "window_s", "ratio_thr", "confirm_idle_s",
            ]
        }],
    )
    return LaunchDescription(args + [node])
