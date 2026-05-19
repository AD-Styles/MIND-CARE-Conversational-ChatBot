"""api_gateway.launch.py."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument("host", default_value="0.0.0.0"),
        DeclareLaunchArgument("port", default_value="8000"),
        DeclareLaunchArgument("elder_id", default_value="elder_01"),
        DeclareLaunchArgument("db_path", default_value=""),
        DeclareLaunchArgument("dev_open", default_value="false"),
    ]
    node = Node(
        package="mind_care_api",
        executable="api_gateway_node",
        name="api_gateway_node",
        output="screen",
        # uvicorn 인자는 ROS 파라미터가 아니라 CLI flag 로 — argparse 가 처리
        arguments=[
            "--host", LaunchConfiguration("host"),
            "--port", LaunchConfiguration("port"),
            "--elder-id", LaunchConfiguration("elder_id"),
            "--db-path",  LaunchConfiguration("db_path"),
        ],
    )
    return LaunchDescription(args + [node])
