"""perception_dryrun.launch.py — Phase 1 통합 검증용 런치.

vision_emulator_node 만 띄운다. dialogue_node 와 같이 돌리려면
`mind_care_vision/launch/hri_system.launch.py` 와 함께 ros2 launch 두 개를
띄우거나, 사용자가 정의한 통합 launch 에서 본 노드를 include 한다.

사용:
  ros2 launch mind_care_perception perception_dryrun.launch.py
  ros2 launch mind_care_perception perception_dryrun.launch.py scenario:=happy
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    scenario_arg = DeclareLaunchArgument(
        "scenario", default_value="rotating",
        description="rotating | absent | happy | sad | fall | random",
    )
    period_arg = DeclareLaunchArgument(
        "publish_period_s", default_value="2.0",
        description="발행 주기(초)",
    )
    name_arg = DeclareLaunchArgument(
        "registered_name", default_value="철수 어르신",
        description="인식 시 표시 이름",
    )

    emulator = Node(
        package="mind_care_perception",
        executable="vision_emulator_node",
        name="vision_emulator_node",
        output="screen",
        parameters=[{
            "scenario": LaunchConfiguration("scenario"),
            "publish_period_s": LaunchConfiguration("publish_period_s"),
            "registered_name": LaunchConfiguration("registered_name"),
        }],
    )

    return LaunchDescription([scenario_arg, period_arg, name_arg, emulator])
