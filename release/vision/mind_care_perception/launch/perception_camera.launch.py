"""perception_camera.launch.py — Phase 2 실 비전 런치.

DeepStream 파이프라인을 띄워 /vision/state 토픽으로 결과를 발행한다.

사용 예
  # 1) 모델/카메라 없이 노드 구동만 검증 (test 패턴)
  ros2 launch mind_care_perception perception_camera.launch.py source_mode:=test

  # 2) USB 웹캠 직결 (usbipd 등으로 /dev/video0 가용 시)
  ros2 launch mind_care_perception perception_camera.launch.py \
      source_mode:=v4l2 v4l2_device:=/dev/video0

  # 3) 영상 파일 회귀
  ros2 launch mind_care_perception perception_camera.launch.py \
      source_mode:=file file_uri:=file:///home/me/sample.mp4

  # 4) ROS image 토픽 입력
  ros2 launch mind_care_perception perception_camera.launch.py \
      source_mode:=ros image_topic:=/usb_cam/image_raw
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument("source_mode", default_value="test",
                              description="test | v4l2 | file | ros"),
        DeclareLaunchArgument("image_topic", default_value="/camera/image_raw",
                              description="(source_mode=ros) 입력 sensor_msgs/Image"),
        DeclareLaunchArgument("v4l2_device", default_value="/dev/video0"),
        DeclareLaunchArgument("file_uri", default_value=""),
        DeclareLaunchArgument("width", default_value="1280"),
        DeclareLaunchArgument("height", default_value="720"),
        DeclareLaunchArgument("fps", default_value="30"),
        DeclareLaunchArgument("publish_topic", default_value="/vision/state"),
        DeclareLaunchArgument("publish_period_s", default_value="0.5"),
        DeclareLaunchArgument("registered_name", default_value="철수 어르신"),
        DeclareLaunchArgument("min_emotion_conf", default_value="0.3"),
        DeclareLaunchArgument("stale_window_s", default_value="1.0"),
        DeclareLaunchArgument("pgie_config_file", default_value="",
                              description="비워두면 패키지 share/config 기본값 사용"),
        DeclareLaunchArgument("sgie_config_file", default_value=""),
        DeclareLaunchArgument("tracker_config_file", default_value=""),
    ]

    node = Node(
        package="mind_care_perception",
        executable="vision_deepstream_node",
        name="vision_deepstream_node",
        output="screen",
        parameters=[{
            "source_mode":         LaunchConfiguration("source_mode"),
            "image_topic":         LaunchConfiguration("image_topic"),
            "v4l2_device":         LaunchConfiguration("v4l2_device"),
            "file_uri":            LaunchConfiguration("file_uri"),
            "width":               LaunchConfiguration("width"),
            "height":              LaunchConfiguration("height"),
            "fps":                 LaunchConfiguration("fps"),
            "publish_topic":       LaunchConfiguration("publish_topic"),
            "publish_period_s":    LaunchConfiguration("publish_period_s"),
            "registered_name":     LaunchConfiguration("registered_name"),
            "min_emotion_conf":    LaunchConfiguration("min_emotion_conf"),
            "stale_window_s":      LaunchConfiguration("stale_window_s"),
            "pgie_config_file":    LaunchConfiguration("pgie_config_file"),
            "sgie_config_file":    LaunchConfiguration("sgie_config_file"),
            "tracker_config_file": LaunchConfiguration("tracker_config_file"),
        }],
    )

    return LaunchDescription(args + [node])
