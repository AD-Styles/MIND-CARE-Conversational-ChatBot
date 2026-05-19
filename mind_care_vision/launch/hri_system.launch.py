"""hri_system.launch.py — 마음돌봄 Vision Phase 1 통합 런치.

사전 조건:
  - llama.cpp 서버가 127.0.0.1:8080에서 실행 중이어야 함
      bash ~/마음돌봄/mind_care_vision/scripts/start_llama_server.sh

구성 노드:
  - audio_bridge_node : 마이크 → VAD → Whisper → /audio/transcripts
  - llm_dialogue_node : /audio/transcripts → llama-server → /llm/responses
  - tts_node          : /llm/responses → TTS → 스피커

파라미터:
  config/hri_params.yaml (기본). `config_file` 인자로 교체 가능.

PYTHONPATH:
  ros2 run/launch가 생성한 entry-point shim은 shebang이 /usr/bin/python3로
  고정되어 venv의 패키지를 찾지 못한다. 따라서 venv site-packages를
  PYTHONPATH로 주입해 시스템 python이 sounddevice/faster-whisper/requests
  등을 찾을 수 있도록 한다.
    override: `export MIND_CARE_VENV=/custom/path/.venv` 가능.
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def _venv_site_packages() -> str:
    """venv-ros의 site-packages 경로를 해석한다."""
    venv_root = os.environ.get(
        "MIND_CARE_VENV",
        os.path.expanduser("~/마음돌봄/.venv-ros"),
    )
    # Python 3.x 자동 감지 (정확한 3.x 디렉터리 탐색)
    lib_dir = os.path.join(venv_root, "lib")
    if os.path.isdir(lib_dir):
        for name in os.listdir(lib_dir):
            if name.startswith("python"):
                sp = os.path.join(lib_dir, name, "site-packages")
                if os.path.isdir(sp):
                    return sp
    # 폴백: Python 3.12 가정
    return os.path.join(venv_root, "lib", "python3.12", "site-packages")


def generate_launch_description():
    pkg_share = FindPackageShare("mind_care_vision")

    default_config = PathJoinSubstitution([pkg_share, "config", "hri_params.yaml"])

    config_arg = DeclareLaunchArgument(
        "config_file",
        default_value=default_config,
        description="ROS 2 params YAML path",
    )

    config = LaunchConfiguration("config_file")

    # venv site-packages를 PYTHONPATH 앞쪽에 주입
    sp = _venv_site_packages()
    existing = os.environ.get("PYTHONPATH", "")
    new_pythonpath = sp + ((":" + existing) if existing else "")
    set_pythonpath = SetEnvironmentVariable("PYTHONPATH", new_pythonpath)

    audio = Node(
        package="mind_care_vision",
        executable="audio_bridge_node",
        name="audio_bridge_node",
        output="screen",
        parameters=[config],
    )

    llm = Node(
        package="mind_care_vision",
        executable="llm_dialogue_node",
        name="llm_dialogue_node",
        output="screen",
        parameters=[config],
    )

    tts = Node(
        package="mind_care_vision",
        executable="tts_node",
        name="tts_node",
        output="screen",
        parameters=[config],
    )

    # --- 응급 파이프라인 (mind_care_emergency / mind_care_api) ---
    # decider  : /audio/transcripts 등 구독 → 응급 판단 → /emergency/alert
    # dispatcher: /emergency/alert 구독 → 부저(GPIO) + 채널 발송
    # api_gw   : /emergency/alert 구독 → 보호자 WebSocket(/api/v1/stream)
    emergency_decider = Node(
        package="mind_care_emergency",
        executable="emergency_decider_node",
        name="emergency_decider_node",
        output="screen",
        parameters=[config],
    )

    alert_dispatcher = Node(
        package="mind_care_emergency",
        executable="alert_dispatcher_node",
        name="alert_dispatcher_node",
        output="screen",
        parameters=[{"dispatch_mode": "auto"}],
    )

    api_gateway = Node(
        package="mind_care_api",
        executable="api_gateway_node",
        name="api_gateway_node",
        output="screen",
        arguments=[
            "--host", "0.0.0.0",
            "--port", "8000",
            "--elder-id", "elder_01",
        ],
    )

    return LaunchDescription([
        set_pythonpath, config_arg,
        audio, llm, tts,
        emergency_decider, alert_dispatcher, api_gateway,
    ])
