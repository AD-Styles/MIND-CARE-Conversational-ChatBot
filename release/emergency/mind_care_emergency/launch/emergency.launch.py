"""emergency.launch.py — Decider + Dispatcher 동시 실행."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    args = [
        DeclareLaunchArgument("elder_id", default_value="elder_01"),
        DeclareLaunchArgument("query_timeout_s", default_value="30.0"),
        DeclareLaunchArgument("cooldown_s", default_value="60.0"),
        DeclareLaunchArgument("long_idle_threshold_s", default_value="21600.0"),
        DeclareLaunchArgument("dispatch_mode", default_value="auto"),
        DeclareLaunchArgument("fcm_credentials_path", default_value=""),
        DeclareLaunchArgument("db_path", default_value=""),
    ]

    decider = Node(
        package="mind_care_emergency",
        executable="emergency_decider_node",
        name="emergency_decider_node",
        output="screen",
        parameters=[{
            "elder_id":              LaunchConfiguration("elder_id"),
            "query_timeout_s":       LaunchConfiguration("query_timeout_s"),
            "cooldown_s":            LaunchConfiguration("cooldown_s"),
            "long_idle_threshold_s": LaunchConfiguration("long_idle_threshold_s"),
        }],
    )
    dispatcher = Node(
        package="mind_care_emergency",
        executable="alert_dispatcher_node",
        name="alert_dispatcher_node",
        output="screen",
        parameters=[{
            "dispatch_mode":         LaunchConfiguration("dispatch_mode"),
            "fcm_credentials_path":  LaunchConfiguration("fcm_credentials_path"),
            "db_path":               LaunchConfiguration("db_path"),
        }],
    )
    return LaunchDescription(args + [decider, dispatcher])
