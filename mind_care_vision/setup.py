import os
from glob import glob

from setuptools import find_packages, setup

package_name = "mind_care_vision"

setup(
    name=package_name,
    version="0.0.1",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="eslee03",
    maintainer_email="eslee180@naver.com",
    description="마음돌봄 Vision: 독거노인 음성/영상 돌봄 HRI 노드 모음",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "audio_bridge_node = mind_care_vision.audio_bridge_node:main",
            "llm_dialogue_node = mind_care_vision.llm_dialogue_node:main",
            "tts_node = mind_care_vision.tts_node:main",
        ],
    },
)
